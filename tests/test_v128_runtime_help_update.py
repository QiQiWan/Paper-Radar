# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.28 foreground process, first-run help, confirmed update handoff regression."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import pytest

from paper_radar.core import DEFAULTS, validate_config
from paper_radar.help_pages import render_guide, markdown_html
from paper_radar import update_handoff


def test_shell_mode_defaults_to_foreground_and_is_validated():
    from paper_radar.startup_update import read_startup_settings
    assert read_startup_settings(Path('/does/not/exist'))['service_run_mode']=='foreground'
    assert validate_config(DEFAULTS)['service_run_mode']=='foreground'
    assert validate_config({**DEFAULTS,'service_run_mode':'background'})['service_run_mode']=='background'
    with pytest.raises(ValueError,match='运行方式'):
        validate_config({**DEFAULTS,'service_run_mode':'unknown'})
    assert validate_config(DEFAULTS)['auto_update_on_startup'] is False


def test_shell_startup_override_and_persisted_mode(tmp_path):
    import startup
    (tmp_path/'settings.json').write_text(json.dumps({'service_run_mode':'background'}),encoding='utf-8')
    called=[]
    def runner(cmd,**kw):
        called.append(cmd)
        return SimpleNamespace(returncode=0)
    startup.launch(tmp_path,runner=runner)
    assert called[-1][-1]=='--launch'
    startup.launch(tmp_path,runner=runner,mode='foreground')
    assert called[-1][-1]=='--web'
    startup.launch(tmp_path,runner=runner,mode='background')
    assert called[-1][-1]=='--launch'


def test_validated_help_pages_and_no_path_traversal():
    for slug in ('quickstart','desktop','usage','zotero','portal'):
        body=render_guide(slug)
        assert body is not None and b'/guide.css' in body
        assert b'/guide/quickstart' in body
    assert render_guide('../README') is None
    assert render_guide('quickstart/../../README') is None
    assert '<script>' not in markdown_html('<script>alert(1)</script>')
    assert '&lt;script&gt;' in markdown_html('<script>alert(1)</script>')


def test_onboarding_state_default_and_roundtrip():
    assert validate_config(DEFAULTS)['onboarding_completed'] is False
    assert validate_config({**DEFAULTS,'onboarding_completed':True})['onboarding_completed'] is True


def test_handoff_manual_version_validation_and_no_frozen_updates(tmp_path,monkeypatch):
    import sys
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    with pytest.raises(ValueError,match='安装版'):
        update_handoff.schedule_install(8765,'1.29.0',tmp_path)
    monkeypatch.delattr(sys,'frozen',raising=False)
    with pytest.raises(ValueError,match='版本号无效'):
        update_handoff.schedule_install(8765,'1.29.0;rm -rf /',tmp_path)


def test_update_handoff_does_not_stop_other_user(tmp_path,monkeypatch):
    from paper_radar import desktop_manager
    owner='same-person-key'
    monkeypatch.setattr(update_handoff,'read_or_create_secret',lambda _p:owner)
    monkeypatch.setattr(desktop_manager,'read_health',lambda port,**kw:({
        'port':port,'desktop_instance':'a-different-user'} if port==8765 else None))
    with pytest.raises(RuntimeError,match='其他用户'):
        update_handoff.shutdown_user_services(desktop_manager.APP_ROOT,timeout=2)


def test_handoff_success_rechecks_release_and_restarts(tmp_path,monkeypatch):
    events=[]
    class FakeUpdater:
        def __init__(self,root):self.root=root
        def public_service_running(self):return False
        def check_release(self):events.append('preflight');return {'version':'1.29.0'}
        def is_web_running(self):return False
        def run(self,**kw):events.append('install');return {'state':'updated'}
        def local_version(self):return '1.29.0'
        def status(self,*args,**kw):events.append('mark-success')
    monkeypatch.setattr(update_handoff,'StartupUpdater',FakeUpdater)
    monkeypatch.setattr(update_handoff,'shutdown_user_services',lambda root:events.append('stop') or True)
    monkeypatch.setattr(update_handoff,'restart_local_service',lambda root,port,version:events.append('restart') or True)
    import startup
    monkeypatch.setattr(startup,'ensure_dependencies',lambda *a,**kw:events.append('dependencies'))
    assert update_handoff.execute(8765,'1.29.0',tmp_path)==0
    assert events==['preflight','stop','install','dependencies','restart','mark-success']
    assert update_handoff.state(tmp_path)['state']=='success'


def test_handoff_preflight_failure_keeps_old_server(tmp_path,monkeypatch):
    calls=[]
    class FakeUpdater:
        def __init__(self,root):pass
        def public_service_running(self):return False
        def check_release(self):return None
        def is_web_running(self):return True
        def local_version(self):return '1.28.0'
    monkeypatch.setattr(update_handoff,'StartupUpdater',FakeUpdater)
    monkeypatch.setattr(update_handoff,'shutdown_user_services',lambda _:calls.append('stop'))
    monkeypatch.setattr(update_handoff,'_resume_surviving_services',lambda _:None)
    assert update_handoff.execute(8765,'1.29.0',tmp_path)==1
    assert calls==[]
    assert update_handoff.state(tmp_path)['state']=='failed'


@pytest.fixture
def local_http(monkeypatch):
    from http.server import ThreadingHTTPServer
    from paper_radar import webapp
    server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
    server.token='test-local-token'
    server.desktop_supervision_key='test-private-secret'
    server.desktop_instance='test-fingerprint'
    monkeypatch.setattr(webapp,'read_config',lambda:validate_config(DEFAULTS))
    monkeypatch.setattr(webapp.update_checker,'status',lambda cfg=None:{
        'state':'available','latest_version':'1.29.0','tag_name':'v1.29.0',
        'enabled':True,'can_install':True})
    t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    webapp.runtime.maintenance_until=0
    yield (server,webapp)
    webapp.runtime.maintenance_until=0
    server.shutdown();server.server_close();t.join(timeout=3)


def get_local(port,path):
    with urlopen(f'http://127.0.0.1:{port}{path}',timeout=4) as response:
        return response.status,response.read()


def post_local(port,path,payload,token='test-local-token'):
    req=Request(f'http://127.0.0.1:{port}{path}',method='POST',
        data=json.dumps(payload).encode('utf-8'),headers={'Content-Type':'application/json','X-Local-Token':token})
    try:
        with urlopen(req,timeout=4) as response:
            return response.status,json.loads(response.read())
    except HTTPError as e:
        return e.code,json.loads(e.read())


def test_local_docs_serve_real_html_and_reject_unknown(local_http):
    (server,_)=local_http
    status,raw=get_local(server.server_port,'/guide/quickstart')
    assert status==200 and b'Paper-Radar' in raw and b'./start_macos_linux.sh' in raw
    assert get_local(server.server_port,'/guide.css')[0]==200
    with pytest.raises(HTTPError) as e:
        get_local(server.server_port,'/guide/../../README')
    assert e.value.code==404


def test_manual_update_requires_csrf_and_exact_confirmation(local_http,monkeypatch):
    server, webapp=local_http
    monkeypatch.setattr(webapp,'LOCK_FILE',Path('/definitely/not/an/existing/lock'))
    monkeypatch.setattr(webapp,'runtime',webapp.Runtime())
    from paper_radar import startup_update
    monkeypatch.setattr(startup_update.StartupUpdater,'public_service_running',lambda _:False)
    monkeypatch.setattr(update_handoff,'schedule_install',lambda port,target,root:True)
    port=server.server_port
    assert post_local(port,'/api/update/install',{'confirm':True,'tag_name':'v1.29.0'},token='incorrect')[0]==403
    assert post_local(port,'/api/update/install',{'confirm':False,'tag_name':'v1.29.0'})[0]==400
    assert post_local(port,'/api/update/install',{'confirm':True,'tag_name':'v9.9.9'})[0]==400
    status,data=post_local(port,'/api/update/install',{'confirm':True,'tag_name':'v1.29.0'})
    assert status==202 and data['state']=='queued'
    assert webapp.runtime.state()['maintenance']
    assert post_local(port,'/api/update/install',{'confirm':True,'tag_name':'v1.29.0'})[0]==409
    # The authenticated desktop manager can cancel an abandoned handoff.
    req=Request(f'http://127.0.0.1:{port}/api/desktop/resume',method='POST',data=b'{}',
        headers={'X-Desktop-Supervision-Key':'test-private-secret','Content-Type':'application/json'})
    with urlopen(req,timeout=3) as res:assert res.status==200
    assert not webapp.runtime.state()['maintenance']


def test_local_process_status_is_visible_and_not_an_update_credential(local_http):
    server,_=local_http
    code,payload=get_local(server.server_port,'/api/process')
    info=json.loads(payload)
    assert code==200 and info['pid']>0 and info['port']==server.server_port
    assert 'desktop_supervision_key' not in payload.decode('utf-8')
    assert 'secret' not in payload.decode('utf-8')


def test_new_user_guide_does_not_run_remote_code():
    html=Path(__file__).parents[1]/'paper_radar/static/index.html'
    text=html.read_text(encoding='utf-8')
    for key in ('id="welcomeWizard"','id="welcomeNext"','id="welcomeSkip"',
                'href="/guide/quickstart"','id="serviceRunMode"','id="installUpdate"'):
        assert key in text


def test_detached_launcher_only_attaches_to_current_account(monkeypatch):
    from paper_radar import launcher, desktop_manager, desktop_identity
    other={'port':8765,'version':'1.28.0','desktop_instance':'another-user'}
    own={'port':8766,'version':'1.28.0','desktop_instance':'current-owner'}
    monkeypatch.setattr(desktop_identity,'read_or_create_secret',lambda p:'local-secret')
    monkeypatch.setattr(desktop_identity,'instance_fingerprint',lambda s:'current-owner')
    monkeypatch.setattr(desktop_manager,'read_health',lambda p,**kwargs:other if p==8765 else own if p==8766 else None)
    opened=[]
    monkeypatch.setattr(launcher.webbrowser,'open',lambda address:opened.append(address))
    launcher.main()
    assert opened==['http://127.0.0.1:8766']


def test_native_bundle_includes_all_help_documents():
    builder=(Path(__file__).parents[1]/'tools/packaging/build_desktop.py').read_text(encoding='utf-8')
    assert "str(ROOT/'docs')+sep+'docs'" in builder


def test_release_mismatch_after_install_triggers_rollback(tmp_path,monkeypatch):
    events=[]
    class FakeUpdater:
        def __init__(self,root):pass
        def public_service_running(self):return False
        def check_release(self):return {'version':'1.29.0'}
        def is_web_running(self):return False
        def run(self,**kwargs):events.append('install');return {'state':'updated'}
        def local_version(self):return '1.30.0'  # GitHub changed after preflight
        def mark_failed_and_rollback(self,**kw):events.append('rollback');return True
    monkeypatch.setattr(update_handoff,'StartupUpdater',FakeUpdater)
    monkeypatch.setattr(update_handoff,'shutdown_user_services',lambda *_args,**_kw:True)
    monkeypatch.setattr(update_handoff,'restart_local_service',lambda *_args,**_kw:True)
    monkeypatch.setattr(update_handoff,'_resume_surviving_services',lambda *_args,**_kw:None)
    assert update_handoff.execute(8765,'1.29.0',tmp_path)==1
    assert events==['install','rollback']


def test_guide_hyperlinks_allowlist_and_external_https():
    from paper_radar.help_pages import inline
    assert 'href="/guide/desktop"' in inline('[升级说明](UPDATING.md)')
    assert 'href="/guide/desktop"' in inline('[启动](RUNTIME_AND_UPDATES_V1.28.0.md)')
    assert 'href="https://example.org/paper"' in inline('[论文](https://example.org/paper)')
    assert '<a' not in inline('[恶意](javascript:alert)')
    assert '<a' not in inline('[本机文件](file:///etc/passwd)')


def test_foreground_selects_unoccupied_port_if_default_taken(tmp_path,monkeypatch):
    import startup
    from paper_radar import desktop_manager
    import subprocess
    launched=[]
    monkeypatch.setattr(desktop_manager,'find_service',lambda *args:None)
    monkeypatch.setattr(desktop_manager,'free_port',lambda :8766)
    monkeypatch.setattr(subprocess,'run',lambda cmd,**kw:launched.append(cmd) or SimpleNamespace(returncode=0))
    startup.launch(tmp_path,mode='foreground')
    assert launched[-1][-3:]==['--web','--port','8766']


def test_foreground_keyboard_interrupt_exits_cleanly(tmp_path,capsys):
    import startup
    def interrupted(cmd,**kw):
        raise KeyboardInterrupt()
    startup.launch(tmp_path,runner=interrupted,mode='foreground')
    assert '本地工作台已停止' in capsys.readouterr().out
