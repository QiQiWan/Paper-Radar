"""v1.40.2 source diagnostics: traceability without storing secrets or papers."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib import request
from urllib.error import URLError
from unittest.mock import patch
import io
import json
import socket
import sys
import zipfile

import pytest

from paper_radar.sources.retrieval_trace import (
    RetrievalTrace, ACTIVE_TRACE, read_trace, export_bundle, sanitized_event, latest_id
)
from paper_radar.sources.base import fetch_enabled, AllSourcesUnavailable
from paper_radar.sources.supervisor import fetch_isolated
from paper_radar.sources.failures import SourceQueryError
from paper_radar.sources.transport_trace import observe_urllib
from paper_radar import webapp
from paper_radar.models import Paper

SECRET = 'sk-SUPER-SECRET-ABC'


def _paper():
    return Paper('2601.00001', 'SECRET ARTICLE TITLE', 'Secret abstract', 'Anon',
                 '2026-11-01', '2026-11-01', 'cs.LG', 'https://arxiv.org/abs/2601.00001', '')


def test_only_whitelisted_metadata_can_enter_trace_or_bundle(tmp_path):
    t = RetrievalTrace(tmp_path)
    t.record('query_error', source='arxiv', query_index=1, error_code='DNS_ERROR',
             password=SECRET, api_key=SECRET, keywords=['very-private-keyword'],
             host='https://example.com/?api_key='+SECRET,
             error='Raw exception: '+SECRET)
    t.close('failed')
    raw=t.path.read_text('utf-8')
    assert SECRET not in raw and 'very-private-keyword' not in raw
    assert 'example.com' not in raw
    saved=read_trace(tmp_path,t.run_id)
    assert saved['error_count']==1
    with zipfile.ZipFile(io.BytesIO(export_bundle(tmp_path,t.run_id))) as z:
        assert sorted(z.namelist())==['manifest.json','retrieval.jsonl','sources.json']
        content=b''.join(z.read(x) for x in z.namelist())
        assert SECRET.encode() not in content
        assert b'very-private-keyword' not in content
        assert b'private' not in content


def test_invalid_run_id_or_symlink_cannot_read_any_other_file(tmp_path):
    record=RetrievalTrace(tmp_path)
    record.close('success')
    assert latest_id(tmp_path)==record.run_id
    with pytest.raises(ValueError):read_trace(tmp_path,'../settings.json')
    (record.path).unlink()
    try:
        (record.path).symlink_to(tmp_path/'outside-file')
    except OSError:
        pytest.skip('Current Windows account cannot create symlinks')
    with pytest.raises(ValueError):read_trace(tmp_path,record.run_id)


def test_urllib_network_failure_is_captured_as_safe_typed_event(monkeypatch):
    events=[]
    def blocked(*a,**k):
        raise URLError(socket.gaierror(-2,'DNS failed SECRET '+SECRET))
    monkeypatch.setattr(request,'urlopen',blocked)
    with observe_urllib(events):
        with pytest.raises(URLError):request.urlopen('https://api.crossref.org/works?api_key='+SECRET,timeout=2)
    assert [x['event'] for x in events]==['http_start','http_error']
    assert events[1]['error_code']=='DNS_ERROR'
    assert events[1]['host']=='api.crossref.org'
    assert SECRET not in str(events)


def test_urllib_success_captures_connect_and_read_without_response_body(monkeypatch):
    class FakeResponse:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self,*args,**kwargs):return b'SECRET_BODY_'+SECRET.encode()
    monkeypatch.setattr(request,'urlopen',lambda *a,**k:FakeResponse())
    rows=[]
    with observe_urllib(rows):
        with request.urlopen('https://export.arxiv.org/api/query?secret='+SECRET,timeout=3) as r:
            assert r.read().startswith(b'SECRET_BODY')
    assert [x['event'] for x in rows]==['http_start','http_connected','http_read']
    assert 'SECRET' not in str(rows)


def test_child_protocol_error_is_typed_and_does_not_echo_stderr(tmp_path):
    trace=[]
    with pytest.raises(SourceQueryError) as err:
        fetch_isolated('arxiv',{'keywords':['private topic']},timeout_seconds=4,
            command=[sys.executable,'-c',f'import sys;sys.stdin.buffer.read();sys.stderr.write("{SECRET}");print("oops")'],
            trace_hook=trace.append)
    assert err.value.code=='SOURCE_CHILD_PROTOCOL'
    assert SECRET not in str(trace)
    assert any(e['event']=='child_error' for e in trace)


def test_child_sanitizes_transport_trace_even_if_child_injects_fields():
    payload=json.dumps({'ok':True,'papers':[], 'proxy_configured':True,
         'transport_trace':[{'event':'http_error','host':'api.crossref.org',
           'error_code':'RATE_LIMIT','http_status':429,'private':SECRET,'query':SECRET}]})
    source='import sys;sys.stdin.buffer.read();print('+repr(payload)+')'
    received=[]
    papers=fetch_isolated('crossref',{'keywords':['SECRET']},timeout_seconds=4,
        command=[sys.executable,'-c',source],trace_hook=received.append)
    assert papers==[]
    assert any(x.get('error_code')=='RATE_LIMIT' for x in received)
    assert SECRET not in str(received)


def test_failing_and_successful_sources_record_separate_events(tmp_path,monkeypatch):
    def fake(name, cfg, **kwargs):
        if name=='arxiv':raise SourceQueryError('DNS_ERROR')
        return [_paper()]
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',fake)
    t=RetrievalTrace(tmp_path)
    token=ACTIVE_TRACE.set(t)
    cfg={'sources':['arxiv','crossref'],'keywords':['private topic'],
        'max_results':5,'max_queries_per_source':1,
        'source_query_timeout_seconds':6,'source_total_timeout_seconds':20}
    try:
        papers=fetch_enabled(cfg,logger=lambda _x:None,supervised=True)
    finally:
        ACTIVE_TRACE.reset(token)
        t.close('partial',count=1)
    assert len(papers)==1
    events=read_trace(tmp_path,t.run_id,limit=600)['events']
    assert any(x['event']=='query_error' and x.get('source')=='arxiv' for x in events)
    assert any(x['event']=='query_finish' and x.get('source')=='crossref' for x in events)
    assert all('keyword' not in x for x in events)
    assert 'private topic' not in t.path.read_text('utf-8')


def test_every_source_failure_still_produces_downloadable_trace(tmp_path,monkeypatch):
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',
                        lambda *_a,**_kw: (_ for _ in ()).throw(SourceQueryError('PROXY_ERROR')))
    t=RetrievalTrace(tmp_path)
    token=ACTIVE_TRACE.set(t)
    try:
        with pytest.raises(AllSourcesUnavailable):
            fetch_enabled({'sources':['arxiv'],'keywords':['Private research'],
                'max_results':2,'source_query_timeout_seconds':6,
                'source_total_timeout_seconds':6}, logger=lambda _:None, supervised=True)
    finally:
        ACTIVE_TRACE.reset(token)
        t.close('failed')
    assert read_trace(tmp_path,t.run_id)['error_count'] >=1


def test_http_trace_json_and_export_are_actual_local_routes(tmp_path,monkeypatch):
    t=RetrievalTrace(tmp_path)
    t.record('query_error',source='arxiv',error_code='SOURCE_TIMEOUT')
    t.close('failed')
    monkeypatch.setattr(webapp,'APP_ROOT',tmp_path)
    server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
    server.token='diagnostic-test'
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'
        with request.urlopen(base+'/api/retrieval-trace?run_id='+t.run_id,timeout=5) as resp:
            data=json.load(resp)
        assert data['run_id']==t.run_id and data['error_count']>=1
        with request.urlopen(base+'/api/retrieval-trace/export?run_id='+t.run_id,timeout=5) as resp:
            payload=resp.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            assert 'retrieval.jsonl' in z.namelist()
    finally:
        server.shutdown();worker.join(timeout=4);server.server_close()


def test_ui_has_clear_diagnosis_action_and_no_secret_log_collect():
    root=Path(__file__).resolve().parents[1]
    html=(root/'paper_radar/static/index.html').read_text('utf-8')
    js=(root/'paper_radar/static/app.js').read_text('utf-8')
    assert 'id="retrievalTraceDetails"' in html
    assert 'id="downloadRetrievalTrace"' in html
    assert 'function loadRetrievalTrace' in js
    assert 'retrievalTraceSummary' in js


def test_nonzero_child_exit_cannot_claim_success():
    source='import sys;sys.stdin.buffer.read();print(\'{"ok":true,"papers":[]}\');sys.exit(5)'
    seen=[]
    with pytest.raises(SourceQueryError) as err:
        fetch_isolated('arxiv',{},timeout_seconds=4,
                       command=[sys.executable,'-c',source],trace_hook=seen.append)
    assert err.value.code=='SOURCE_CHILD_FAILED'
    assert any(row.get('exit_code')==5 for row in seen)


def test_real_runtime_failed_search_retains_trace_id_and_error_result(tmp_path,monkeypatch):
    from paper_radar.core import DEFAULTS
    states=[]
    monkeypatch.setattr(webapp,'APP_ROOT',tmp_path)
    monkeypatch.setattr(webapp,'write_status',states.append)
    monkeypatch.setattr(webapp,'record_run',lambda *a,**k:None)
    def failing_cycle(cfg, *, logger, progress):
        trace=ACTIVE_TRACE.get()
        assert trace is not None
        trace.record('source_start',source='arxiv',source_index=1,source_total=1)
        trace.record('query_error',source='arxiv',error_code='DNS_ERROR')
        raise AllSourcesUnavailable('所有文献来源均无法连接',[{'name':'arxiv',
            'ok':False,'partial':False,'error_code':'DNS_ERROR','count':0}])
    monkeypatch.setattr(webapp,'run_cycle',failing_cycle)
    runtime=webapp.Runtime()
    cfg={**DEFAULTS,'llm_data_consent':False}
    runtime._worker('search',[],cfg)
    assert states[-1]['state']=='failed'
    run_id=states[-1]['diagnostic_run_id']
    assert run_id
    assert runtime.last_result['source_results'][0]['error_code']=='DNS_ERROR'
    assert read_trace(tmp_path,run_id)['error_count']>=1
    assert not runtime.busy


def test_task_history_keeps_trace_reference_but_not_search_terms(tmp_path):
    from paper_radar.history import record_run,recent_runs
    trace=RetrievalTrace(tmp_path)
    trace.close('partial')
    history_file=tmp_path/'runs.sqlite3'
    record_run('default','search','partial',result={
        'diagnostic_run_id':trace.run_id,'retrieved':0,'new':0,
        'secret_keyword':SECRET,
        'source_results':[{'name':'arxiv','ok':False,'count':0,
                           'error_code':'UNKNOWN '+SECRET,'queries':[{'keyword':SECRET}]}]},
        path=history_file)
    item=recent_runs(path=history_file)[0]
    assert item['summary']['diagnostic_run_id']==trace.run_id
    assert item['summary']['sources'][0]['error_code']==''
    assert SECRET not in str(item)


def test_diagnostic_only_folder_does_not_block_fresh_config(tmp_path):
    from paper_radar.core import DEFAULTS, validate_config
    from paper_radar.config_safety import initialize_fresh_workspace
    trace=RetrievalTrace(tmp_path)
    trace.close('failed')
    settings=tmp_path/'settings.json'
    assert initialize_fresh_workspace(settings,DEFAULTS,validate_config)
    assert settings.is_file()


def test_unknown_existing_log_prevents_accidental_config_regeneration(tmp_path):
    from paper_radar.core import DEFAULTS, validate_config
    from paper_radar.config_safety import initialize_fresh_workspace
    trace=RetrievalTrace(tmp_path)
    trace.close('failed')
    (tmp_path/'logs'/'scheduled.log').write_text('existing worker activity','utf-8')
    assert not initialize_fresh_workspace(tmp_path/'settings.json',DEFAULTS,validate_config)
    assert not (tmp_path/'settings.json').exists()
