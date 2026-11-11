"""v1.25: topic sources, safe retries, live evidence export and account isolation."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from threading import Thread
import http.client

import pytest

from paper_radar.migrations import REGISTRIES, status
from paper_radar.models import Paper
from paper_radar.portal_auth import db as auth_db,create_account
from paper_radar.portal_discovery import validate_sources,topic_sources
from paper_radar.portal_jobs import QueueError
from paper_radar.public_portal import Portal,make_server,PortalError
from test_v116_portal_and_synthesis import Client,register


def mkpaper(pid, doi='', title='Paper Title'):
    return Paper(paper_id=pid,title=title,abstract='A relevant scientific paper',
        authors='A. Researcher',published='2026-10-01',updated='2026-10-02T00:00:00Z',
        category='cs.LG',url='https://example.org/paper',pdf_url='',doi=doi)


@pytest.fixture
def web(tmp_path):
    portal=Portal(tmp_path/'data',embedded_workers=False)
    server=make_server('127.0.0.1',0,portal)
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:yield portal,server.server_port
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def test_additive_schema_sources_and_legacy_fallback(web):
    p,port=web; a=Client(port);user=register(a,'SourcesUserA')
    uid=user['user']['id']
    with p.workspace(uid) as db:
        assert status(db,'portal_workspace')['version']==len(REGISTRIES['portal_workspace'])
        assert topic_sources(db,'default')==['arxiv']
        assert db.execute('SELECT COUNT(*) FROM topic_sources').fetchone()[0]==0
    code,data=a.post('/api/public/topics',{'name':'Hybrid operators',
        'keywords':['neural operators'],'sources':['crossref','arxiv']})
    assert code==201,data
    assert data['sources']==['arxiv','crossref']
    assert next(x for x in a.get('/api/public/topics')[1]['topics']
                if x['id']==data['id'])['sources']==['arxiv','crossref']
    with p.workspace(uid) as db:
        assert topic_sources(db,data['id'])==['arxiv','crossref']
    assert len(REGISTRIES['portal_auth'])==6


def test_topic_edit_is_scoped_and_validated(web):
    p,port=web; a,b=Client(port),Client(port)
    ua=register(a,'TopicOwnerA')['user']['id'];register(b,'TopicOwnerB')
    code,created=a.post('/api/public/topics',{'name':'Research',
        'keywords':['FNO']})
    assert code==201,created
    topic=created['id']
    data={'topic_id':topic,'name':'Edited research','keywords':['PINO','FNO'],
          'sources':['openalex','arxiv','crossref']}
    assert b.post('/api/public/topics/update',data)[0]==400
    assert a.post('/api/public/topics/update',data,csrf=False)[0]==403
    assert a.post('/api/public/topics/update',{**data,'sources':['arxiv','private-provider']})[0]==400
    assert a.post('/api/public/topics/update',{**data,'sources':[]})[0]==400
    assert a.post('/api/public/topics/update',{**data,'keywords':[42]})[0]==400
    assert a.post('/api/public/topics/update',data)[0]==200
    fetched=next(x for x in a.get('/api/public/topics')[1]['topics'] if x['id']==topic)
    assert fetched['name']=='Edited research'
    assert fetched['sources']==['arxiv','openalex','crossref']
    assert len(b.get('/api/public/topics')[1]['topics'])==1


def test_public_sources_continue_after_failure_and_dedupe(web,monkeypatch):
    p,port=web; a=Client(port);uid=register(a,'SearchOwner')['user']['id']
    code,topic=a.post('/api/public/topics',{'name':'Multi source','keywords':['Fourier'],
        'sources':['arxiv','openalex','crossref']})
    assert code==201
    def fake_arxiv(cfg,checkpoint):
        return [mkpaper('2601.00001','10.1111/shared','A confirmed arXiv paper')],[]
    def fake_source(name,cfg,should_cancel=None):
        if name=='openalex':
            return [mkpaper('openalex:W100','10.1111/shared','Duplicate DOI'),
                    mkpaper('openalex:W200','10.1111/second','Different paper')]
        raise RuntimeError('internal provider URL must not reach the browser')
    monkeypatch.setattr(p,'_fetch_public_arxiv',fake_arxiv)
    monkeypatch.setattr(p,'_fetch_public_source',fake_source)
    info=p.search(uid,topic['id'])
    assert info['state']=='partial' and info['retrieved']==2 and info['new']==2
    assert [x['ok'] for x in info['source_results']]==[True,True,False]
    assert 'internal provider' not in json.dumps(info)
    with p.workspace(uid) as db:
        rows=db.execute('SELECT id FROM papers ORDER BY id').fetchall()
        assert {r[0] for r in rows}=={'2601.00001','openalex:W200'}


def test_all_sources_fail_does_not_create_papers(web,monkeypatch):
    p,port=web; a=Client(port);uid=register(a,'AllUnavailable')['user']['id']
    monkeypatch.setattr(p,'_fetch_public_arxiv',lambda cfg,checkpoint: (_ for _ in ()).throw(RuntimeError('provider secret')))
    with pytest.raises(PortalError,match='所有检索来源均失败') as err:
        p.search(uid,'default')
    assert 'secret' not in str(err.value)
    with p.workspace(uid) as db:
        assert db.execute('SELECT COUNT(*) FROM papers').fetchone()[0]==0


def test_cross_source_existing_doi_preserves_user_flags(web,monkeypatch):
    p,port=web; a=Client(port);uid=register(a,'DedupeUser')['user']['id']
    with p.workspace(uid) as db:
        db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',
            ('existing','Existing metadata','','2026-03-01','https://example.org','10.1111/ref','now'))
        db.execute('INSERT INTO topic_papers VALUES (?,?)',('default','existing'))
        db.execute("INSERT INTO topic_paper_flags VALUES ('default','existing',1,'reading',1,'now')")
    p.fetcher=lambda cfg,logger:[mkpaper('2601.00002','10.1111/ref','New metadata')]
    p.search(uid,'default')
    with p.workspace(uid) as db:
        assert db.execute('SELECT COUNT(*) FROM papers').fetchone()[0]==1
        assert db.execute('SELECT title FROM papers').fetchone()[0]=='Existing metadata'
        assert db.execute('SELECT is_starred FROM topic_paper_flags').fetchone()[0]==1


def test_safe_explicit_task_retry_with_quota_and_permissions(web):
    p,port=web;a,b=Client(port),Client(port)
    uid=register(a,'RetryUserA')['user']['id']
    register(b,'RetryUserB')
    search=p.job_queue.enqueue(uid,'default',kind='search')
    assert p.job_queue.cancel(uid,search['id'])
    assert p.job_queue.detail(uid,search['id'])['retryable']
    assert b.post('/api/public/jobs/retry',{'job_id':search['id']})[0] in (400,429)
    code,retry=a.post('/api/public/jobs/retry',{'job_id':search['id']})
    assert code==202,retry
    assert retry['retry_of']==search['id'] and retry['job_id']!=search['id']
    # A queued retry consumes an active slot, so stop it before other tasks.
    assert p.job_queue.cancel(uid,retry['job_id'])
    paid=p.job_queue.enqueue(uid,'default',kind='analyze',paper_id='fake',
                             options={'cloud_analysis':True,'fetch_pdf':False})
    assert p.job_queue.cancel(uid,paid['id'])
    assert not p.job_queue.detail(uid,paid['id'])['retryable']
    assert a.post('/api/public/jobs/retry',{'job_id':paid['id']})[0]==429
    full=p.job_queue.enqueue(uid,'default',kind='research',options={'cloud_analysis':False,'fetch_pdf':True})
    assert p.job_queue.cancel(uid,full['id'])
    assert not p.job_queue.detail(uid,full['id'])['retryable']


def test_csv_export_is_isolated_and_formula_safe(web):
    p,port=web;a,b=Client(port),Client(port)
    uid=register(a,'ExportOwner')['user']['id'];register(b,'ExportOther')
    with p.workspace(uid) as db:
        db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',
            ('p','Paper 1','','2026-10-01','','','now'))
        db.execute('INSERT INTO topic_papers VALUES (?,?)',('default','p'))
        db.execute("INSERT INTO pdf_documents VALUES ('p',?, 'upload', 1, '')",('b'*64,))
        db.execute('''INSERT INTO verified_evidence
            (id,paper_id,sha256,kind,dataset,metric,value,baseline,setting,page,quote,provenance,extractor)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('a'*64,'p','b'*64,'metric','Darcy','RMSE','=HYPERLINK(\"https://bad\")','Baseline',
             'fixed',3,'Darcy RMSE table row','pdf_quote_verified','mock'))
        db.execute('''INSERT INTO evidence_reviews
            (evidence_id,paper_id,sha256,decision,conditions_json,note,reviewer_id,reviewed_at)
            VALUES (?,?,?,?,?,?,?,?)''',
            ('a'*64,'p','b'*64,'approved','{}','confirmed',uid,'2026-11-06'))
    from paper_radar.portal_export import export_evidence
    body=export_evidence(p,uid,'default')
    assert body.startswith(b'\xef\xbb\xbf')
    rows=list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
    assert len(rows)==1 and rows[0]['人工复核状态']=='approved'
    assert rows[0]['原文数值'].startswith("'=HYPERLINK")
    from paper_radar.portal_export import _safe_cell
    assert _safe_cell('\ufeff\u200b  =HYPERLINK("https://bad")').startswith("'\ufeff")
    assert len(list(csv.DictReader(io.StringIO(export_evidence(p,register_isolated_account(p),'default').decode('utf-8-sig')))))==0
    assert a.get('/api/public/evidence/export?topic_id=forbidden')[0]==400
    assert b.get('/api/public/evidence/export')[0]==200


def register_isolated_account(portal):
    with auth_db(portal.auth_path) as db:
        return create_account(db,'NewIsolatedUser','a strong offline password 9876')['id']


def test_dashboard_to_read_pending_and_exact_paper_jump(web):
    p,port=web;a=Client(port);uid=register(a,'DashboardPlus')['user']['id']
    with p.workspace(uid) as db:
        for paper_id in ('p1','p2'):
            db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',(paper_id,paper_id,'','2026-11-05','','','now'))
            db.execute('INSERT INTO topic_papers VALUES (?,?)',('default',paper_id))
        db.execute("INSERT INTO topic_paper_flags VALUES ('default','p1',1,'unread',0,'now')")
        db.execute("INSERT INTO pdf_documents VALUES ('p1',?, 'upload', 1, '')",('b'*64,))
        db.execute('''INSERT INTO verified_evidence
            (id,paper_id,sha256,kind,dataset,metric,value,baseline,setting,page,quote,provenance,extractor)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            ('a'*64,'p1','b'*64,'metric','Set','Score','0.1','Base','fixed',1,'Score 0.1','pdf_quote_verified','mock'))
    dash=a.get('/api/public/dashboard')[1]
    assert dash['to_read']==1 and dash['pending_review']==1
    assert dash['pending_papers']==[{'id':'p1','title':'p1','first_page':1,'pending_count':1}]
    assert a.get('/api/public/papers?paper_id=p1')[1]['total']==1
    assert a.get('/api/public/papers?filter=to_read')[1]['total']==1
    assert a.get('/api/public/papers?filter=starred')[1]['total']==1
    assert a.get('/api/public/papers?paper_id=wrong')[1]['total']==0


def test_frontend_has_functional_controls_and_safety():
    root=Path(__file__).parents[1]/'paper_radar/static'
    html=(root/'portal.html').read_text()
    js=(root/'portal.js').read_text()
    for name in ('topicSave','openReadingQueue','openEvidenceQueue','retrySafeJob',
                 'exportEvidence','jobSourceResults','showAllPapers','metricPendingEvidence'):
        assert 'id="'+name+'"' in html
        assert "$('"+name+"')" in js
    assert '.innerHTML=' not in js and '.innerHTML =' not in js


def test_public_source_child_does_not_inherit_model_and_api_secrets(monkeypatch):
    from paper_radar.sources.supervisor import source_child_env
    monkeypatch.setenv('OPENALEX_API_KEY','sample-restricted-key')
    monkeypatch.setenv('PAPERRADAR_PUBLIC_LLM_KEY','sample-model-secret')
    monkeypatch.delenv('PAPERRADAR_PUBLIC_ALLOW_OPENALEX_API_KEY',raising=False)
    assert source_child_env('openalex') is None  # local desktop unchanged
    public = source_child_env('openalex',public_metadata=True)
    assert 'OPENALEX_API_KEY' not in public and 'PAPERRADAR_PUBLIC_LLM_KEY' not in public
    assert 'OPENALEX_API_KEY' not in source_child_env('crossref',public_metadata=True)
    monkeypatch.setenv('PAPERRADAR_PUBLIC_ALLOW_OPENALEX_API_KEY','1')
    approved = source_child_env('openalex',public_metadata=True)
    assert approved['OPENALEX_API_KEY']=='sample-restricted-key'
    assert 'PAPERRADAR_PUBLIC_LLM_KEY' not in approved
