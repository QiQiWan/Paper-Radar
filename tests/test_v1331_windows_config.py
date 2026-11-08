"""Regression for Windows OneDrive Errno 22 on settings.json."""
from __future__ import annotations
import errno
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from paper_radar.core import DEFAULTS, read_config, save_config
from paper_radar.config_safety import config_health, assert_primary_healthy
from paper_radar.startup_update import read_startup_settings
from tools.windows_config_doctor import inspect, restore


def test_unicode_onedrive_path_and_backup(tmp_path):
    project = tmp_path/'OneDrive - 科研/研究项目/AI4paper'
    config = project/'settings.json'
    settings = {**DEFAULTS, 'keywords':['时序模型', 'fluid operator'],
                'auto_update_on_startup':False, 'service_run_mode':'background'}
    result = save_config(settings, config)
    assert config.is_file()
    assert (project/'settings.json.bak').is_file()
    assert read_config(config)['keywords'] == settings['keywords']
    assert read_startup_settings(project)['service_run_mode'] == 'background'
    assert config_health(config)['state'] == 'ok'


def test_missing_config_is_fresh_install(tmp_path):
    config=tmp_path/'settings.json'
    assert read_config(config)['keywords'] == DEFAULTS['keywords']
    assert config_health(config)['read_only'] is False
    save_config(DEFAULTS, config)
    assert config.is_file()


def test_corrupted_primary_falls_back_to_last_good_but_does_not_overwrite(tmp_path):
    config=tmp_path/'settings.json'
    save_config({**DEFAULTS,'keywords':['my-special-research']},config)
    original = '{broken json with old settings that must never be overwritten'
    config.write_text(original,encoding='utf-8')
    recovered=read_config(config)
    assert recovered['keywords']==['my-special-research']
    assert config_health(config)['state']=='backup'
    assert recovered['update_check_enabled'] is False
    assert recovered['ai_enabled'] is False
    with pytest.raises(ValueError,match='只读恢复模式'):
        save_config({**recovered,'keywords':['oops']},config)
    assert config.read_text(encoding='utf-8') == original
    assert read_startup_settings(tmp_path)['service_run_mode']=='foreground'
    assert restore(tmp_path)==0
    assert read_config(config)['keywords']==['my-special-research']
    assert list((tmp_path/'.paper_radar_recovery').glob('settings.*.unreadable'))


def test_errno22_on_primary_falls_back_without_touching_user_file(tmp_path,monkeypatch):
    config=tmp_path/'settings.json'
    save_config({**DEFAULTS,'keywords':['preserved']},config)
    from paper_radar import config_safety as mod
    original=mod._read_payload
    attempts=[]
    def failed_if_primary(path):
        if Path(path)==config:
            attempts.append(1)
            raise OSError(errno.EINVAL,'Invalid argument')
        return original(path)
    monkeypatch.setattr(mod,'_read_payload',failed_if_primary)
    monkeypatch.setattr(mod.time,'sleep',lambda n:None)
    result=read_config(config)
    assert result['keywords']==['preserved']
    assert len(attempts)==3
    assert config_health(config)['state']=='backup'
    with pytest.raises(ValueError): assert_primary_healthy(config)
    assert config.exists()


def test_transient_errno22_retries_and_restores_normal_mode(tmp_path,monkeypatch):
    config=tmp_path/'settings.json'
    save_config(DEFAULTS,config)
    from paper_radar import config_safety as mod
    original=mod._read_payload
    attempts=0
    def flaky(path):
        nonlocal attempts
        if Path(path)==config:
            attempts+=1
            if attempts<3:raise OSError(errno.EINVAL,'Invalid argument')
        return original(path)
    monkeypatch.setattr(mod,'_read_payload',flaky)
    monkeypatch.setattr(mod.time,'sleep',lambda n:None)
    assert read_config(config)['keywords']==DEFAULTS['keywords']
    assert attempts==3
    assert config_health(config)['state']=='ok'
    assert_primary_healthy(config)


def test_no_backup_uses_temporary_defaults_and_refuses_save(tmp_path):
    config=tmp_path/'settings.json'
    bad='bad JSON - DO NOT REMOVE'
    config.write_text(bad,encoding='utf-8')
    assert read_config(config)['keywords']==DEFAULTS['keywords']
    assert config_health(config)['state']=='defaults'
    assert read_config(config)['update_check_enabled'] is False
    assert read_config(config)['ai_enabled'] is False
    with pytest.raises(ValueError):save_config(DEFAULTS,config)
    assert config.read_text(encoding='utf-8')==bad
    assert restore(tmp_path)==2
    assert config.read_text(encoding='utf-8')==bad


def test_explicit_defaults_repair_archives_corrupt_file_and_preserves_data(tmp_path):
    config=tmp_path/'settings.json'
    config.write_text('broken settings',encoding='utf-8')
    data=tmp_path/'data'/'papers.sqlite3'
    data.parent.mkdir()
    data.write_bytes(b'SQLITE_FILE_MUST_REMAIN')
    assert restore(tmp_path,use_defaults=True)==0
    assert data.read_bytes()==b'SQLITE_FILE_MUST_REMAIN'
    assert read_config(config)['keywords']==DEFAULTS['keywords']
    archived=list((tmp_path/'.paper_radar_recovery').glob('settings.*.unreadable'))
    assert len(archived)==1 and archived[0].read_text()=='broken settings'


def test_path_is_directory_cannot_be_overwritten(tmp_path):
    path=tmp_path/'settings.json'
    path.mkdir()
    assert read_config(path)['keywords']==DEFAULTS['keywords']
    assert config_health(path)['read_only']
    with pytest.raises(ValueError): save_config(DEFAULTS,path)
    assert path.is_dir()


def test_bad_backup_keeps_existing_primary(tmp_path):
    path=tmp_path/'settings.json'
    path.write_text('invalid primary')
    (tmp_path/'settings.json.bak').write_text('invalid backup')
    assert read_config(path)['keywords']==DEFAULTS['keywords']
    assert config_health(path)['state']=='defaults'
    assert restore(tmp_path)==2
    assert path.read_text()=='invalid primary'


def test_boot_fallback_reads_backup_preferences(tmp_path):
    config=tmp_path/'settings.json'
    save_config({**DEFAULTS,'service_run_mode':'background','update_check_enabled':False,
                 'auto_update_on_startup':False},config)
    config.write_text('bad config')
    assert read_startup_settings(tmp_path)=={'service_run_mode':'background',
        'update_check_enabled':False,'auto_update_on_startup':False}


def test_valid_user_config_reappearing_recovers_read_only_state(tmp_path):
    config=tmp_path/'settings.json'
    save_config(DEFAULTS,config)
    before=config.read_text(encoding='utf-8')
    config.write_text('{garbled')
    read_config(config)
    assert config_health(config)['read_only']
    config.write_text(before)
    read_config(config)
    assert not config_health(config)['read_only']
    save_config({**DEFAULTS,'max_results':56},config)
    assert read_config(config)['max_results']==56


def test_ui_has_persistent_recovery_warning_and_link():
    root=Path(__file__).resolve().parent.parent
    assert 'configRecoveryBanner' in (root/'paper_radar/static/index.html').read_text()
    assert 'renderConfigRecovery' in (root/'paper_radar/static/app.js').read_text()
    assert 'windows-recovery' in (root/'paper_radar/help_pages.py').read_text()


def test_primary_disappearing_but_backup_exists_must_not_reset_config(tmp_path):
    config=tmp_path/'settings.json'
    save_config({**DEFAULTS,'keywords':['saved research']},config)
    config.unlink()
    cfg=read_config(config)
    assert cfg['keywords']==['saved research']
    assert cfg['update_check_enabled'] is False
    assert config_health(config)['state']=='backup'
    with pytest.raises(ValueError):save_config(DEFAULTS,config)
    assert restore(tmp_path)==0
    assert read_config(config)['keywords']==['saved research']


def test_startup_fallback_blocks_enabled_auto_updates(tmp_path):
    config=tmp_path/'settings.json'
    save_config({**DEFAULTS,'service_run_mode':'background','update_check_enabled':True,
                 'auto_update_on_startup':True},config)
    config.write_text('{not-valid-json')
    safe=read_startup_settings(tmp_path)
    assert safe['service_run_mode']=='background'
    assert safe['update_check_enabled'] is False
    assert safe['auto_update_on_startup'] is False


def test_explicit_defaults_repair_produces_new_valid_backup(tmp_path):
    config=tmp_path/'settings.json'
    config.write_text('{invalid')
    (tmp_path/'settings.json.bak').write_text('{also-invalid')
    assert restore(tmp_path,use_defaults=True)==0
    assert json.loads(config.read_text())['keywords']==DEFAULTS['keywords']
    assert json.loads((tmp_path/'settings.json.bak').read_text())['keywords']==DEFAULTS['keywords']
    assert list((tmp_path/'.paper_radar_recovery').glob('settings-bak.*.unreadable'))


def test_existing_paper_library_does_not_reinitialize_missing_config(tmp_path):
    library=tmp_path/'data'/'papers.sqlite3'
    library.parent.mkdir()
    library.write_bytes(b'old data')
    cfg=read_config(tmp_path/'settings.json')
    assert cfg['update_check_enabled'] is False
    assert config_health(tmp_path/'settings.json')['read_only']
    with pytest.raises(ValueError):save_config(DEFAULTS,tmp_path/'settings.json')
    assert library.read_bytes()==b'old data'


def test_http_service_starts_with_invalid_config_in_isolated_workspace(tmp_path):
    """Exercise the actual main.py --serve -> webapp.run_server path."""
    import shutil, socket, subprocess, sys, time
    from urllib.request import urlopen
    root=Path(__file__).resolve().parent.parent
    isolated=tmp_path/'OneDrive - 科研'/'AI4paper'
    def excludes(directory, names):
        ignored={'__pycache__','.pytest_cache','.git','.venv'}
        if Path(directory).resolve()==root.resolve():
            ignored.update({'data','logs','reports','downloads'})
        return ignored.intersection(names)
    shutil.copytree(root,isolated,ignore=excludes)
    (isolated/'settings.json').write_text('{invalid',encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    proc=subprocess.Popen([sys.executable,'-u','main.py','--serve','--port',str(port)],
        cwd=str(isolated),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        last_error=None
        for _ in range(80):
            if proc.poll() is not None:
                out,err=proc.communicate(timeout=5)
                pytest.fail(f'service exited: {out[-400:]} {err[-600:]}')
            try:
                with urlopen(f'http://127.0.0.1:{port}/api/state',timeout=.5) as res:
                    payload=json.loads(res.read())
                assert payload['config_health']['state']=='defaults'
                assert payload['config_health']['read_only'] is True
                assert payload['update']['enabled'] is False
                assert 'config_health' in payload
                from urllib.error import HTTPError
                from urllib.request import Request
                import re
                with urlopen(f'http://127.0.0.1:{port}/',timeout=2) as ui:
                    page=ui.read().decode('utf-8')
                token=re.search(r'<meta content="([^"]+)" name="local-token"',page)
                assert token
                req=Request(f'http://127.0.0.1:{port}/api/onboarding/complete',
                    data=b'{}',method='POST',headers={
                    'Content-Type':'application/json', 'X-Local-Token':token.group(1)})
                with pytest.raises(HTTPError) as err:
                    urlopen(req,timeout=2)
                assert err.value.code==423
                assert (isolated/'settings.json').read_text()=='{invalid'
                break
            except OSError as exc:
                last_error=exc
                time.sleep(.1)
        else:
            pytest.fail(f'HTTP service did not become ready: {last_error}')
    finally:
        proc.terminate()
        try:proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill();proc.communicate(timeout=5)


def test_doctor_missing_primary_with_existing_db_needs_explicit_restore(tmp_path):
    from tools.windows_config_doctor import main
    data=tmp_path/'data'/'papers.sqlite3'
    data.parent.mkdir()
    data.write_bytes(b'keep')
    assert main(['--root',str(tmp_path),'--check'])==2
    assert restore(tmp_path)==2
    assert restore(tmp_path,use_defaults=True)==0
    assert data.read_bytes()==b'keep'
    assert (tmp_path/'settings.json').exists()
