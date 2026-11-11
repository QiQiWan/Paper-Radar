"""v1.49.1: real HTTP error classification and privacy-safe diagnostics bundle."""
from contextlib import contextmanager
from pathlib import Path
from urllib import request, error
from zipfile import ZipFile
from io import BytesIO
import json
import os
import re
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@contextmanager
def running_server(tmp_path, crash=False):
    port=_port()
    env={**os.environ, 'PAPER_RADAR_USER_HOME':str(tmp_path),
         'PAPER_RADAR_API_KEY':'fake-test-key-never-share'}
    if crash:
        script=("from paper_radar import webapp; "
                "webapp.runtime.metrics=lambda cfg: (_ for _ in ()).throw(RuntimeError('SECRET_PAPER_TEXT_NO_LOG')); "
                f"webapp.run_server({port})")
        args=[sys.executable, '-c', script]
    else:
        args=[sys.executable, 'main.py', '--serve', '--port', str(port)]
    p=subprocess.Popen(args,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    address=f'http://127.0.0.1:{port}'
    try:
        for _ in range(80):
            try:
                with request.urlopen(address+'/',timeout=.4) as response:
                    html=response.read().decode('utf-8')
                break
            except (OSError,TimeoutError):
                if p.poll() is not None:raise AssertionError('Local server stopped at boot')
                time.sleep(.1)
        else:raise AssertionError('Local server did not bind the port')
        token=re.search(r'<meta content="([^"]+)" name="local-token"',html).group(1)
        def api(path,body=None,token_override=None):
            headers={}
            if body is not None:
                headers={'Content-Type':'application/json','X-Local-Token':token if token_override is None else token_override,
                         'Origin':address}
            req=request.Request(address+path,method='POST' if body is not None else 'GET',
                                headers=headers,data=json.dumps(body).encode('utf-8') if body is not None else None)
            try:
                with request.urlopen(req,timeout=7) as result:
                    payload=result.read()
                    return result.status,payload
            except error.HTTPError as exc:
                return exc.code,exc.read()
        yield api, tmp_path
    finally:
        p.terminate()
        try:p.communicate(timeout=5)
        except subprocess.TimeoutExpired:p.kill();p.communicate(timeout=5)


def test_fresh_bootstrap_and_model_failure_are_actionable(tmp_path):
    with running_server(tmp_path) as (api,folder):
        assert (folder/'settings.json').is_file(), 'Diagnostics must not break initial settings creation'
        for endpoint in ('/api/health','/api/bootstrap','/api/state','/api/topics',
                         '/api/papers?limit=5','/api/agents','/api/history',
                         '/api/downloads','/api/reports','/api/logs?source=system'):
            status,body=api(endpoint)
            assert status==200, (endpoint,status,body[:100])
        status,body=api('/api/config', {'api_provider':'DeepSeek','api_base_url':'http://127.0.0.1:9',
                                      'api_model':'example-model','llm_data_consent':True})
        assert status==200,body
        status,body=api('/api/ai/plan',{'goal':'研究有限元神经算子的跨网格与复杂几何泛化，并比较多种实验条件的误差'})
        payload=json.loads(body)
        assert status==424,payload
        assert payload['code']=='MODEL_CONNECT_FAILED'
        assert '模型服务' in payload['error']
        assert re.fullmatch('[a-f0-9]{12}',payload['request_id'])
        _,data=api('/api/logs?source=system')
        assert payload['request_id'] in json.loads(data)['system']
        assert 'fake-test-key-never-share' not in json.loads(data)['system']
        status,_=api('/api/diagnostics/export',{},token_override='wrong')
        assert status==403
        status,blob=api('/api/diagnostics/export',{'include_task_logs':False})
        assert status==200
        with ZipFile(BytesIO(blob)) as z:
            assert 'runtime.jsonl' in z.namelist()
            assert 'manifest.json' in z.namelist()
            assert all('settings.json' not in f for f in z.namelist())
            text=z.read('runtime.jsonl').decode('utf-8')
            assert payload['request_id'] in text
            assert 'fake-test-key-never-share' not in text
            assert '研究有限元神经算子' not in text
            assert 'http://127.0.0.1:9' not in text
            assert all('optional_task_logs/' not in f for f in z.namelist())
        (folder/'logs'/'manual.log').write_text('Bearer MY_PRIVATE_TOKEN\nresearch keywords maybe private\n',encoding='utf-8')
        status,blob=api('/api/diagnostics/export',{'include_task_logs':True})
        assert status==200
        with ZipFile(BytesIO(blob)) as z:
            task=z.read('optional_task_logs/manual.log').decode('utf-8')
            assert 'MY_PRIVATE_TOKEN' not in task
            assert 'Bearer [REDACTED]' in task


def test_unexpected_exception_has_error_id_but_never_leaks_message(tmp_path):
    with running_server(tmp_path,crash=True) as (api,folder):
        code,body=api('/api/state')
        response=json.loads(body)
        assert code==500,response
        assert response['code']=='INTERNAL_ERROR'
        assert 'SECRET_PAPER_TEXT_NO_LOG' not in str(response)
        assert re.fullmatch('[a-f0-9]{12}',response['request_id'])
        rows=(folder/'logs'/'runtime.jsonl').read_text(encoding='utf-8')
        assert response['request_id'] in rows
        assert 'RuntimeError' in rows
        assert 'SECRET_PAPER_TEXT_NO_LOG' not in rows
        assert 'webapp.py' in rows


def test_diagnostic_rotation_does_not_crash_on_write_errors(tmp_path,monkeypatch):
    from paper_radar import runtime_diagnostics as d
    log=d.RuntimeDiagnostics(tmp_path)
    monkeypatch.setattr(d,'MAX_LOG_BYTES',200)
    for _ in range(14):
        log.record('test_event',method='SYSTEM',route='/api/test?private_query',detail='safe')
    assert (tmp_path/'logs'/'runtime.jsonl.1').exists()
    assert (tmp_path/'logs'/'runtime.jsonl.2').exists()
    for name in d._LOG_FILES:
        for line in (tmp_path/'logs'/name).read_text(encoding='utf-8').splitlines():
            row=json.loads(line)
            assert row['route']=='/api/test'
            assert 'private_query' not in line
    dead=tmp_path/'not_a_directory';dead.write_text('x',encoding='utf-8')
    assert d.RuntimeDiagnostics(dead).record('cannot_write',method='SYSTEM')==''


def test_no_get_download_and_client_error_payload_validation(tmp_path):
    with running_server(tmp_path) as (api,folder):
        status,_=api('/api/diagnostics/export')
        assert status in (404,405)
        status,_=api('/api/diagnostics/client',{'kind':'javascript_error','source':'../../secrets','line':1})
        assert status==400
        status,_=api('/api/diagnostics/client',{'kind':'javascript_error','source':'app.js','line':120})
        assert status==202
        text=(folder/'logs'/'runtime.jsonl').read_text(encoding='utf-8')
        assert 'client_error' in text
        assert 'app.js:120' in text


def test_can_download_diagnostics_in_readonly_recovery_mode(tmp_path):
    with running_server(tmp_path) as (api,folder):
        (folder/'settings.json').write_text('{damaged config',encoding='utf-8')
        # A read causes configuration safety to enter read-only fallback mode.
        api('/api/state')
        status,_=api('/api/config',{'lookback_days':14})
        assert status==423
        status,blob=api('/api/diagnostics/export',{'include_task_logs':False})
        assert status==200
        with ZipFile(BytesIO(blob)) as archive:
            assert 'manifest.json' in archive.namelist()


def test_storage_failure_is_recorded_without_exposing_error(tmp_path):
    # Storage availability errors should remain distinguishable from provider
    # errors while retaining a correlatable, sanitised request id.
    port=_port()
    env={**os.environ,'PAPER_RADAR_USER_HOME':str(tmp_path)}
    code=("import sqlite3; from paper_radar import webapp; "
          "webapp.runtime.metrics=lambda cfg: (_ for _ in ()).throw(sqlite3.OperationalError('SECRET_DB_MESSAGE')); "
          f"webapp.run_server({port})")
    proc=subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    try:
        for _ in range(75):
            try:
                with request.urlopen(f'http://127.0.0.1:{port}/',timeout=.5): break
            except OSError:
                if proc.poll() is not None:raise AssertionError('Server unavailable')
                time.sleep(.1)
        try:
            request.urlopen(f'http://127.0.0.1:{port}/api/state',timeout=5)
            pytest.fail('Storage exception was silently ignored')
        except error.HTTPError as response:
            assert response.code==503
            result=json.loads(response.read())
        assert re.fullmatch('[a-f0-9]{12}',result['request_id'])
        rows=(tmp_path/'logs'/'runtime.jsonl').read_text(encoding='utf-8')
        assert 'storage_error' in rows
        assert result['request_id'] in rows
        assert 'SECRET_DB_MESSAGE' not in rows
    finally:
        proc.terminate()
        try:proc.communicate(timeout=4)
        except subprocess.TimeoutExpired:proc.kill();proc.communicate(timeout=4)
