"""v1.40 retrieval reliability: regression for all-provider timeout failure."""
from __future__ import annotations

import io
import json
import socket
import sys
import time
from urllib.error import HTTPError, URLError

import pytest

from paper_radar.core import DEFAULTS, validate_config
from paper_radar.models import Paper
from paper_radar.sources.base import fetch_enabled, AllSourcesUnavailable
from paper_radar.sources.diagnostics import probe_sources
from paper_radar.sources.failures import code_for_exception, SourceQueryError
from paper_radar.sources.supervisor import fetch_isolated
from paper_radar.sources.request_policy import request_timeout


def paper(n):
    return Paper(paper_id=f'2601.{n:05d}', title='Neural operator work',
                 authors='Researcher', abstract='Test',published='2026-11-01',
                 updated='2026-11-01',category='cs.LG',
                 url='https://arxiv.org/abs/2601.00001',pdf_url='',source='arxiv')


def test_legacy_timeout_defaults_are_automatically_upgraded():
    config = validate_config({**DEFAULTS,'source_query_timeout_seconds':10,
                              'source_total_timeout_seconds':75})
    assert config['source_query_timeout_seconds'] >= 30
    assert config['source_total_timeout_seconds'] >= 180
    custom = validate_config({**DEFAULTS,'source_query_timeout_seconds':45,
                              'source_total_timeout_seconds':240})
    assert custom['source_query_timeout_seconds'] == 45
    assert custom['source_total_timeout_seconds'] == 240


def test_socket_deadline_reserves_windows_startup_time():
    assert request_timeout({'_source_wall_timeout_seconds':35},30) <= 31
    assert request_timeout({'_source_wall_timeout_seconds':10},30) == 6
    assert request_timeout({},30) == 30


def test_error_codes_do_not_expose_provider_url_or_credentials():
    from paper_radar.sources.failures import MESSAGES
    assert code_for_exception(HTTPError('https://secret?api_key=token',429,'too fast',{},None)) == 'RATE_LIMIT'
    assert code_for_exception(HTTPError('https://secret?api_key=token',403,'Forbidden',{},None)) == 'AUTH_REQUIRED'
    assert code_for_exception(URLError(socket.gaierror(-2,'name lookup'))) == 'DNS_ERROR'
    assert code_for_exception(TimeoutError('https://secret?api_key=token')) == 'SOURCE_TIMEOUT'
    assert 'token' not in str(SourceQueryError('AUTH_REQUIRED'))
    assert set(MESSAGES) >= {'RATE_LIMIT','AUTH_REQUIRED','SOURCE_TIMEOUT','DNS_ERROR','TLS_ERROR'}


def test_child_returns_typed_failure_without_leaking_remote_url():
    code = ('import sys,json;sys.stdin.buffer.read();'
            'print(json.dumps({"ok":False,"error_code":"RATE_LIMIT",'
            '"error":"SECRET https://example.org?key=private"}))')
    with pytest.raises(SourceQueryError) as ex:
        fetch_isolated('crossref',{},timeout_seconds=3,
                       command=[sys.executable,'-c',code])
    assert ex.value.code == 'RATE_LIMIT'
    assert 'SECRET' not in str(ex.value)


def test_first_source_timeout_does_not_starve_later_sources(monkeypatch):
    budgets = {}
    def synthetic(name,cfg,**kwargs):
        budgets[name] = kwargs['timeout_seconds']
        if name=='arxiv':
            # A real subprocess reaches its hard deadline and is terminated.
            raise TimeoutError('endpoint slow')
        return [paper(100)]
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',synthetic)
    cfg = {**DEFAULTS,'sources':['arxiv','openalex','crossref'],
           'keywords':['operator learning'], 'max_results':5,
           'max_queries_per_source':1,'source_query_timeout_seconds':35,
           'source_total_timeout_seconds':60}
    result=fetch_enabled(cfg, supervised=True, logger=lambda *_: None)
    assert len(result) == 2
    assert list(budgets)==['arxiv','openalex','crossref']
    assert all(2 <= x <= 35 for x in budgets.values())
    assert cfg['_source_results'][0]['error_code']=='SOURCE_UNAVAILABLE' or cfg['_source_results'][0]['error_code']=='SOURCE_TIMEOUT'


def test_source_faults_are_separate_and_explainable(monkeypatch):
    def source(name,cfg,**kwargs):
        if name=='arxiv':raise SourceQueryError('RATE_LIMIT')
        if name=='openalex':raise SourceQueryError('DNS_ERROR')
        return [paper(122)]
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',source)
    cfg={**DEFAULTS,'sources':['arxiv','openalex','crossref'],
         'keywords':['FNO'],'max_queries_per_source':1,'max_results':4}
    result=fetch_enabled(cfg,supervised=True,logger=lambda *_:None)
    assert result
    assert [r['error_code'] for r in cfg['_source_results'][:2]] == ['RATE_LIMIT','DNS_ERROR']
    assert cfg['_source_results'][-1]['ok']


def test_connectivity_probe_is_allowlisted_on_demand():
    opened=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*_):return None
        def read(self,n):return b'{}'
    def opener(req,timeout):
        opened.append((req.full_url,timeout))
        return Response()
    info=probe_sources(['arxiv','crossref','invalid','arxiv'],opener=opener,timeout=3)
    assert len(info['checks'])==2
    assert len(opened)==2
    assert info['checks'][0]['ok'] and info['checks'][1]['ok']
    assert not any('invalid' in url for url,_ in opened)


def test_connectivity_probe_safely_reports_dns_and_limit():
    def fail(req,timeout):
        if 'arxiv' in req.full_url:
            raise URLError(socket.gaierror(-2,'host not found'))
        raise HTTPError(req.full_url,429,'limit',{},None)
    rows=probe_sources(['arxiv','crossref'],opener=fail)['checks']
    assert [x['code'] for x in rows] == ['DNS_ERROR','RATE_LIMIT']
    assert not any('https://' in x['message'] for x in rows)


def test_source_cache_key_changes_with_incremental_window_and_watchlist():
    from paper_radar.source_cache import cache_key
    baseline={'keywords':['neural operator'],'max_results':20,
              '_window_start':'2026-11-07T00:00:00+00:00',
              '_window_end':'2026-11-08T00:00:00+00:00',
              '_watch_arxiv_ids':['2501.12345']}
    first=cache_key('arxiv',baseline,clock=1794096000)
    assert cache_key('arxiv',{**baseline,'_window_end':'2026-11-09T00:00:00+00:00'},
                     clock=1794096000)!=first
    assert cache_key('arxiv',{**baseline,'_watch_arxiv_ids':['2501.99999']},
                     clock=1794096000)!=first
    assert cache_key('arxiv',baseline,clock=1794096000)==first


def test_arxiv_followed_revision_timeout_preserves_main_results(monkeypatch):
    from paper_radar.sources.arxiv import fetch_arxiv
    from paper_radar.sources.supervisor import SourceBatch
    import urllib.error
    feed=b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
       <entry><id>https://arxiv.org/abs/2601.12345v1</id><title>Neural operators</title>
       <summary>Test</summary><published>2026-11-07T00:00:00Z</published>
       <updated>2026-11-07T00:00:00Z</updated></entry></feed>'''
    requests=[]
    class Response(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*_):return None
    def open_url(req,timeout):
        requests.append(req.full_url)
        if len(requests)==1:return Response(feed)
        raise TimeoutError('late revision request failed')
    monkeypatch.setattr('paper_radar.sources.arxiv.request.urlopen',open_url)
    monkeypatch.setattr('paper_radar.sources.arxiv.time.sleep',lambda *_:None)
    rows=fetch_arxiv({**DEFAULTS,'max_results':4,'keywords':['neural'],
           '_watch_arxiv_ids':['2501.99999']},logger=lambda *_:None)
    assert isinstance(rows,SourceBatch) and rows.partial
    assert len(rows)==1 and rows[0].paper_id=='2601.12345'


def test_partial_source_keeps_checkpoint_pending(monkeypatch):
    from paper_radar.sources.supervisor import SourceBatch
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',lambda *_args,**_kw:
                        SourceBatch([paper(321)],partial=True,warning_code='SOURCE_PARTIAL'))
    cfg={**DEFAULTS,'sources':['arxiv'],'keywords':['operator'],
         'max_queries_per_source':1,'max_results':10}
    found=fetch_enabled(cfg,supervised=True,logger=lambda *_:None)
    assert len(found)==1 and cfg['_source_results'][0]['partial']
    assert cfg['_source_results'][0]['error_code']=='SOURCE_PARTIAL'


def test_local_connectivity_route_with_mocked_probe(tmp_path,monkeypatch):
    import http.client
    from threading import Thread
    from http.server import ThreadingHTTPServer
    from paper_radar.sources import diagnostics
    monkeypatch.setattr(diagnostics,'probe_sources',lambda sources:{'checks':[
        {'name':'arxiv','ok':False,'code':'SOURCE_TIMEOUT','message':'测试超时','seconds':0.01}],
        'proxy_configured':False})
    from paper_radar import webapp
    server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
    server.daemon_threads=True
    server.token='network-test-token'
    runner=Thread(target=server.serve_forever,daemon=True);runner.start()
    try:
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        conn.request('GET','/api/source-connectivity')
        response=conn.getresponse()
        data=json.loads(response.read())
        assert response.status==200 and data['checks'][0]['code']=='SOURCE_TIMEOUT'
        conn.close()
    finally:server.shutdown();server.server_close();runner.join(timeout=6)


def test_elapsed_provider_deadline_reserves_time_for_later_sources(monkeypatch):
    """One slow provider must not consume the remaining two source slots."""
    import paper_radar.sources.base as base
    class FakeClock:
        value=100.0
        def __call__(self):return self.value
    clock=FakeClock()
    monkeypatch.setattr(base,'monotonic',clock)
    budgets={}
    def synthetic(name,cfg,**kwargs):
        allotted=kwargs['timeout_seconds']
        budgets[name]=allotted
        clock.value += allotted-0.05  # emulate nearly the full wall deadline
        if name=='arxiv':raise SourceQueryError('SOURCE_TIMEOUT')
        return [paper(90 if name=='openalex' else 91)]
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',synthetic)
    cfg={**DEFAULTS,'sources':['arxiv','openalex','crossref'],
         'keywords':['neural operator'], 'max_results':4,
         'max_queries_per_source':1,'source_query_timeout_seconds':35,
         'source_total_timeout_seconds':60}
    result=fetch_enabled(cfg,supervised=True,logger=lambda *_:None)
    assert len(result)==2
    assert list(budgets)==['arxiv','openalex','crossref']
    assert all(18 <= budget <= 21 for budget in budgets.values()),budgets


def test_optional_arxiv_revision_check_never_expires_successful_main_query(monkeypatch):
    """If main search used most of its budget, return it before ID polling."""
    from paper_radar.sources.arxiv import fetch_arxiv
    import io
    import paper_radar.sources.arxiv as arxiv
    tick={'t':100.}
    monkeypatch.setattr(arxiv,'_monotonic',lambda:tick['t'])
    monkeypatch.setattr(arxiv.time,'sleep',lambda *_:None)
    feed=b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>https://arxiv.org/abs/2601.12345v1</id><title>Neural operator</title>
      <summary>Benchmark</summary><published>2026-11-07T00:00:00Z</published>
      <updated>2026-11-07T00:00:00Z</updated></entry></feed>'''
    class Response(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*_):return None
    calls=[]
    def network(req,timeout):
        calls.append(req.full_url)
        tick['t'] += 12.0
        return Response(feed)
    monkeypatch.setattr(arxiv.request,'urlopen',network)
    cfg={**DEFAULTS,'max_results':4,'keywords':['operator'],
         '_watch_arxiv_ids':['2501.11111'],'_source_wall_timeout_seconds':17}
    papers=fetch_arxiv(cfg,logger=lambda *_:None)
    assert len(papers)==1
    assert papers.partial is True and papers.warning_code=='SOURCE_PARTIAL'
    assert len(calls)==1  # costly secondary API request is postponed


def test_openalex_second_page_failure_keeps_first_page(monkeypatch):
    import io
    import paper_radar.sources.openalex as openalex
    class Response(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*_):return None
    pages=[]
    def fetch(req,timeout):
        pages.append(req.full_url)
        if len(pages)==1:
            rows=[{'id':f'https://openalex.org/W{i+1000}',
                   'title':f'Neural operators paper {i}'} for i in range(100)]
            return Response(json.dumps({'results':rows}).encode())
        raise TimeoutError('second page slow')
    monkeypatch.setattr(openalex.request,'urlopen',fetch)
    monkeypatch.setattr(openalex.time,'sleep',lambda *_:None)
    result=openalex.OpenAlexSource().fetch({**DEFAULTS,
        'keywords':['neural operators'],'max_results':101}, logger=lambda *_:None)
    assert len(result)==100
    assert result.partial is True and result.warning_code=='SOURCE_PARTIAL'
    assert len(pages)>=2


def test_crossref_second_page_failure_keeps_first_page(monkeypatch):
    import io
    import paper_radar.sources.crossref as crossref
    class Response(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*_):return None
    calls=[]
    def fetch(req,timeout):
        calls.append(req.full_url)
        if len(calls)==1:
            items=[{'DOI':f'10.1234/neural-{i}','title':['Neural operator']}
                   for i in range(50)]
            return Response(json.dumps({'message':{'items':items,
                                         'next-cursor':'second-cursor'}}).encode())
        raise TimeoutError('second cursor slow')
    monkeypatch.setattr(crossref.request,'urlopen',fetch)
    monkeypatch.setattr(crossref.time,'sleep',lambda *_:None)
    result=crossref.CrossrefSource().fetch({**DEFAULTS,
        'keywords':['neural operators'],'max_results':51},logger=lambda *_:None)
    assert len(result)==50
    assert result.partial is True and result.warning_code=='SOURCE_PARTIAL'
    assert len(calls)>=2
