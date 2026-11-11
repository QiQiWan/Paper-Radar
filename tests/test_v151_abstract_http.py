# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Real HTTP + SQLite integration for the abstract-first workflow."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import io
import json
import zipfile
import pytest
from paper_radar import webapp
from paper_radar.core import DEFAULTS,connect,save_papers
from paper_radar.models import Paper


def test_abstract_quality_background_start_and_pilot_report_http(tmp_path,monkeypatch):
    path=tmp_path/'papers.sqlite3'
    with connect(path) as db:
        save_papers(db,[Paper(paper_id='arxiv:example',title='Neural operator mesh generalization',
          abstract='',authors='Researcher',published='2026-11-11',updated='2026-11-11',
          category='cs.LG',url='https://arxiv.org/abs/2611.12345',pdf_url='',source='arxiv',doi='')])
    @contextmanager
    def local_db():
        with connect(path) as db:
            with db:yield db
    class CapturingRuntime:
        last_seen=0
        def __init__(self):self.called=[]
        def state(self):return {'busy':False}
        def start(self,mode,args):self.called.append((mode,args))
    fake=CapturingRuntime()
    monkeypatch.setattr(webapp,'DB_FILE',path)
    monkeypatch.setattr(webapp,'open_database',local_db)
    monkeypatch.setattr(webapp,'runtime',fake)
    monkeypatch.setattr(webapp,'read_config',lambda:{**DEFAULTS,'active_topic_id':'default'})
    monkeypatch.setattr(webapp,'config_health',lambda p:{'read_only':False})
    server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
    server.token='v151-token'
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    def post(body,token=True):
        header={'Content-Type':'application/json','Origin':base}
        if token:header['X-Local-Token']=server.token
        return urlopen(Request(base+'/api/abstracts/start',data=json.dumps(body).encode(),headers=header),timeout=10)
    try:
        with urlopen(base+'/api/abstracts/quality',timeout=10) as r:
            quality=json.load(r)
            assert quality['total']==1 and quality['needs_refresh']==1
            assert quality['by_quality']['missing']==1
        with pytest.raises(HTTPError) as exc:post({'limit':1},token=False)
        assert exc.value.code==403
        with pytest.raises(HTTPError) as exc:post({'limit':0})
        assert exc.value.code==400
        with post({'limit':60}) as r:
            assert r.status==202 and json.load(r)['mode']=='abstracts'
        assert fake.called==[('abstracts',['60'])]
        with urlopen(base+'/api/pilot/summary',timeout=10) as r:
            data=json.load(r)
            assert data['library_total']==1 and data['pilot_selected_unique']==0
            assert data['pdf_acquisition_rate'] is None
            assert data['field_accuracy_on_reviewed_fields'] is None
            assert data['manual_review_seconds'] is None
            assert data['abstract_quality']['missing']==1
        with urlopen(base+'/api/pilot/export',timeout=10) as r:
            z=zipfile.ZipFile(io.BytesIO(r.read()))
            assert json.loads(z.read('summary.json'))['abstract_quality']['missing']==1
    finally:
        server.shutdown();server.server_close();thread.join(3)
