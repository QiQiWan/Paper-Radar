# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.26 desktop, provider cache, reproducible evaluation hardening."""
from __future__ import annotations
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from paper_radar.models import Paper
from paper_radar.source_cache import SourceCache, SourceResponse, cache_key
from paper_radar.desktop_paths import user_data_dir, ensure_data_dir
from paper_radar.desktop_manager import Controller, backend_command, read_log
from paper_radar.sources.request_policy import retry_seconds
from paper_radar.portal_discovery import discover
from paper_radar.public_portal import Portal
from paper_radar.sources.supervisor import source_child_env
from paper_radar.scheduler import build_install_script
from tools.eval_real_papers import (evaluate_predictions,create_annotation_template,load,
                                   evaluate_pdf_pages)


def paper(name='paper1',title='A publication'):
    return Paper(paper_id=name,title=title,abstract='Abstract',authors='A',
                 published='2026-11-05',updated='2026-11-05',category='article',
                 url='https://arxiv.org/abs/1234.5678',pdf_url='')


def cfg():
    return {'keywords':['neural operator'],'max_results':20,'lookback_days':14,
            'max_queries_per_source':8}


def test_cache_hit_ttl_and_stale(tmp_path):
    t=[1800000000]
    c=SourceCache(tmp_path,clock=lambda:t[0]);c.put('openalex',cfg(),[paper()])
    value=c.get('openalex',cfg());assert value.cache_state=='hit' and len(value)==1
    t[0]+=3600
    assert c.get('openalex',cfg()) is None
    old=c.get('openalex',cfg(),allow_stale=True)
    assert old and old.cache_state=='stale' and old.cache_age_seconds==3600
    t[0]+=24*3600
    assert c.get('openalex',cfg(),allow_stale=True) is None


def test_cache_no_private_text_in_sqlite_key(tmp_path):
    c=SourceCache(tmp_path);c.put('crossref',{'keywords':['PRIVATE QUERY'], 'max_results':1},[paper()])
    with sqlite3.connect(c.path) as db:
        records=db.execute('SELECT cache_key,payload FROM source_cache').fetchall()
    assert len(records)==1 and len(records[0][0])==64
    assert 'PRIVATE QUERY' not in json.dumps(records)


def test_cache_is_not_reused_after_filter_changes(tmp_path):
    c=SourceCache(tmp_path);c.put('arxiv',cfg(),[paper()])
    different=cfg() | {'keywords':['different topic']}
    assert c.get('arxiv',different) is None
    assert c.get('openalex',cfg()) is None


def test_cache_corruption_falls_back_without_exception(tmp_path):
    c=SourceCache(tmp_path);c.put('arxiv',cfg(),[paper()]);key=cache_key('arxiv',cfg())
    with sqlite3.connect(c.path) as db:
        db.execute('UPDATE source_cache SET payload=? WHERE cache_key=?',('INVALID JSON',key))
    assert c.get('arxiv',cfg()) is None


def test_cache_is_cross_process_and_bounded(tmp_path):
    c=SourceCache(tmp_path);c.put('arxiv',cfg(),[paper()])
    code='from paper_radar.source_cache import SourceCache; import json,sys; print(len(SourceCache(sys.argv[1]).get("arxiv",json.loads(sys.argv[2]))))'
    proc=subprocess.run([sys.executable,'-c',code,str(tmp_path),json.dumps(cfg())],
                        text=True,capture_output=True,check=True)
    assert proc.stdout.strip()=='1'


def test_cache_discover_diagnostic_fresh_and_stale(tmp_path,monkeypatch):
    portal=Portal(tmp_path,embedded_workers=False)
    monkeypatch.setattr(portal,'_fetch_public_source',lambda *_args,**_kw:
                        SourceResponse([paper()],cache_state='stale',age_seconds=35999))
    papers,details=discover(portal,cfg(),['crossref'])
    assert len(papers)==1 and details[0]['degraded'] is True
    assert details[0]['cache_state']=='stale'


def test_cache_permission_and_symlink_guard(tmp_path):
    c=SourceCache(tmp_path/'cache');c.put('arxiv',cfg(),[paper()])
    if os.name!='nt':assert c.path.stat().st_mode & 0o077 == 0
    link=tmp_path/'link'
    try:link.symlink_to(c.path)
    except (OSError,NotImplementedError):return
    other=SourceCache(tmp_path);other.path=link
    assert other.get('arxiv',cfg()) is None


def test_provider_retry_after_is_bounded():
    assert retry_seconds('120')==6.0
    assert retry_seconds('-100')==.5
    assert retry_seconds(None,attempt=2)==2.0
    assert retry_seconds('nan')==6.0


def test_source_public_child_has_no_model_secret(monkeypatch):
    monkeypatch.setenv('PAPER_RADAR_API_KEY','very-secret')
    monkeypatch.setenv('OPENALEX_API_KEY','also-secret')
    monkeypatch.delenv('PAPERRADAR_PUBLIC_ALLOW_OPENALEX_API_KEY',raising=False)
    env=source_child_env('openalex',public_metadata=True)
    assert 'PAPER_RADAR_API_KEY' not in env and 'OPENALEX_API_KEY' not in env


def test_user_data_directories_platforms(tmp_path):
    assert user_data_dir(platform='win32',env={'LOCALAPPDATA':'C:/Users/Test/AppData/Local'},home=tmp_path).name=='Paper-Radar'
    assert user_data_dir(platform='darwin',env={},home=tmp_path)==tmp_path/'Library'/'Application Support'/'Paper-Radar'
    assert user_data_dir(platform='linux',env={},home=tmp_path)==tmp_path/'.local'/'share'/'paper-radar'
    assert user_data_dir(platform='linux',env={'XDG_DATA_HOME':str(tmp_path/'custom')},home=tmp_path)==tmp_path/'custom'/'paper-radar'


def test_user_data_relative_override_rejected(tmp_path):
    with pytest.raises(ValueError):user_data_dir(env={'PAPER_RADAR_USER_HOME':'relative/path'})
    link=tmp_path/'link';destination=tmp_path/'real';destination.mkdir()
    try:link.symlink_to(destination,target_is_directory=True)
    except (OSError,NotImplementedError):return
    with pytest.raises(ValueError):ensure_data_dir(link)


def test_source_child_command_frozen(monkeypatch):
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    assert backend_command(8772)==[sys.executable,'--serve','--port','8772']
    monkeypatch.delattr(sys,'frozen',raising=False)
    assert backend_command(8772)[-4:]==['--serve','--port','8772'][-4:] if False else backend_command(8772)[-3:]==['--serve','--port','8772']


def test_frozen_windows_task_scheduler_argument():
    script=build_install_script('08:30','C:/Program Files/Paper-Radar/Paper-Radar.exe','',frozen=True)
    assert "--once" in script
    assert 'main.py' not in script


def test_controller_never_attaches_to_another_user_service(tmp_path,monkeypatch):
    ctrl=Controller(tmp_path)
    def fake_find(owner_id=None):
        return {'port':8766,'version':'1.26.0','desktop_instance':'foreign'} if owner_id is None else None
    monkeypatch.setattr('paper_radar.desktop_manager.find_service',fake_find)
    monkeypatch.setattr('paper_radar.desktop_manager.free_port',lambda:None)
    assert ctrl.start()['state']=='error'
    assert ctrl.stop() is False


def test_desktop_supervision_key_private_and_stable(tmp_path):
    from paper_radar.desktop_identity import read_or_create_secret,instance_fingerprint
    key1=read_or_create_secret(tmp_path/'data')
    key2=read_or_create_secret(tmp_path/'data')
    assert key1==key2 and len(key1)>=40
    assert instance_fingerprint(key1)!=instance_fingerprint('another-secret')
    assert key1 not in instance_fingerprint(key1)
    if os.name!='nt':
        assert (tmp_path/'data'/'.desktop_supervision.key').stat().st_mode & 0o077==0


def test_desktop_stop_only_accepts_private_manager_key(tmp_path):
    import http.client
    import time
    from http.server import ThreadingHTTPServer
    from paper_radar.webapp import Handler
    from paper_radar.desktop_identity import read_or_create_secret,instance_fingerprint
    secret=read_or_create_secret(tmp_path/'data')
    httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    httpd.daemon_threads=True
    httpd.token='a-different-browser-token'
    httpd.desktop_supervision_key=secret
    httpd.desktop_instance=instance_fingerprint(secret)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True)
    thread.start()
    try:
        conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=3)
        conn.request('GET','/api/health')
        resp=conn.getresponse()
        health=json.loads(resp.read())
        assert health['desktop_instance']==instance_fingerprint(secret)
        assert secret not in json.dumps(health)
        conn.close()
        for supplied,code in [('',403),('incorrect',403),(secret,200)]:
            conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=3)
            conn.request('POST','/api/desktop/stop',body=b'{}',headers={'Host':f'127.0.0.1:{httpd.server_port}',
                'Content-Type':'application/json','X-Desktop-Supervision-Key':supplied})
            reply=conn.getresponse()
            assert reply.status==code
            reply.read();conn.close()
        thread.join(timeout=3)
        assert not thread.is_alive()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_controller_log_tail_is_bounded(tmp_path):
    target=tmp_path/'run.log';target.write_bytes(b'X'*10000+b'\nlast line\n')
    tail=read_log(target);assert 'last line' in tail and len(tail)<=4500


def test_prediction_matches_all_critical_fields(tmp_path):
    _,gold=load();gold={'fields':[gold['fields'][0]]}
    correct=gold['fields'][0].copy()
    f=tmp_path/'predictions.json'
    f.write_text(json.dumps([correct]),encoding='utf-8')
    assert evaluate_predictions(f,gold)['f1']==1
    wrong=correct | {'method':'Another algorithm'}
    f.write_text(json.dumps([wrong]),encoding='utf-8')
    assert evaluate_predictions(f,gold)['f1']==0
    f.write_text(json.dumps([correct,correct]),encoding='utf-8')
    result=evaluate_predictions(f,gold)
    assert result['duplicate_predictions']==1 and result['precision']==.5


def test_real_eval_missing_pdf_is_not_a_metric(tmp_path):
    manifest,gold=load()
    summary=evaluate_pdf_pages(manifest,gold,tmp_path)
    assert summary['real_pdfs_parsed']==0
    assert summary['candidate_page_recall'] is None
    assert summary['exact_quote_span_rate'] is None


def test_double_reviewer_template_is_unlabeled(tmp_path):
    result=create_annotation_template(tmp_path)
    assert result['annotation_rows_measured']==0
    assert len(result['template_files'])==2
    import csv
    for path in result['template_files']:
        with open(path,encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f))
        assert len(rows)==3 and all(not x['value'] and not x['quote'] for x in rows)


def test_portal_cache_reuses_real_source_adapter_across_instances(tmp_path,monkeypatch):
    source_calls=[]
    def fake_source(name,config,**kwargs):
        source_calls.append(name)
        return [paper('crossref:10.1234/a','Study with reproducible metadata')]
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',fake_source)
    first=Portal(tmp_path,embedded_workers=False)
    rows=first._fetch_public_source('crossref',cfg())
    assert len(rows)==1 and rows.cache_state=='miss'
    second=Portal(tmp_path,embedded_workers=False)
    reused=second._fetch_public_source('crossref',cfg())
    assert len(reused)==1 and reused.cache_state=='hit'
    assert source_calls==['crossref']


def test_arxiv_query_cache_avoids_second_network_and_rate_delay(tmp_path,monkeypatch):
    network=[]
    def stub(name,config,**kw):
        network.append(name)
        return [paper('2501.12345','Neural operators and generalization')]
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',stub)
    portal=Portal(tmp_path,embedded_workers=False)
    first,_=portal._fetch_public_arxiv(cfg(),lambda:False)
    second,diag=portal._fetch_public_arxiv(cfg(),lambda:False)
    assert len(first)==1 and len(second)==1
    assert diag[0]['cache_state']=='hit'
    assert network==['arxiv']


def test_read_runtime_never_exposes_sensitive_configuration(monkeypatch):
    from paper_radar.desktop_manager import read_runtime
    class FakeResp:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self,max_size):
            payload={'config':{'api_key':'SECRET'},
                     'runtime':{'busy':True,'phase':'Analyzing PDF','progress':{'percent':67.3}},
                     'last_run':{'state':'success'}}
            return json.dumps(payload).encode('utf-8')
    monkeypatch.setattr('paper_radar.desktop_manager.urlopen',lambda *_args,**_kwargs:FakeResp())
    summary=read_runtime(8765)
    assert summary['busy'] and summary['phase']=='Analyzing PDF'
    assert summary['progress_percent']==67.3
    assert 'SECRET' not in repr(summary)


def test_empty_source_result_cache_is_short_lived_and_never_used_as_stale(tmp_path):
    t=[1_800_000_000]
    cache=SourceCache(tmp_path,clock=lambda:t[0]);cache.put('arxiv',cfg(),[])
    assert cache.get('arxiv',cfg()) is not None
    t[0]+=301
    assert cache.get('arxiv',cfg()) is None
    assert cache.get('arxiv',cfg(),allow_stale=True) is None


def test_source_cache_rejects_root_symlink(tmp_path):
    actual=tmp_path/'root';actual.mkdir()
    link=tmp_path/'alias'
    try:link.symlink_to(actual,target_is_directory=True)
    except (OSError,NotImplementedError):return
    with pytest.raises(ValueError):SourceCache(link)

def test_source_release_keeps_desktop_build_files_but_excludes_user_secrets():
    from tools.package_release import include
    assert include(Path('paper_radar/desktop_app.py'))
    assert include(Path('tools/packaging/build_desktop.py'))
    assert include(Path('.github/workflows/desktop-release.yml'))
    assert not include(Path('data/.desktop_supervision.key'))
    assert not include(Path('data/source_metadata_cache.sqlite3'))


def test_parallel_desktop_launches_share_one_private_identity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from paper_radar.desktop_identity import read_or_create_secret
    with ThreadPoolExecutor(max_workers=8) as pool:
        result=list(pool.map(lambda _:read_or_create_secret(tmp_path/'data'),range(16)))
    assert len(set(result))==1 and len(result[0])>=40


def test_frozen_research_worker_uses_native_entrypoint(monkeypatch, tmp_path):
    from paper_radar.portal_worker import child_command
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    native=child_command(tmp_path,22,tmp_path/'child-result.json',768,240)
    assert native[:3]==[sys.executable,'--portal-worker','_execute']
    assert '-m' not in native
    monkeypatch.delattr(sys,'frozen',raising=False)
    source=child_command(tmp_path,22,tmp_path/'child-result.json',768,240)
    assert source[1:4]==['-m','paper_radar.portal_worker','_execute']

def test_frozen_worker_dispatch_keeps_control_flow(monkeypatch):
    from paper_radar import desktop_app
    seen=[]
    monkeypatch.setattr('paper_radar.portal_worker.main',lambda argv:(seen.extend(argv),0)[1])
    assert desktop_app.main(['--portal-worker','_execute','--job-id','4'])==0
    assert seen==['_execute','--job-id','4']
