# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import io,json,zipfile
from paper_radar import webapp
from paper_radar.core import DEFAULTS,connect
from paper_radar.evidence_batch import create_run,process_run,list_run,review_candidate
from paper_radar.pilot_audit import QUALITY_FIELDS, COMPATIBILITY_FIELDS
from test_v150_evidence_batch import fixture
import pytest


def test_live_http_pilot_summary_record_review_and_zip(tmp_path,monkeypatch):
    db,path=fixture(tmp_path)
    rid=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',['evidence-0','evidence-1'])['run_id']
    process_run(path,rid,DEFAULTS)
    record=list_run(db,'default',rid,db_path=path)['candidates'][0]
    result=review_candidate(db,'default',record['id'],revision=record['revision'],decision='approved',
            metric='RMSE',value='0.0123',dataset='MeshBench',baseline='baseline',setting='split A',
            review_note='已经在 PDF 第 1 页核实实验字段与指标原文。',expected_sha256=record['pdf_sha256'],db_path=path)
    db.close()
    @contextmanager
    def local_db():
        c=connect(path)
        try:
            with c:yield c
        finally:c.close()
    monkeypatch.setattr(webapp,'DB_FILE',path)
    monkeypatch.setattr(webapp,'open_database',local_db)
    monkeypatch.setattr(webapp,'read_config',lambda:{**DEFAULTS,'active_topic_id':'default'})
    monkeypatch.setattr(webapp,'config_health',lambda p:{'read_only':False})
    server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
    server.token='v1501-pilot-local-token'
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    def post(path_,obj,csrf=True):
        headers={'Content-Type':'application/json','Origin':base}
        if csrf:headers['X-Local-Token']=server.token
        return urlopen(Request(base+path_,data=json.dumps(obj).encode(),headers=headers),timeout=8)
    try:
        with urlopen(base+'/api/pilot/summary') as response:
            assert json.load(response)['pilot_selected_unique']==2
        payload={'candidate_id':record['id'],'revision':result['revision'],'active_seconds':112.5,
            'labels':{k:'correct' for k in QUALITY_FIELDS},
            'original_fields':{k:'x' for k in QUALITY_FIELDS},
            'corrected_fields':{k:'y' for k in QUALITY_FIELDS}}
        with pytest.raises(HTTPError) as forbidden:post('/api/pilot/record-review',payload,csrf=False)
        assert forbidden.value.code==403
        with post('/api/pilot/record-review',payload) as response:
            assert json.load(response)['saved'] is True
        with urlopen(base+'/api/pilot/summary') as response:
            stats=json.load(response)
            assert stats['field_accuracy_on_reviewed_fields']==1.0
            assert stats['mean_active_review_seconds']==112.5
        with urlopen(base+'/api/pilot/export') as response:
            assert response.headers.get_content_type()=='application/zip'
            z=zipfile.ZipFile(io.BytesIO(response.read()))
            assert json.loads(z.read('summary.json'))['pilot_selected_unique']==2
    finally:
        server.shutdown();server.server_close();thread.join(3)
