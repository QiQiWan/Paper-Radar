"""v1.37-39 integration: label coverage and authenticated PDF evidence navigation."""
from __future__ import annotations

import io
import json
from threading import Thread
from urllib.parse import quote

import pytest

from paper_radar.retrieval_eval import evaluate,evaluate_files
from paper_radar.public_portal import Portal,make_server
from paper_radar.models import Paper
from paper_radar.portal_research import save_pdf
from tests.test_v136_portal_following import Session


def test_retrieval_metrics_do_not_invent_unjudged_accuracy():
    gold=[{'topic':'topic-a','paper_id':'p1','relevant':True},
          {'topic':'topic-a','paper_id':'p2','relevant':False}]
    run=[{'topic':'topic-a','paper_id':'p1','rank':1},
         {'topic':'topic-a','paper_id':'p3','rank':2}]
    partial=evaluate(gold,run,k=2,closed_pool=False)['topics'][0]
    assert partial['precision_at_k'] is None
    assert partial['judged_precision_at_k']==1.0
    assert partial['label_coverage_at_k']==.5
    assert partial['recall_at_200'] is None
    assert partial['partial_pool_recall_at_200']==1.0
    with pytest.raises(ValueError,match='重复'):
        evaluate(gold,[run[0],run[0]],k=2)


def test_closed_pool_precision_uses_k_denominator():
    gold=[{'topic':'X','paper_id':'p1','relevant':True}]
    prediction=[{'topic':'X','paper_id':'p1','rank':1}]
    result=evaluate(gold,prediction,k=20,closed_pool=True)['topics'][0]
    assert result['precision_at_k']==1/20
    assert result['recall_at_200']==1.0


def test_retrieval_file_runner(tmp_path):
    gold=tmp_path/'gold.jsonl';run=tmp_path/'run.jsonl'
    gold.write_text(json.dumps({'topic':'X','paper_id':'p1','relevant':True})+'\n',encoding='utf-8')
    run.write_text(json.dumps({'topic':'X','paper_id':'p1','rank':1})+'\n',encoding='utf-8')
    assert evaluate_files(gold,run,k=1,closed_pool=True)['macro_precision_at_k']==1
    gold.write_text('{"topic":"X","paper_id":"p1","relevant":"true"}\n',encoding='utf-8')
    with pytest.raises(ValueError):evaluate_files(gold,run)


@pytest.fixture
def portal_pdf_service(tmp_path):
    fitz=pytest.importorskip('fitz')
    portal=Portal(tmp_path/'data',job_workers=0)
    portal.fetcher=lambda cfg,logger:[Paper('PAPER_1','Operator learning','A safe abstract',
        'Researcher','2026-11-08','2026-11-08','cs.LG','https://example.com/paper','',source='arxiv')]
    server=make_server('127.0.0.1',0,portal)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    a=Session(server.server_port);b=Session(server.server_port)
    status,reg=a.post('/api/public/register',{'username':'PDFResearcherA','password':'strong passphrase alpha 123'})
    assert status==201,reg
    status,regb=b.post('/api/public/register',{'username':'PDFResearcherB','password':'strong passphrase beta 456'})
    assert status==201,regb
    uid=reg['user']['id']
    portal.search(uid,'default')
    doc=fitz.open();page=doc.new_page();page.insert_text((75,100),'Operator learning improves accuracy on this dataset.')
    raw=doc.tobytes();doc.close()
    save_pdf(portal,uid,'default','PAPER_1',raw)
    try:
        yield portal,a,b
    finally:
        server.shutdown();server.server_close();thread.join(timeout=4)


def test_public_pdf_locate_highlight_table_and_isolation(portal_pdf_service):
    portal,a,b=portal_pdf_service
    q='topic_id=default&paper_id=PAPER_1&page=1&quote='+quote('Operator learning')
    code,data=a.get('/api/public/pdf/locate?'+q)
    assert code==200,data
    assert data['matched'] and data['boxes']
    assert a.get('/api/public/pdf/tables?'+q)[0]==200
    import http.client
    c=http.client.HTTPConnection('127.0.0.1',a.port,timeout=5)
    c.request('GET','/api/public/pdf/highlight?'+q,
              headers={'Host':f'127.0.0.1:{a.port}','Cookie':a.cookie})
    resp=c.getresponse();png=resp.read();assert resp.status==200
    assert resp.getheader('Content-Type')=='image/png'
    assert png.startswith(b'\x89PNG')
    c.close()
    assert b.get('/api/public/pdf/locate?'+q)[0]==400
    assert a.get('/api/public/pdf/locate?'+q.replace('paper_id=PAPER_1','paper_id=unknown'))[0]==400
    assert a.get('/api/public/pdf/locate?'+q.replace('page=1','page=999'))[0]==400
    assert a.get('/api/public/pdf/highlight?'+q.replace('Operator%20learning','fabricated%20quote'))[0]==400


def test_public_pdf_changed_file_rejected(portal_pdf_service):
    portal,a,b=portal_pdf_service
    from paper_radar.portal_research import path_for
    uid=None
    from paper_radar.portal_auth import db as auth_db
    with auth_db(portal.auth_path) as db:
        uid=db.execute('SELECT id FROM accounts WHERE username=?',('PDFResearcherA',)).fetchone()['id']
    path=path_for(portal,uid,'PAPER_1')
    with path.open('ab') as f:f.write(b'modified')
    q='topic_id=default&paper_id=PAPER_1&page=1&quote=Operator%20learning'
    code,data=a.get('/api/public/pdf/locate?'+q)
    assert code==400
    assert '不可用' in data['error'] or '指纹' in data['error']


def test_local_http_can_save_openreview_invitation(monkeypatch):
    import http.client
    from http.server import ThreadingHTTPServer
    from paper_radar import webapp
    from paper_radar.core import DEFAULTS
    cfg=dict(DEFAULTS)
    saved={}
    monkeypatch.setattr(webapp,'read_config',lambda:cfg.copy())
    def save(payload):
        saved.update(payload)
        return payload
    monkeypatch.setattr(webapp,'save_config',save)
    monkeypatch.setattr(webapp,'config_health',lambda *_:{'read_only':False})
    monkeypatch.setattr(webapp.update_checker,'schedule',lambda *a,**kw:False)
    monkeypatch.setattr(webapp,'get_api_key',lambda *a,**kw:None)
    srv=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
    srv.daemon_threads=True;srv.token='local-test-token'
    t=Thread(target=srv.serve_forever,daemon=True);t.start()
    try:
        invitation='ICLR.cc/2026/Conference/-/Submission'
        conn=http.client.HTTPConnection('127.0.0.1',srv.server_port,timeout=5)
        conn.request('POST','/api/config',body=json.dumps({'openreview_invitation':invitation}),
             headers={'Host':f'127.0.0.1:{srv.server_port}',
                      'Content-Type':'application/json','X-Local-Token':'local-test-token',
                      'Origin':f'http://127.0.0.1:{srv.server_port}'})
        r=conn.getresponse();body=json.loads(r.read());assert r.status==200,body
        assert saved['openreview_invitation']==invitation
        conn.close()
    finally:
        srv.shutdown();srv.server_close();t.join(timeout=3)
