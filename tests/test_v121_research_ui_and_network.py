# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.21: bounded offline discovery, PDF auth, reader/matrix contracts."""
from __future__ import annotations
import http.client
import json
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread
from urllib.parse import urlencode

import pytest

from paper_radar.sources.supervisor import SourceTimeout,fetch_isolated
from paper_radar.sources.base import fetch_enabled,AllSourcesUnavailable
from paper_radar.agents.contracts import AgentCancelled,AgentDeadlineExceeded
from paper_radar.models import Paper
from paper_radar.public_portal import Portal, make_server
from paper_radar.portal_research import save_pdf, path_for
from paper_radar.synthesis import compare_records


def one_paper():
    return Paper('2401.12345','Verified Research','Short abstract','Author',
                 '2026-11-05','2026-11-05','cs.LG','https://arxiv.org/abs/2401.12345',
                 'https://arxiv.org/pdf/2401.12345')


def test_blocked_source_process_has_wall_clock_timeout():
    start=time.monotonic()
    with pytest.raises(SourceTimeout):
        fetch_isolated('arxiv',{'keywords':['FNO']},timeout_seconds=.35,
                       command=[sys.executable,'-c','import time;time.sleep(20)'])
    assert time.monotonic()-start < 3.0


def test_source_cancellation_kills_subprocess():
    invoked=0
    def checkpoint():
        nonlocal invoked
        invoked+=1
        if invoked>2:
            raise InterruptedError('task cancelled')
    start=time.monotonic()
    with pytest.raises(InterruptedError):
        fetch_isolated('arxiv',{'keywords':['FNO']},timeout_seconds=5,
            checkpoint=checkpoint,command=[sys.executable,'-c','import time;time.sleep(20)'])
    assert time.monotonic()-start<3.0


def test_isolated_source_child_returns_json_without_network():
    papers=fetch_isolated('crossref',{'keywords':[],'max_results':2,'lookback_days':7},timeout_seconds=6)
    assert papers==[]


def test_unavailable_source_skipped_and_healthy_source_survives(monkeypatch):
    calls=[];states=[]
    def fake_source(name,cfg,**kwargs):
        calls.append(name)
        if name=='arxiv':raise SourceTimeout('wall time exhausted')
        return [one_paper()]
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',fake_source)
    cfg={'sources':['arxiv','crossref'],'keywords':['FNO'], 'max_results':5,
         'max_queries_per_source':1,'source_query_timeout_seconds':3,
         'source_total_timeout_seconds':12}
    results=fetch_enabled(cfg,supervised=True,logger=lambda _:None,
                          progress=lambda *v:states.append(v))
    assert len(results)==1 and results[0].paper_id=='2401.12345'
    assert calls==['arxiv','crossref']
    assert cfg['_source_results'][0]['timed_out']
    assert not cfg['_source_results'][0]['ok'] and cfg['_source_results'][1]['ok']
    assert states[-1][0]==2 and '完成' in states[-1][-1]


def test_all_sources_offline_return_diagnosis(monkeypatch):
    def offline(name,cfg,**kwargs):raise SourceTimeout('unreachable')
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',offline)
    cfg={'sources':['arxiv'],'keywords':['FNO'],'max_results':4,
         'source_query_timeout_seconds':3,'source_total_timeout_seconds':6}
    with pytest.raises(AllSourcesUnavailable,match='所有文献来源') as err:
        fetch_enabled(cfg,supervised=True,logger=lambda _:None)
    assert len(err.value.source_results)==1
    assert len(cfg['_source_results'])==1
    assert cfg['_source_results'][0]['queries'][0]['timed_out']


class HttpClient:
    def __init__(self, port):
        self.port=port;self.cookie='';self.csrf=''
    def request(self,method,path,data=None,extra=None):
        hdr={'Host':f'127.0.0.1:{self.port}'}
        if self.cookie:hdr['Cookie']=self.cookie
        body=None
        if method=='POST':
            hdr.update({'Content-Type':'application/json','Origin':f'http://127.0.0.1:{self.port}',
                        'X-Portal-Action':'1'})
            if self.csrf:hdr['X-CSRF-Token']=self.csrf
            body=json.dumps(data or {}).encode()
        hdr.update(extra or {})
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        conn.request(method,path,body=body,headers=hdr)
        res=conn.getresponse();raw=res.read();status=res.status
        headers=dict(res.getheaders());conn.close()
        cookie=headers.get('Set-Cookie')
        if cookie:self.cookie=cookie.split(';',1)[0]
        try:obj=json.loads(raw)
        except ValueError:obj={}
        if isinstance(obj,dict) and obj.get('csrf'):self.csrf=obj['csrf']
        return status,raw,headers,obj
    def register(self,name):
        code,raw,h,obj=self.request('POST','/api/public/register',
            {'username':name,'password':'long testing password 123456'})
        assert code==201,obj
        return obj['user']['id']


@pytest.fixture
def portal_service(tmp_path):
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    server=make_server('127.0.0.1',0,portal)
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:yield portal,server.server_port
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def seed_pdf(portal,uid,pid='2401.12345'):
    with portal.workspace(uid) as conn:
        conn.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',
            (pid,'Evidence paper','Experimental results','2026-11-05',
             'https://arxiv.org/abs/2401.12345','','now'))
        conn.execute('INSERT INTO topic_papers VALUES (?,?)',('default',pid))
    content=b'%PDF-1.4\n'+b'valid owner bytes\n'*20+b'%%EOF\n'
    save_pdf(portal,uid,'default',pid,content)
    return content


def pdf_path(pid='2401.12345',topic='default'):
    return '/api/public/pdf/view?'+urlencode({'topic_id':topic,'paper_id':pid})


def test_pdf_reader_requires_auth_and_member(portal_service):
    portal,port=portal_service
    alice=HttpClient(port);bob=HttpClient(port)
    uid=alice.register('ReaderFNO1');bob.register('ReaderFNO2')
    raw=seed_pdf(portal,uid)
    unauth=HttpClient(port)
    assert unauth.request('GET',pdf_path())[0]==401
    assert bob.request('GET',pdf_path())[0]==400
    status,content,headers,_=alice.request('GET',pdf_path())
    assert status==200 and content==raw
    assert headers['Content-Type']=='application/pdf'
    assert headers['X-Frame-Options']=='SAMEORIGIN'
    assert 'frame-ancestors \'self\'' in headers['Content-Security-Policy']
    assert headers['Cache-Control']=='no-store'


def test_pdf_range_and_invalid_range(portal_service):
    portal,port=portal_service
    client=HttpClient(port);uid=client.register('ReaderFNO3')
    raw=seed_pdf(portal,uid)
    status,blob,headers,_=client.request('GET',pdf_path(),extra={'Range':'bytes=5-18'})
    assert status==206 and blob==raw[5:19]
    assert headers['Content-Range']==f'bytes 5-18/{len(raw)}'
    assert client.request('GET',pdf_path(),extra={'Range':'bytes=500000-500001'})[0]==416
    assert client.request('GET',pdf_path(),extra={'Range':'bytes=-1'})[0]==416


def test_replaced_pdf_does_not_serve_stale_blob(portal_service):
    portal,port=portal_service
    client=HttpClient(port);uid=client.register('ReaderFNO4')
    seed_pdf(portal,uid)
    path_for(portal,uid,'2401.12345').write_bytes(b'%PDF-1.4\n'+b'other bytes'*50)
    code,_,_,_=client.request('GET',pdf_path())
    assert code==404


def test_report_matrix_preserves_source_and_baseline_warnings():
    rows=[]
    for pid,baseline in [('a','Model-A'),('b','Model-B')]:
        rows.append({'paper_id':pid,'title':pid,'metric':'L2 error','dataset':'Darcy',
          'value':'0.2','kind':'metric','setting':'64x64','baseline':baseline,'page':3,
          'quote':'Experimental evidence: Darcy L2 error 0.2 on 64x64 baseline '+baseline,
          'provenance':'pdf_sha256_verified','published':'2024-01-01'})
    result=compare_records(rows)
    assert len(result['comparisons'])==1
    assert any('对照基线不一致' in flag for flag in result['comparisons'][0]['flags'])
    assert result['comparisons'][0]['records'][0]['page']==3


def test_ui_contract_for_embedded_pdf_and_matrix():
    root=Path(__file__).resolve().parent.parent/'paper_radar/static'
    html=(root/'portal.html').read_text()
    js=(root/'portal.js').read_text()
    css=(root/'portal.css').read_text()
    local=(root/'index.html').read_text()
    assert 'id="comparisonMatrix"' in html and 'id="comparisonFilter"' in html
    assert 'id="fulltextPdfViewer"' in local
    assert 'pdf-evidence-layout' in js and 'matrix-unverified' in js
    assert 'pdf-reader-frame' in css
    assert 'textContent' in js and 'renderComparisonMatrix' in js


@pytest.mark.parametrize('signal',[AgentCancelled,AgentDeadlineExceeded])
def test_cancel_and_agent_deadline_not_hidden_as_source_failure(monkeypatch,signal):
    def blocked(_name,_config,**kwargs):
        raise signal('task stopped')
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',blocked)
    cfg={'sources':['arxiv','crossref'],'keywords':['FNO'],'max_results':4,
         'source_query_timeout_seconds':4,'source_total_timeout_seconds':12}
    with pytest.raises(signal,match='task stopped'):
        fetch_enabled(cfg,supervised=True,logger=lambda _:None)
    assert not cfg.get('_source_results')


def test_local_task_failure_converges_and_exposes_provider_diagnostics(monkeypatch):
    from paper_radar import webapp, core
    final=[]
    monkeypatch.setattr(webapp,'write_status',lambda data:final.append(dict(data)))
    monkeypatch.setattr(webapp,'record_run',lambda *args,**kwargs:None)
    def offline(*_args,**_kwargs):
        raise AllSourcesUnavailable('所有文献来源均无法连接',[{'name':'arxiv','ok':False,
            'count':0,'timed_out':True,'queries':[]}])
    monkeypatch.setattr(webapp,'run_cycle',offline)
    runner=webapp.Runtime()
    monkeypatch.setattr(runner,'push',lambda txt:None)
    cfg={**core.DEFAULTS,'active_topic_id':'default'}
    runner._worker('search',[],cfg)
    assert final[-1]['state']=='failed' and final[-1]['progress']['percent']==100
    assert final[-1]['result']['source_results'][0]['timed_out']
    assert not runner.busy
    assert runner.last_result['source_results'][0]['name']=='arxiv'


def test_responsive_matrix_has_bounded_grid_column():
    css=(Path(__file__).resolve().parent.parent/'paper_radar/static/portal.css').read_text()
    assert '.portal-page{grid-template-columns:minmax(0,1fr)}' in css
    assert '.matrix-scroll,.report-table-scroll{min-width:0;max-width:100%;overflow-x:auto' in css
