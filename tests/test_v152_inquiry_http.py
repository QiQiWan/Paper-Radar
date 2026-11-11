# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import urlopen,Request
from urllib.error import HTTPError
from urllib.parse import urlencode
import json
from paper_radar import webapp
from paper_radar.core import connect,DEFAULTS
from paper_radar.research_jobs import process_job
from test_v152_scope_jobs_and_acceptance import fixtures,QUESTION


def test_http_scope_preview_background_job_and_result(tmp_path,monkeypatch):
    path=tmp_path/'papers.sqlite3';fixtures(path,18)
    @contextmanager
    def db():
        with connect(path) as db:
            with db:yield db
    class Runtime:
        last_seen=0
        def __init__(self):self.called=[]
        def state(self):return {'busy':False}
        def start(self,mode,args):self.called.append((mode,args))
    fake=Runtime()
    monkeypatch.setattr(webapp,'DB_FILE',path)
    monkeypatch.setattr(webapp,'open_database',db)
    monkeypatch.setattr(webapp,'runtime',fake)
    monkeypatch.setattr(webapp,'read_config',lambda:{**DEFAULTS,'active_topic_id':'default'})
    monkeypatch.setattr(webapp,'config_health',lambda _:{'read_only':False})
    server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
    server.token='research-v152-test'
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    def get(path):
        with urlopen(base+path,timeout=10) as response:
            return response.status,json.load(response)
    def post(route,payload,authorized=True):
        headers={'Origin':base,'Content-Type':'application/json'}
        if authorized:headers['X-Local-Token']=server.token
        req=Request(base+route,data=json.dumps(payload,ensure_ascii=False).encode(),headers=headers)
        with urlopen(req,timeout=10) as response:
            return response.status,json.load(response)
    try:
        _,preview=get('/api/inquiry/preview?'+urlencode({'question':QUESTION}))
        assert preview['topic_total']==18
        assert len(preview['recommended_ids'])==16
        with __import__('pytest').raises(HTTPError) as err:
            post('/api/inquiry/jobs',{'question':QUESTION,'selection_mode':'all'},False)
        assert err.value.code==403
        _,created=post('/api/inquiry/jobs',{'question':QUESTION,'selection_mode':'manual',
            'paper_ids':['2601.10001','2601.10002']})
        assert created['selected_papers']==2
        assert fake.called==[('inquiry',[created['run_id']])]
        _,pending=get('/api/inquiry/job?'+urlencode({'run_id':created['run_id']}))
        assert pending['state']=='queued'
        process_job(path,created['run_id'],DEFAULTS)
        _,finished=get('/api/inquiry/job?'+urlencode({'run_id':created['run_id']}))
        assert finished['state']=='completed'
        assert finished['metrics']['selected_papers']==2
        _,result=get('/api/inquiry/view?'+urlencode({'id':finished['report_id']}))
        assert result['brief']['selection']['selected_ids']==['2601.10001','2601.10002']
        _,latest=get('/api/inquiry/latest')
        assert latest['id']==created['run_id']
    finally:
        server.shutdown();server.server_close();thread.join(3)
