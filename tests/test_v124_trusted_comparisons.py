"""v1.25.0 evidence review, scope, stale SHA and research-claim safety."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from threading import Thread

import pytest
from paper_radar.comparison_policy import (pair_assessment, validate_conditions)
from paper_radar.migrations import REGISTRIES,migrate
from paper_radar.portal_auth import db as auth_db,create_account
from paper_radar.portal_research import records,save_pdf,analyze,synthesis,preview
from paper_radar.portal_reviews import review_evidence,review_pair
from paper_radar.public_portal import Portal,make_server
from tests.test_v118_public_pipeline import pdf_bytes, paper, QUOTE
from test_v116_portal_and_synthesis import Client, register

CONDITIONS={'dataset_version':'dataset release 1','split':'test split 1',
 'unit':'percent','metric_direction':'higher','aggregation':'mean of 3 runs',
 'training_budget':'identical 50 epochs','evaluation_protocol':'published test protocol'}


def setup(tmp_path):
    p=Portal(tmp_path/'data',embedded_workers=False)
    p.fetcher=lambda cfg,logger:[paper('2601.12345'),paper('2601.12346')]
    p.model_callable=lambda rows:json.dumps({'findings':[{'chunk_id':rows[0]['chunk_id'],
        'kind':'metric','dataset':'DatasetA','metric':'Accuracy','value':'91%',
        'baseline':'BaselineX','setting':'split1','quote':QUOTE}]})
    ids=[]
    for name in ('UserA','UserB'):
        with auth_db(p.auth_path) as db:
            uid=create_account(db,name,'a very long password for tests')['id']
        p.search(uid,'default')
        ids.append(uid)
        for pid in ('2601.12345','2601.12346'):
            save_pdf(p,uid,'default',pid,pdf_bytes())
            assert analyze(p,uid,'default',pid,cloud=True)['evidence_count']==1
    return p,ids


def approve(p,uid,*,note='Checked the source PDF table'):
    rows=records(p,uid,'default')
    assert len(rows)==2
    for r in rows:
        out=review_evidence(p,uid,'default',r['paper_id'],r['evidence_id'],
            'approved',CONDITIONS,note)
        assert out['decision']=='approved'
    return records(p,uid,'default')


def test_append_only_upgrade_v6_to_v7_with_existing_evidence(tmp_path,monkeypatch):
    steps=REGISTRIES['portal_workspace']
    monkeypatch.setitem(REGISTRIES,'portal_workspace',steps[:6])
    db=sqlite3.connect(tmp_path/'existing.sqlite')
    assert migrate(db,'portal_workspace')==6
    db.execute("INSERT INTO topics VALUES ('old','Legacy theme','[]','2026-01-01')")
    db.commit()
    monkeypatch.setitem(REGISTRIES,'portal_workspace',steps)
    assert migrate(db,'portal_workspace')==len(steps)
    assert db.execute('SELECT name FROM topics WHERE id="old"').fetchone()[0]=='Legacy theme'
    assert db.execute('SELECT COUNT(*) FROM evidence_reviews').fetchone()[0]==0
    assert db.execute('SELECT COUNT(*) FROM comparison_pair_reviews').fetchone()[0]==0
    db.close()


def test_review_requires_complete_conditions_and_audit_reason(tmp_path):
    p,users=setup(tmp_path);uid=users[0]
    r=records(p,uid,'default')[0]
    with pytest.raises(ValueError,match='完整记录'):
        review_evidence(p,uid,'default',r['paper_id'],r['evidence_id'],
            'approved',{'split':'test'},'Reviewed carefully')
    with pytest.raises(ValueError,match='依据'):
        review_evidence(p,uid,'default',r['paper_id'],r['evidence_id'],
            'approved',CONDITIONS,'')
    assert review_evidence(p,uid,'default',r['paper_id'],r['evidence_id'],
        'needs_review',{},'')['decision']=='needs_review'
    approved=review_evidence(p,uid,'default',r['paper_id'],r['evidence_id'],
        'approved',CONDITIONS,'Validated original table header')
    assert approved['decision']=='approved'
    original=records(p,uid,'default')[0]
    assert original['review_conditions']==CONDITIONS
    assert original['relation_verified'] is False
    assert original['quote_verified_against_pdf'] is True


def test_pair_only_user_confirmed_with_current_sha_and_matching_protocol(tmp_path):
    p,users=setup(tmp_path);uid=users[0]
    records2=approve(p,uid)
    report=synthesis(p,uid,'default')['summary']
    assert report['comparisons'][0]['review_status']=='manual_review_required'
    ids=[r['evidence_id'] for r in records2]
    result=review_pair(p,uid,'default',ids[0],ids[1],'comparable',
                       'Matched published test protocol and official dataset split')
    assert result['assessment']['eligible']
    report=synthesis(p,uid,'default')['summary']
    assert report['comparisons'][0]['review_status']=='user_confirmed_conditions'
    assert len(report['comparisons'][0]['user_confirmed_pairs'])==1
    assert '人工确认' in synthesis(p,uid,'default')['markdown']
    # A changed human evidence audit must revoke its dependent pair judgement.
    review_evidence(p,uid,'default',records2[0]['paper_id'],ids[0],
                    'approved',{**CONDITIONS,'split':'validation'},'Corrected split after inspection')
    report=synthesis(p,uid,'default')['summary']
    assert report['comparisons'][0]['review_status']=='manual_review_required'
    assert report['comparisons'][0]['blocked_pairs']
    with pytest.raises(ValueError,match='条件不满足'):
        review_pair(p,uid,'default',*ids,'comparable','Attempted unsafe comparison')


def test_rejection_removes_report_candidate_but_stays_in_pdf_reader(tmp_path):
    p,users=setup(tmp_path);uid=users[0]
    e=records(p,uid,'default')[0]
    review_evidence(p,uid,'default',e['paper_id'],e['evidence_id'],
                    'rejected',{},'Table cell belongs to a different method')
    assert preview(p,uid,'default',e['paper_id'],1)['evidence'][0]['review_decision']=='rejected'
    summary=synthesis(p,uid,'default')['summary']
    assert summary['finding_count']==1 and summary['comparisons']==[]


def test_account_and_topic_cannot_cross_review_or_pair(tmp_path):
    p,users=setup(tmp_path);a,b=users
    r=records(p,a,'default')[0]
    # Even identical paper IDs have unrelated evidence/signature storage per user.
    assert records(p,b,'default')[0]['evidence_id']==r['evidence_id']
    review_evidence(p,a,'default',r['paper_id'],r['evidence_id'],
                    'approved',CONDITIONS,'Verified in user A copy')
    assert records(p,b,'default')[0]['review_decision']=='needs_review'
    with pytest.raises(ValueError,match='主题|Paper'):
        review_evidence(p,a,'nonexistent',r['paper_id'],r['evidence_id'],
                        'approved',CONDITIONS,'Should not work')
    assert not records(p,b,'default')[0]['review_note']


def test_replacing_pdf_revokes_evidence_and_pair_reviews(tmp_path):
    p,users=setup(tmp_path);uid=users[0]
    rr=approve(p,uid)
    review_pair(p,uid,'default',rr[0]['evidence_id'],rr[1]['evidence_id'],
                'comparable','Checked matching units and evaluation protocols')
    pid=rr[0]['paper_id']
    save_pdf(p,uid,'default',pid,pdf_bytes('Different experiments using DatasetA score 45%'))
    with p.workspace(uid) as db:
        assert db.execute('SELECT COUNT(*) FROM evidence_reviews WHERE paper_id=?',(pid,)).fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM comparison_pair_reviews').fetchone()[0]==0
    assert synthesis(p,uid,'default')['summary']['comparisons']==[]
    with pytest.raises(ValueError,match='失效|不存在'):
        review_evidence(p,uid,'default',pid,rr[0]['evidence_id'],
                        'approved',CONDITIONS,'The old PDF was replaced')


def test_protocol_policy_blocks_unit_and_split_conflicts():
    a={'paper_id':'a','kind':'metric','dataset':'DatasetA','metric':'RMSE','setting':'test',
       'baseline':'B','value':'5%', 'review_decision':'approved',
       'quote_verified_against_pdf':True,'evidence_id':'a'*64,'review_conditions':CONDITIONS}
    b={**a,'paper_id':'b','evidence_id':'b'*64,'value':'4%'}
    assert pair_assessment(a,b)['eligible']
    for field,value in [('split','val'),('unit','fraction'),('dataset_version','release 2'),
                        ('evaluation_protocol','alternate protocol'),('metric_direction','lower'),
                        ('training_budget','10 epochs'),('aggregation','median')]:
        bad={**b,'review_conditions':{**CONDITIONS,field:value}}
        assert not pair_assessment(a,bad)['eligible'],field
    assert not pair_assessment(a,{**b,'baseline':'Other'})['eligible']
    assert not pair_assessment(a,{**b,'value':'0.04'})['eligible']
    assert not pair_assessment(a,{**b,'review_decision':'needs_review'})['eligible']
    with pytest.raises(ValueError):validate_conditions({**CONDITIONS,'javascript':'evil'},require_complete=True)


def test_http_review_routes_require_csrf_and_are_tenant_scoped(tmp_path):
    p=Portal(tmp_path/'data',embedded_workers=False)
    p.fetcher=lambda cfg,logger:[paper('2601.12345')]
    p.model_callable=lambda rows:json.dumps({'findings':[{'chunk_id':rows[0]['chunk_id'],
        'kind':'metric','dataset':'DatasetA','metric':'Accuracy','value':'91%',
        'baseline':'BaselineX','setting':'split1','quote':QUOTE}]})
    server=make_server('127.0.0.1',0,p)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        a,b=Client(server.server_port),Client(server.server_port)
        uid=register(a,'ReviewUserA')['user']['id']
        register(b,'ReviewUserB')
        p.search(uid,'default');save_pdf(p,uid,'default','2601.12345',pdf_bytes());analyze(p,uid,'default','2601.12345',cloud=True)
        ev=records(p,uid,'default')[0]
        payload={'topic_id':'default','paper_id':ev['paper_id'],'evidence_id':ev['evidence_id'],
                 'decision':'approved','conditions':CONDITIONS,'note':'I inspected the source PDF table'}
        assert a.post('/api/public/evidence/review',payload,csrf=False)[0]==403
        assert a.post('/api/public/evidence/review',payload,origin='https://evil.example')[0]==403
        assert b.post('/api/public/evidence/review',payload)[0]==400
        code,result=a.post('/api/public/evidence/review',payload)
        assert code==200,result
        assert result['decision']=='approved'
        assert a.get('/api/public/fulltext?paper_id=2601.12345&topic_id=default')[1]['evidence'][0]['review_decision']=='approved'
        assert b.post('/api/public/comparisons/review',{'topic_id':'default',
            'evidence_a_id':ev['evidence_id'],'evidence_b_id':'1'*64,
            'decision':'comparable','note':'wrong account'})[0]==400
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_frontend_has_collapsed_review_controls():
    html=Path(__file__).parents[1]/'paper_radar/static/portal.js'
    js=html.read_text()
    assert "'/api/public/evidence/review'" in js
    assert "'/api/public/comparisons/review'" in js
    assert "const details=node('details'" in js
    assert 'innerHTML' in js  # only mention is a comment forbidding it


def test_review_audit_history_persists_after_pdf_replacement(tmp_path):
    from paper_radar.portal_reviews import review_history
    p,users=setup(tmp_path);uid=users[0]
    rr=approve(p,uid)
    review_evidence(p,uid,'default',rr[0]['paper_id'],rr[0]['evidence_id'],
                    'rejected',{},'Found the wrong data row')
    history=review_history(p,uid,'default',rr[0]['paper_id'])['history']
    assert len(history)==2 and history[0]['decision']=='rejected'
    with p.workspace(uid) as db:
        assert db.execute('SELECT COUNT(*) FROM evidence_review_history').fetchone()[0]==3
    save_pdf(p,uid,'default',rr[0]['paper_id'],pdf_bytes('Changed study, no matching values.'))
    assert not records(p,uid,'default') or len(records(p,uid,'default'))==1
    preserved=review_history(p,uid,'default',rr[0]['paper_id'])
    assert len(preserved['history'])==2
    assert '历史' in preserved['warning']


def test_explicit_user_incompatible_pair_prevents_comparability_status(tmp_path):
    p,users=setup(tmp_path);uid=users[0]
    rr=approve(p,uid)
    result=review_pair(p,uid,'default',rr[0]['evidence_id'],rr[1]['evidence_id'],
                       'incompatible','Paper B has undocumented preprocessing bias')
    assert result['decision']=='incompatible'
    cmp=synthesis(p,uid,'default')['summary']['comparisons'][0]
    assert cmp['review_status']=='manual_review_required'
    assert any('用户人工判定不可比' in ' '.join(b['reasons']) for b in cmp['blocked_pairs'])
    with p.workspace(uid) as db:
        assert db.execute('SELECT COUNT(*) FROM comparison_pair_review_history').fetchone()[0]==1


def test_evaluation_only_measures_synthetic_protocol_cases():
    from tools.eval_comparison_policy import evaluate
    report=evaluate()
    assert report['data_type']=='synthetic_hand_authored'
    assert report['n']==15 and report['true_positive']==2 and report['true_negative']==13
    assert report['false_positive']==0 and report['false_negative']==0
    assert 'not PDF extraction' in report['evaluation_target']


def test_deployment_template_and_immutable_report_schema(tmp_path):
    from tools.verify_public_deployment import inspect_templates
    from paper_radar.portal_reports import create_report,read_report
    assert inspect_templates()==[]
    p,users=setup(tmp_path);uid=users[0]
    row=create_report(p,uid,'default')
    report=read_report(p,uid,row['id'])
    assert report['schema_version']==2
    assert report['summary']['comparisons'][0]['review_status']=='manual_review_required'
    assert 'manual_review_required' in report['markdown'] and '人工复核项目' in report['markdown']


def test_http_review_pair_and_history_end_to_end(tmp_path):
    p=Portal(tmp_path/'data',embedded_workers=False)
    p.fetcher=lambda cfg,logger:[paper('2601.12345'),paper('2601.12346')]
    p.model_callable=lambda rows:json.dumps({'findings':[{'chunk_id':rows[0]['chunk_id'],
        'kind':'metric','dataset':'DatasetA','metric':'Accuracy','value':'91%',
        'baseline':'BaselineX','setting':'split1','quote':QUOTE}]})
    server=make_server('127.0.0.1',0,p)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        a,b=Client(server.server_port),Client(server.server_port)
        uid=register(a,'HttpPairUserA')['user']['id']
        register(b,'HttpPairUserB')
        p.search(uid,'default')
        for pid in ('2601.12345','2601.12346'):
            save_pdf(p,uid,'default',pid,pdf_bytes());analyze(p,uid,'default',pid,cloud=True)
        rr=records(p,uid,'default')
        ids=[]
        for r in rr:
            ids.append(r['evidence_id'])
            code,result=a.post('/api/public/evidence/review',{'topic_id':'default',
                'paper_id':r['paper_id'],'evidence_id':r['evidence_id'],
                'decision':'approved','conditions':CONDITIONS,
                'note':'Verified original table and experimental settings'})
            assert code==200,result
        data={'topic_id':'default','evidence_a_id':ids[0],
            'evidence_b_id':ids[1],'decision':'comparable',
            'note':'Validated matching split, unit, metric and budget'}
        assert b.post('/api/public/comparisons/review',data)[0]==400
        assert a.post('/api/public/comparisons/review',data,csrf=False)[0]==403
        code,response=a.post('/api/public/comparisons/review',data)
        assert code==200,response
        status,summary=a.get('/api/public/synthesis?topic_id=default')
        assert status==200 and summary['summary']['comparisons'][0]['review_status']=='user_confirmed_conditions'
        status,history=a.get('/api/public/reviews/history?topic_id=default&paper_id=2601.12345')
        assert status==200 and history['history']
        assert b.get('/api/public/reviews/history?topic_id=default&paper_id=2601.12345')[0]==400
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_pair_review_does_not_overwrite_other_topic_decisions(tmp_path):
    p,users=setup(tmp_path);uid=users[0]
    rr=approve(p,uid)
    first,second=rr
    ids=(first['evidence_id'],second['evidence_id'])
    with p.workspace(uid) as db:
        db.execute("INSERT INTO topics VALUES ('extra','Independent topic','[\"neural operator\"]','now')")
        for r in rr:db.execute('INSERT INTO topic_papers VALUES (?,?)',('extra',r['paper_id']))
    result_a=review_pair(p,uid,'default',*ids,'comparable',
                         'The official test protocol is shared in topic default')
    result_b=review_pair(p,uid,'extra',*ids,'incompatible',
                         'Theme extra requires a different sample budget')
    assert result_a['pair_id']!=result_b['pair_id']
    assert synthesis(p,uid,'default')['summary']['comparisons'][0]['review_status']=='user_confirmed_conditions'
    assert synthesis(p,uid,'extra')['summary']['comparisons'][0]['review_status']=='manual_review_required'
    with p.workspace(uid) as db:
        assert db.execute('SELECT COUNT(*) FROM comparison_pair_reviews').fetchone()[0]==2


def test_pair_review_rechecks_conditions_after_preflight_race(tmp_path,monkeypatch):
    from paper_radar import portal_reviews
    p,users=setup(tmp_path);uid=users[0]
    items=approve(p,uid)
    ids=[row['evidence_id'] for row in items]
    original=portal_reviews.pair_assessment
    changed=[False]
    def concurrent_change(a,b):
        outcome=original(a,b)
        if not changed[0]:
            changed[0]=True
            review_evidence(p,uid,'default',items[0]['paper_id'],ids[0],
                'approved',{**CONDITIONS,'split':'validation split'},
                'Concurrent correction after initial pair inspection')
        return outcome
    monkeypatch.setattr(portal_reviews,'pair_assessment',concurrent_change)
    with pytest.raises(ValueError,match='\u5b9e\u9a8c\u6761\u4ef6\u5df2\u66f4\u65b0'):
        review_pair(p,uid,'default',ids[0],ids[1],'comparable',
                    'Was checking experimental conditions before review changed')
    with p.workspace(uid) as db:
        assert db.execute('SELECT COUNT(*) FROM comparison_pair_reviews').fetchone()[0]==0
