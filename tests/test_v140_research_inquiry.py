"""v1.40: question-driven research agents must not promote weak evidence."""
from __future__ import annotations

import hashlib
from pathlib import Path
from threading import Thread

import pytest

from paper_radar.core import connect,save_papers
from paper_radar.models import Paper
from paper_radar.evidence_chain import local_chain,portal_chain
from paper_radar.research_intelligence import (
    run_local,run_portal,read_local,read_portal,list_local,list_portal,_model_reorder,to_markdown)
from paper_radar.migrations import REGISTRIES, migrate,status
from paper_radar.public_portal import Portal,make_server
from paper_radar.portal_auth import db as auth_db,create_account
from paper_radar.portal_research import save_pdf
from tests.test_v136_portal_following import Session
from tests.test_v118_public_pipeline import pdf_bytes,QUOTE,paper


def _create_local(tmp_path):
    from paper_radar.downloads.manager import download_root
    from paper_radar.fulltext import init_schema
    path=tmp_path/'store'/'papers.sqlite3';path.parent.mkdir()
    directory=download_root(path);directory.mkdir(parents=True)
    file=directory/'study.pdf';file.write_bytes(pdf_bytes())
    paper_id='local:study';sha=hashlib.sha256(file.read_bytes()).hexdigest()
    row=Paper(paper_id=paper_id,title='Neural operator benchmark',abstract='A neural operator benchmark on DatasetA',
        authors='Researcher',published='2026-11-03',updated='2026-11-03',category='cs.LG',
        url='https://example.org/study',pdf_url='',doi='10.0000/example')
    with connect(path) as db:
        save_papers(db,[row]);init_schema(db)
        db.execute('UPDATE papers SET local_file=? WHERE paper_id=?',('study.pdf',paper_id))
        db.execute('''INSERT INTO fulltext_documents
            (paper_id,pdf_sha256,file_name,parser_version,total_pages,indexed_pages,chunk_count,state)
            VALUES (?,?,'study.pdf','v1',1,1,1,'indexed')''',(paper_id,sha))
        db.execute('INSERT INTO fulltext_chunks(paper_id,page,section,content) VALUES (?,?,?,?)',
                   (paper_id,1,'experimental',QUOTE))
    return path,file,paper_id


def test_empty_workspace_reports_missing_evidence_not_invented(tmp_path):
    path=tmp_path/'empty.sqlite3'
    with connect(path):pass
    result=run_local(path,'default','神经算子在不同几何下的外推精度如何？')
    brief=result['brief']
    assert brief['findings']==[] and brief['metadata_candidates']==[]
    assert brief['research_questions'] and brief['coverage']['papers_scanned']==0
    assert brief['agent_trace']['state']=='success'
    assert len(brief['agent_trace']['steps'])==5
    assert '暂无与本问题匹配' in read_local(path,'default',result['id'])['markdown']
    assert len(list_local(path,'default')['reports'])==1


def test_confirmed_pdf_claim_cited_verbatim_and_revalidated(tmp_path):
    path,file,pid=_create_local(tmp_path)
    anchored=local_chain('default','add_anchor',pid,1,QUOTE,db_path=path)
    body='Neural operator benchmark reports Accuracy 91% under split1.'
    draft=local_chain('default','save_claim',body,'finding',[anchored['id']],0,db_path=path)
    preview=run_local(path,'default','Neural operator benchmark accuracy comparisons')
    assert preview['brief']['findings']==[]  # draft cannot be promoted
    local_chain('default','confirm',draft['id'],1,'I checked the exact source PDF',db_path=path)
    result=run_local(path,'default','Neural operator benchmark accuracy comparisons')
    brief=result['brief']
    assert len(brief['findings'])==1 and brief['findings'][0]['body']==body
    assert brief['findings'][0]['sources'][0]['quote']==QUOTE
    assert result['digest']
    assert read_local(path,'default',result['id'])['snapshot_status']=='current'
    with connect(path) as db:
        with pytest.raises(Exception,match='immutable'):
            db.execute('UPDATE research_inquiries SET question=? WHERE id=?',('tampered',result['id']))
    file.write_bytes(pdf_bytes('Completely different results after the PDF change'))
    assert read_local(path,'default',result['id'])['snapshot_status']=='needs_refresh'
    # Previous immutable snapshot remains, but must be labeled stale.
    assert '依据已变化' not in read_local(path,'default',result['id'])['markdown']  # state uses machine label
    assert 'needs_refresh' in read_local(path,'default',result['id'])['markdown']
    new=run_local(path,'default','Neural operator benchmark accuracy comparisons')
    assert new['brief']['findings']==[]


def test_metadata_never_promoted_to_conclusion_and_topic_isolation(tmp_path):
    path,file,pid=_create_local(tmp_path)
    r=run_local(path,'default','Neural operator benchmark accuracy comparisons')
    assert r['brief']['metadata_candidates'][0]['status']=='metadata_only_not_verified'
    assert r['brief']['findings']==[]
    with pytest.raises(ValueError):
        read_local(path,'other',r['id'])
    with pytest.raises(ValueError):
        run_local(path,'default','a')


def test_cloud_consent_unknown_ids_and_safe_rank(tmp_path):
    path,file,pid=_create_local(tmp_path)
    first=local_chain('default','add_anchor',pid,1,QUOTE,db_path=path)
    draft=local_chain('default','save_claim','Neural operator benchmark accuracy is 91% on DatasetA.',
                            'finding',[first['id']],0,db_path=path)
    local_chain('default','confirm',draft['id'],1,'I checked the entire source PDF',db_path=path)
    with pytest.raises(ValueError,match='确认'):
        run_local(path,'default','Neural operator benchmark accuracy comparisons',use_model=True)
    calls=[]
    def good(payload):
        calls.append(payload)
        return {'ordered_claim_ids':[item['id'] for item in payload['claims']]}
    r=run_local(path,'default','Neural operator benchmark accuracy comparisons',
                use_model=True,confirm_transfer=True,model_callable=good)
    assert r['brief']['model']['status']=='reordered_existing_claims'
    assert len(calls)==1 and 'quote' not in str(calls)
    r=run_local(path,'default','Neural operator benchmark accuracy comparisons',
                use_model=True,confirm_transfer=True,
                model_callable=lambda _: {'ordered_claim_ids':['fake']})
    assert r['brief']['model']['status']=='model_failed_offline_order_used'
    assert r['brief']['findings'][0]['claim_id']==draft['id']


def test_portal_isolation_and_api_security(tmp_path):
    p=Portal(tmp_path/'portal',job_workers=0,embedded_workers=False)
    p.fetcher=lambda cfg,logger:[paper('2601.12345')]
    server=make_server('127.0.0.1',0,p)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    a,b=Session(server.server_port),Session(server.server_port)
    try:
        assert a.post('/api/public/register',{'username':'InquiryAlpha','password':'secure password alpha 123'})[0]==201
        assert b.post('/api/public/register',{'username':'InquiryBeta','password':'secure password beta 123'})[0]==201
        p.search(a.get('/api/public/me')[1]['user']['id'],'default')
        code,result=a.post('/api/public/inquiry/run',{'topic_id':'default','question':'Neural operator benchmark accuracy'})
        assert code==201,result
        rid=result['id']
        assert a.get('/api/public/inquiry?topic_id=default')[1]['reports']
        assert a.get('/api/public/inquiry/view?topic_id=default&id='+rid)[0]==200
        assert b.get('/api/public/inquiry/view?topic_id=default&id='+rid)[0]==400
        assert b.get('/api/public/inquiry?topic_id=default')[1]['reports']==[]
        assert a.post('/api/public/inquiry/run',{'topic_id':'default','question':'Neural operator benchmark accuracy'},
                      valid_csrf=False)[0]==403
        assert a.get('/inquiry')[0]==200
        assert a.get('/inquiry_workbench.js')[0]==200
    finally:
        server.shutdown();server.server_close();thread.join(timeout=6)


def test_migration_is_idempotent_and_registries_separate(tmp_path):
    from paper_radar.migrations import migrate
    path=tmp_path/'m.sqlite3'
    with connect(path) as db:
        assert status(db,'research')['version']==len(REGISTRIES['research'])
        assert migrate(db,'research')==len(REGISTRIES['research'])
    p=Portal(tmp_path/'portal',job_workers=0,embedded_workers=False)
    uid='0'*32
    with p.workspace(uid) as db:
        assert status(db,'portal_workspace')['version']==len(REGISTRIES['portal_workspace'])
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_research_brief_discloses_pending_retrieval_sources(tmp_path):
    """A timed-out monitored source must prevent a 'complete coverage' story."""
    from datetime import datetime, timezone
    from paper_radar.core import DEFAULTS,validate_config
    from paper_radar.following import plan_windows,record_cycle
    path,_,pid = _create_local(tmp_path)
    when=datetime(2026,11,8,11,tzinfo=timezone.utc)
    with connect(path) as db:
        cfg=validate_config(dict(DEFAULTS,keywords=['neural operator'],sources=['arxiv']))
        cfg['_following_windows']=plan_windows(db,cfg,when)
        record_cycle(db,cfg,[],[],[{'name':'arxiv','ok':False,'count':0,
            'partial':False,'error_code':'SOURCE_TIMEOUT','queries':[
                {'keyword':'neural operator','ok':False,'count':0,
                 'error_code':'SOURCE_TIMEOUT'}]}],now=when)
    result=run_local(path,'default','Neural operator accuracy and extrapolation')
    brief=result['brief']
    assert brief['coverage']['source_tracking']['pending_sources']==1
    assert any(g['id']=='source_incomplete' for g in brief['gaps'])
    assert '需补检' in to_markdown(brief)
    assert '需补检' in read_local(path,'default',result['id'])['markdown']
