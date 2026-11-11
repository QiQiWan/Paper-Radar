# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Windows code-page, JSON IPC, corruption and retrieval fallbacks."""
from __future__ import annotations
import os
import sys
import json
from pathlib import Path

import pytest
from paper_radar.sources.supervisor import fetch_isolated
from paper_radar.sources.failures import SourceQueryError
from paper_radar.sources.base import fetch_enabled
from paper_radar.sources.retrieval_trace import sanitized_event
from paper_radar.models import Paper


def _child(source: str):
    # Actual child code and adapter registration, with no real HTTP traffic.
    script = '''
import sys
from paper_radar.models import Paper
import paper_radar.sources
from paper_radar.sources.base import _REGISTRY
from paper_radar.sources.source_process import main
class LocalSource:
    def fetch(self, config, logger, now=None):
        print("NOISE: third party debug output, must never corrupt IPC")
        return [Paper(paper_id="local-1", title="高维张量 α ∇ 🚀",
            abstract="µm² and Chinese 数值模拟", authors="研究者", published="2026-11-08",
            updated="2026-11-08", category="cs.LG", url="https://example.org/1",
            pdf_url="", source="%s")]
_REGISTRY["%s"] = LocalSource
raise SystemExit(main())
''' % (source, source)
    return [sys.executable, '-c', script]


def test_real_child_protocol_survives_windows_chinese_codepage(monkeypatch):
    monkeypatch.setenv('PYTHONIOENCODING', 'cp936')
    traces=[]
    rows=fetch_isolated('arxiv', {'keywords':['张量'], 'max_results':2},
        command=_child('arxiv'),timeout_seconds=8,trace_hook=traces.append)
    assert len(rows)==1
    assert rows[0].title=='高维张量 α ∇ 🚀'
    assert rows[0].abstract=='µm² and Chinese 数值模拟'
    assert traces[-1]['event']=='child_exit' or any(x['event']=='child_exit' for x in traces)
    assert not any(x['event']=='child_error' for x in traces)


def test_real_child_protocol_survives_non_utf8_console_codepage(monkeypatch):
    monkeypatch.setenv('PYTHONIOENCODING', 'cp1252')
    rows=fetch_isolated('crossref', {'max_results':1},
                        command=_child('crossref'),timeout_seconds=8)
    assert rows[0].authors=='研究者'


def test_legacy_non_utf8_child_classified_correctly(monkeypatch):
    monkeypatch.setenv('PYTHONIOENCODING','cp936')
    code='import sys,json;sys.stdin.buffer.read();print(json.dumps({"ok":True,"papers":[],"extra":"中文"},ensure_ascii=False))'
    records=[]
    with pytest.raises(SourceQueryError) as err:
        fetch_isolated('arxiv',{},command=[sys.executable,'-c',code],
                       timeout_seconds=5,trace_hook=records.append)
    assert err.value.code=='SOURCE_CHILD_ENCODING'
    assert any(x.get('stage')=='decode' for x in records)


def test_legacy_unicode_encode_crash_is_not_masked_by_invalid_json():
    code='import sys;sys.stdin.buffer.read();sys.stderr.write("UnicodeEncodeError: codec\\n");sys.exit(1)'
    with pytest.raises(SourceQueryError) as err:
        fetch_isolated('arxiv',{},command=[sys.executable,'-c',code],timeout_seconds=5)
    assert err.value.code=='SOURCE_CHILD_ENCODING'


def test_empty_child_output_has_distinct_error_and_length():
    code='import sys;sys.stdin.buffer.read();sys.exit(0)'
    records=[]
    with pytest.raises(SourceQueryError) as err:
        fetch_isolated('arxiv',{},command=[sys.executable,'-c',code],
                       timeout_seconds=5,trace_hook=records.append)
    assert err.value.code=='SOURCE_CHILD_EMPTY'
    assert any(x['event']=='child_exit' and x['stdout_bytes']==0 for x in records)


def test_child_protocol_wrong_version_is_rejected():
    obj=json.dumps({'ok':True,'papers':[], 'protocol_version':10})
    script='import sys;sys.stdin.buffer.read();print('+repr(obj)+')'
    with pytest.raises(SourceQueryError) as err:
        fetch_isolated('arxiv',{},command=[sys.executable,'-c',script],timeout_seconds=5)
    assert err.value.code=='SOURCE_CHILD_PROTOCOL'


def test_child_protocol_legacy_ascii_json_keeps_backward_compatibility():
    script='import sys;sys.stdin.buffer.read();print(\'{"ok":true,"papers":[]}\')'
    rows=fetch_isolated('arxiv',{},command=[sys.executable,'-c',script],timeout_seconds=5)
    assert rows==[]


def test_bad_paper_schema_classified_as_protocol_error():
    script='import sys;sys.stdin.buffer.read();print(\'{"ok":true,"papers":[{"title":"incomplete"}]}\')'
    with pytest.raises(SourceQueryError) as err:
        fetch_isolated('arxiv',{},command=[sys.executable,'-c',script],timeout_seconds=5)
    assert err.value.code=='SOURCE_CHILD_PROTOCOL'


def test_raw_stderr_is_never_exported_even_when_child_crashes():
    secret='SUPER_SECRET_DO_NOT_LOG_1403'
    script='import sys;sys.stdin.buffer.read();sys.stderr.write('+repr(secret)+');sys.exit(2)'
    records=[]
    with pytest.raises(SourceQueryError) as err:
        fetch_isolated('arxiv',{},command=[sys.executable,'-c',script],
                       timeout_seconds=5,trace_hook=records.append)
    assert err.value.code=='SOURCE_CHILD_FAILED'
    assert secret not in repr(records)
    assert any(x['event']=='child_exit' and x['stderr_bytes']>=len(secret) for x in records)


def test_known_fatal_child_error_does_not_repeat_all_keywords(monkeypatch):
    calls=[]
    def fake(name, config, **kwargs):
        calls.append(name)
        if name=='crossref':
            return [Paper('test-id','Test title','','','2026-11-08','','','https://example.org','')]
        raise SourceQueryError('SOURCE_CHILD_ENCODING')
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',fake)
    cfg={'sources':['openalex','crossref'], 'keywords':['one','two','three'],
         'max_results':6,'source_query_timeout_seconds':6, 'source_total_timeout_seconds':20}
    result=fetch_enabled(cfg,supervised=True,logger=lambda _line:None)
    assert len(result)==1
    assert calls.count('openalex')==1
    assert calls.count('crossref')>=1
    assert cfg['_source_results'][0]['error_code']=='SOURCE_CHILD_ENCODING'


def test_trace_numeric_size_fields_are_sanitized():
    row=sanitized_event('child_exit',source='arxiv',stdout_bytes=2048,
                        stderr_bytes=100,error_code='SOURCE_CHILD_ENCODING',
                        private_keyword='TOP_SECRET')
    assert row['stdout_bytes']==2048 and row['stderr_bytes']==100
    assert 'private_keyword' not in row


def test_windowed_child_with_missing_python_std_streams(monkeypatch):
    # Emulates sys.stdin/sys.stdout=None in a GUI-frozen subprocess. Popen
    # still supplies OS-level stdin/stdout PIPE descriptors.
    script = '''
import sys
import paper_radar.sources
from paper_radar.sources.base import _REGISTRY
from paper_radar.sources.source_process import main
class F:
    def fetch(self, config, logger, now=None):
        return []
_REGISTRY["crossref"] = F
sys.stdin = None
sys.stdout = None
raise SystemExit(main())
'''
    rows = fetch_isolated('crossref', {'max_results': 1},
        command=[sys.executable, '-c', script], timeout_seconds=8)
    assert rows==[]


def test_public_shared_search_does_not_read_desktop_openalex_key(monkeypatch):
    from paper_radar.sources.credentials import get_credential
    from paper_radar.sources.supervisor import source_child_env
    monkeypatch.setenv('OPENALEX_API_KEY', 'DESKTOP_KEY_SHOULD_NOT_LEAK')
    monkeypatch.delenv('PAPERRADAR_PUBLIC_ALLOW_OPENALEX_API_KEY', raising=False)
    assert get_credential('openalex')=='DESKTOP_KEY_SHOULD_NOT_LEAK'
    public_env=source_child_env('openalex', public_metadata=True)
    assert public_env['PAPERRADAR_PUBLIC_METADATA_CHILD']=='1'
    assert 'OPENALEX_API_KEY' not in public_env
    monkeypatch.setenv('PAPERRADAR_PUBLIC_METADATA_CHILD', '1')
    assert get_credential('openalex')==''
    monkeypatch.setenv('PAPERRADAR_PUBLIC_ALLOW_OPENALEX_API_KEY','1')
    assert get_credential('openalex')=='DESKTOP_KEY_SHOULD_NOT_LEAK'


def test_openalex_metadata_uses_optional_keyring_api_key(monkeypatch):
    from urllib.parse import urlsplit, parse_qs
    from paper_radar.sources.openalex import OpenAlexSource
    import paper_radar.sources.openalex as module
    seen=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self,*args):return b'{"results":[]}'
    def fake_request(req, timeout):
        seen.append(parse_qs(urlsplit(req.full_url).query))
        return Response()
    monkeypatch.setattr(module, 'get_credential', lambda source:'FROM_SECURE_STORE')
    monkeypatch.setattr(module.request, 'urlopen', fake_request)
    result=OpenAlexSource().fetch({'keywords':['materials'], 'lookback_days':7, 'max_results':3}, logger=lambda _:None)
    assert result==[]
    assert seen[0]['api_key']==['FROM_SECURE_STORE']


def test_openalex_key_fields_exist_in_local_settings():
    root=Path(__file__).resolve().parents[1]
    html=(root/'paper_radar/static/index.html').read_text('utf-8')
    js=(root/'paper_radar/static/app.js').read_text('utf-8')
    assert 'id="openalexKey"' in html
    assert 'id="saveOpenAlex"' in html
    assert 'id="openalexState"' in html
    assert "hook('saveOpenAlex'" in js


def test_live_smoke_is_opt_in_and_does_not_run_without_consent():
    from paper_radar.sources.smoke import main
    with pytest.raises(SystemExit) as err:
        main([])
    assert err.value.code==2


def test_live_smoke_exports_only_sanitized_status(monkeypatch,tmp_path):
    from paper_radar.sources import smoke
    def stub(name, config, timeout_seconds, trace_hook):
        assert config['keywords']==['machine learning']
        trace_hook(sanitized_event('child_exit',source=name,stdout_bytes=200))
        return [object()]
    monkeypatch.setattr(smoke,'fetch_isolated',stub)
    output=tmp_path/'smoke.json'
    assert smoke.main(['--live','--sources','arxiv','--output',str(output)])==0
    raw=output.read_text('utf-8')
    assert 'arxiv' in raw and 'child_exit' in raw
    assert 'keywords' not in raw and 'machine learning' not in raw


def test_openalex_connection_probe_uses_saved_key_without_exposing_it(monkeypatch):
    from paper_radar.sources.diagnostics import probe_sources
    import paper_radar.sources.diagnostics as diagnostics
    from urllib.parse import urlsplit, parse_qs
    secret='OPENALEX_PROBE_TEST_PRIVATE_KEY'
    monkeypatch.setattr(diagnostics,'get_credential',lambda source:secret)
    observed=[]
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self,n=0):return b'{}'
    def fake(req,timeout=5):
        observed.append(parse_qs(urlsplit(req.full_url).query))
        return Response()
    outcome=probe_sources(['openalex'],opener=fake)
    assert outcome['checks'][0]['ok']
    assert observed[0]['api_key']==[secret]
    assert secret not in repr(outcome)


def test_openalex_409_credits_expired_is_an_auth_guidance_error():
    from paper_radar.sources.failures import code_for_exception
    from urllib.error import HTTPError
    error=HTTPError('https://api.openalex.org/works?api_key=SECRET',409,'Credits expired',{},None)
    assert code_for_exception(error)=='AUTH_REQUIRED'


@pytest.mark.parametrize('source,module_name',[
    ('arxiv','paper_radar.sources.arxiv'),
    ('openalex','paper_radar.sources.openalex'),
    ('crossref','paper_radar.sources.crossref'),
    ('openreview','paper_radar.sources.openreview'),
    ('openalex_semantic','paper_radar.sources.openalex_semantic'),
])
def test_dns_error_does_not_repeat_expensive_requests(monkeypatch,source,module_name):
    from urllib.error import URLError
    import socket,importlib
    module=importlib.import_module(module_name)
    calls=[]
    def fail(*args,**kwargs):
        calls.append(1)
        raise URLError(socket.gaierror(-2,'name resolution failed'))
    monkeypatch.setattr(module.request,'urlopen',fail)
    monkeypatch.setattr('paper_radar.sources.openalex.get_credential',lambda _: '')
    monkeypatch.setattr('paper_radar.sources.openalex_semantic.get_credential',lambda _: '')
    from paper_radar.sources.base import _REGISTRY
    cfg={'keywords':['physics models'], 'categories':[], 'lookback_days':7,
         'max_results':3, 'research_goal':'Physics-informed models for simulation'}
    with pytest.raises(RuntimeError):
        _REGISTRY[source]().fetch(cfg, logger=lambda _msg:None)
    assert len(calls)==1
