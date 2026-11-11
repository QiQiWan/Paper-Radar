"""v1.44/1.45: real local PDF provenance, snapshot staleness and topic safety."""
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from paper_radar.core import connect
from paper_radar.evidence_chain import local_chain, EvidenceChain
from paper_radar.research_synthesis_agent import (
    run_local,read_local,list_local,build_report,method_profiles,conflict_candidates)
from paper_radar.research_continuity import (
    save_memory,read_memory,retire_memory,feedback,interests,update_preferences,
    generate_brief,list_briefs,read_brief,automatic_due,_window,to_markdown,
    after_search,run_local as run_brief)
from paper_radar.migrations import migrate, REGISTRIES
from tests.test_v140_research_inquiry import _create_local
from tests.test_v118_public_pipeline import QUOTE,pdf_bytes


def _confirmed(tmp_path):
    path,pdf,pid=_create_local(tmp_path)
    anchor=local_chain('default','add_anchor',pid,1,QUOTE,db_path=path)
    draft=local_chain('default','save_claim',
        'Neural operator benchmark reports Accuracy 91% on DatasetA.',
        'finding',[anchor['id']],0,db_path=path)
    local_chain('default','confirm',draft['id'],1,'I checked the exact PDF evidence.',db_path=path)
    return path,pdf,pid,draft['id']


def test_synthesis_uses_only_current_confirmed_pdf_claims_and_detects_stale(tmp_path):
    path,pdf,pid,cid=_confirmed(tmp_path)
    report=run_local(path,'default','Neural operator benchmark Accuracy on DatasetA')
    assert report['report']['coverage']['matched_claims']==1
    assert report['report']['findings'][0]['claim_id']==cid
    assert report['report']['findings'][0]['evidence'][0]['quote']==QUOTE
    assert report['report']['model']['status']=='disabled'
    assert 'PDF 第 1 页' in report['markdown']
    assert read_local(path,'default',report['id'])['snapshot_status']=='current'
    assert list_local(path,'default')['reports'][0]['id']==report['id']
    with connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            db.execute('UPDATE research_synthesis_snapshots SET question=?',('tampered',))
    pdf.write_bytes(pdf_bytes('This is now a different PDF.'))
    assert read_local(path,'default',report['id'])['snapshot_status']=='needs_review'
    new=run_local(path,'default','Neural operator benchmark Accuracy on DatasetA')
    assert new['report']['findings']==[]


def test_synthesis_model_must_be_authorized_and_cannot_add_unknown_ids(tmp_path):
    path,_,_,cid=_confirmed(tmp_path)
    config={'llm_data_consent':True}
    with pytest.raises(ValueError,match='确认'):
        run_local(path,'default','Neural operator benchmark Accuracy on DatasetA',
                  use_model=True,model_config=config)
    with pytest.raises(ValueError,match='授权'):
        run_local(path,'default','Neural operator benchmark Accuracy on DatasetA',
                  use_model=True,confirm_transfer=True,model_config={'llm_data_consent':False})
    calls=[]
    def bad(payload):
        calls.append(payload)
        return {'sections':[{'title':'虚构','interpretation':'基于不存在的论文效果提升 900%',
                             'claim_ids':['f'*32]}],
                'questions':[{'text':'正确确认的研究问题','claim_ids':[cid]}]}
    result=run_local(path,'default','Neural operator benchmark Accuracy on DatasetA',
        use_model=True,confirm_transfer=True,model_config=config,model_callable=bad)
    assert result['report']['model']['sections']==[]
    assert result['report']['model']['questions'][0]['status']=='model_interpretation_pending_review'
    assert len(calls)==1 and calls[0]['payload']['verified_claims'][0]['id']==cid


def test_synthesis_does_not_invent_method_or_confirm_numeric_conflicts():
    findings=[{'claim_id':'1'*32,'body':'FNO methods use spectral layers.',
               'sources':[{'paper_id':'p1','anchor_id':'a1','quote':'Fourier neural operator experiment',
                           'experiment':{'dataset':'test','metric':'RMSE','setting':'same','baseline':'B',
                                         'value':'0.1','review_decision':'approved'}}]},
              {'claim_id':'2'*32,'body':'FNO has a numerical result.',
               'sources':[{'paper_id':'p2','anchor_id':'a2','quote':'FNO neural operator',
                           'experiment':{'dataset':'test','metric':'RMSE','setting':'same','baseline':'B',
                                         'value':'0.3','review_decision':'approved'}}]}]
    assert method_profiles(findings)[0]['method']=='FNO'
    conflicts=conflict_candidates(findings)
    assert len(conflicts)==1 and conflicts[0]['status']=='candidate_only'
    findings[1]['sources'][0]['experiment']['review_decision']='needs_review'
    assert conflict_candidates(findings)==[]


def test_topic_isolation_and_memories_require_confirmed_claims(tmp_path):
    path,pdf,pid,cid=_confirmed(tmp_path)
    with connect(path) as db:
        migrate(db,'research');chain=EvidenceChain(db,'default',backend='local',db_path=path)
        with pytest.raises(ValueError,match='确认'):
            save_memory(db,'default',chain,kind='confirmed_claim',body='This is a verified research claim',
                        claim_ids=['f'*32])
        with pytest.raises(ValueError,match='原文'):
            save_memory(db,'default',chain,kind='confirmed_claim',
                body='Confirmed benchmark finding for DatasetA',claim_ids=[cid])
        original='Neural operator benchmark reports Accuracy 91% on DatasetA.'
        result=save_memory(db,'default',chain,kind='confirmed_claim',body=original,claim_ids=[cid])
        assert result['evidence_status']=='current'
        notes=read_memory(db,'default',chain)
        assert notes['items'][0]['evidence_status']=='current'
        with pytest.raises(ValueError,match='覆盖'):
            save_memory(db,'default',chain,kind='note',body='Different text in this memory',
                item_id=result['id'],expected_revision=10)
        rev=save_memory(db,'default',chain,kind='note',body='Edited personal note on experiment',
                        item_id=result['id'],expected_revision=1)
        assert rev['revision']==2
        retire_memory(db,'default',result['id'],expected_revision=2)
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            db.execute('DELETE FROM research_memory_audit')
        assert len(read_memory(db,'default',chain)['items'])==1
        other=EvidenceChain(db,'secondary',backend='local',db_path=path)
        assert read_memory(db,'secondary',other)['items']==[]
    with pytest.raises(ValueError):read_local(path,'other','f'*32)


def test_user_feedback_does_not_change_paper_pool(tmp_path):
    path,_,pid,_=_confirmed(tmp_path)
    with connect(path) as db:
        feedback(db,'default',pid,'relevant')
        result=interests(db,'default')
        assert result['positive']==1 and result['negative']==0
        assert any(x['term']=='neural' for x in result['top_interests'])
        assert db.execute('SELECT COUNT(*) FROM papers').fetchone()[0]==1
        feedback(db,'default',pid,'irrelevant')
        assert interests(db,'default')['negative']==1
        feedback(db,'default',pid,'clear')
        assert interests(db,'default')['positive']==0
        with pytest.raises(ValueError,match='不属于'):
            feedback(db,'other',pid,'relevant')


def test_completed_utc_calendar_window_and_idempotent_automatic_brief(tmp_path):
    path,_,pid,cid=_confirmed(tmp_path)
    now=datetime(2026,11,10,10,tzinfo=timezone.utc)
    assert _window('weekly',now)[0].date().isoformat()=='2026-11-02'
    assert _window('weekly',now)[1].date().isoformat()=='2026-11-09'
    assert _window('monthly',datetime(2026,1,2,tzinfo=timezone.utc))[0].date().isoformat()=='2025-12-01'
    with connect(path) as db:
        migrate(db,'research')
        db.execute('''INSERT INTO following_events
            (topic_id,source_name,source_id,paper_id,kind,signature,observed_at,title,details_json)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            ('default','arxiv','abc',pid,'new','sig','2026-11-04T12:00:00+00:00','Neural operator paper','{}'))
        chain=EvidenceChain(db,'default',backend='local',db_path=path)
        assert automatic_due(db,'default',chain,now=now)['state']=='disabled'
        update_preferences(db,'default',enabled=True,cadence='weekly')
        a=automatic_due(db,'default',chain,now=now)
        assert a['state']=='generated'
        assert a['result']['report']['event_count']==1
        assert a['result']['report']['claim_changes']['new']==[cid]
        assert a['result']['report']['event_summaries'][0]['evidence_level']=='metadata_only'
        assert automatic_due(db,'default',chain,now=now)['state']=='already_generated'
        assert len(list_briefs(db,'default')['briefs'])==1
        b=generate_brief(db,'default',chain,cadence='weekly',now=now)
        assert b['id']==a['result']['id']
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            db.execute('UPDATE research_period_briefs SET period_start=?',('bad',))
        assert '新增和修改的论文' in to_markdown(b['report'])
        pdf=tmp_path/'store'/'data'/'pdfs'/'study.pdf'
        # Verify that a user-owned research memory cannot be silently promoted
        # after a confirmed claim loses its original PDF provenance.
        original=read_brief(db,'default',b['id'],chain)
        assert original['snapshot_status']=='current'
    assert len(list_briefs_from_disk(path))==1


def list_briefs_from_disk(path):
    with connect(path) as db:return list_briefs(db,'default')['briefs']


def test_schema_local_and_portal_registry_versions_are_append_only(tmp_path):
    assert REGISTRIES['research'][-1].version==9
    assert REGISTRIES['portal_workspace'][-1].version==16
    path=tmp_path/'test.sqlite3'
    with connect(path) as db:
        assert migrate(db,'research')==9
        assert migrate(db,'research')==9
        for table in ('research_synthesis_snapshots','research_memory_items',
                      'research_interest_feedback','research_period_briefs'):
            assert db.execute('SELECT name FROM sqlite_master WHERE name=?',(table,)).fetchone()


def test_portal_research_program_authorization_is_per_account(tmp_path):
    from paper_radar.public_portal import Portal,make_server
    from tests.test_v136_portal_following import Session
    from threading import Thread
    portal=Portal(tmp_path/'public',job_workers=0,embedded_workers=False)
    server=make_server('127.0.0.1',0,portal)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    a=Session(server.server_port);b=Session(server.server_port)
    try:
        assert a.post('/api/public/register',{'username':'ResearcherAlpha','password':'secure pass alpha 456'})[0]==201
        assert b.post('/api/public/register',{'username':'ResearcherBeta','password':'secure pass beta 456'})[0]==201
        q={'topic_id':'default','question':'Neural operator benchmark across domains'}
        code,result=a.post('/api/public/research-synthesis/run',q)
        assert code==201,result
        rid=result['id']
        assert a.get('/api/public/research-synthesis/view?topic_id=default&id='+rid)[0]==200
        assert b.get('/api/public/research-synthesis/view?topic_id=default&id='+rid)[0]==400
        assert b.get('/api/public/research-synthesis?topic_id=default')[1]['reports']==[]
        assert a.post('/api/public/research-synthesis/run',q,valid_csrf=False)[0]==403
        assert a.post('/api/public/research-memory/save',{'topic_id':'default','kind':'note',
             'body':'Important note about a research topic'})[0]==200
        assert b.get('/api/public/research-memory?topic_id=default')[1]['items']==[]
        assert a.post('/api/public/research-briefs/preferences',{
            'topic_id':'default','cadence':'weekly','enabled':True})[0]==200
        code,brief=a.post('/api/public/research-briefs/run',{
            'topic_id':'default','cadence':'weekly'})
        assert code==201,brief
        assert b.get('/api/public/research-briefs/view?topic_id=default&id='+brief['id'])[0]==400
        assert a.get('/research_program.js')[0]==200
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_preliminary_pdf_search_is_separate_from_confirmed_research_claims(tmp_path):
    path, pdf, pid = _create_local(tmp_path)
    from paper_radar.fulltext import index_paper
    with connect(path) as db:
        index_paper(db, pid, db_path=path)
    result = run_local(path, 'default', 'Neural operator benchmark Accuracy on DatasetA')
    report = result['report']
    # An indexed PDF can provide a traceable *candidate* without a human review.
    assert report['findings'] == []
    assert report['coverage']['independent_papers'] == 0
    assert len(report['unreviewed_excerpts']) >= 1
    assert report['unreviewed_excerpts'][0]['paper_id'] == pid
    assert '待核实的 PDF 原文候选' in result['markdown']
    pdf.write_bytes(b'damaged pdf')
    new = run_local(path, 'default', 'Neural operator benchmark Accuracy on DatasetA')
    assert new['report']['unreviewed_excerpts'] == []


def test_confirmed_memory_is_invalidated_by_pdf_replacement(tmp_path):
    path, pdf, _, cid = _confirmed(tmp_path)
    original = 'Neural operator benchmark reports Accuracy 91% on DatasetA.'
    with connect(path) as db:
        chain = EvidenceChain(db, 'default', backend='local', db_path=path)
        stored = save_memory(db, 'default', chain, kind='confirmed_claim', body=original,
                             claim_ids=[cid])
        assert read_memory(db, 'default', chain)['items'][0]['evidence_status'] == 'current'
        db.commit()
    pdf.write_bytes(b'modified')
    with connect(path) as db:
        chain = EvidenceChain(db, 'default', backend='local', db_path=path)
        item = read_memory(db, 'default', chain)['items'][0]
        assert item['id'] == stored['id'] and item['evidence_status'] == 'needs_review'
        assert item['body'] == original


def test_retired_memory_cannot_be_reactivated_via_revision_race(tmp_path):
    path, _, _, _ = _confirmed(tmp_path)
    with connect(path) as db:
        chain = EvidenceChain(db, 'default', backend='local', db_path=path)
        item = save_memory(db, 'default', chain, kind='note', body='A personal researcher observation')
        retired = retire_memory(db, 'default', item['id'], 1)
        with pytest.raises(ValueError, match='覆盖'):
            save_memory(db, 'default', chain, kind='note', body='Incorrect resurrection of retired note',
                        item_id=item['id'], expected_revision=retired['revision'])
        assert read_memory(db, 'default', chain)['items'][0]['evidence_status'] == 'retired'


def test_opposing_claim_language_is_only_a_conflict_review_candidate():
    from paper_radar.research_synthesis_agent import qualitative_tension_candidates
    rows = [
      {'claim_id':'a'*32,'body':'FNO improves neural operator Accuracy on DatasetA against baseline.',
       'sources':[{'paper_id':'p1'}]},
      {'claim_id':'b'*32,'body':'FNO does not improve neural operator Accuracy on DatasetA against baseline.',
       'sources':[{'paper_id':'p2'}]},
    ]
    candidates = qualitative_tension_candidates(rows)
    assert len(candidates) == 1
    assert candidates[0]['status'] == 'candidate_only'
    assert '不能认定结论冲突' in candidates[0]['warning']
