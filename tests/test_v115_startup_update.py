# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Offline, end-to-end security and recovery tests for pre-launch updating."""
from __future__ import annotations

from io import BytesIO
import hashlib
import json
import sqlite3
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError
import zipfile

import pytest

from paper_radar.startup_update import (
    StartupUpdater, UpdateError, MAX_ARCHIVE, parsed_version, release_asset,
    unpack_verified, read_json, atomic_json, _safe_member,
)

VERSION = '1.15.1'
TAG = 'v' + VERSION
ROOT_URL = 'https://github.com/QiQiWan/Paper-Radar/releases'
ASSET_NAME = f'Paper-Radar_v{VERSION}_Source.zip'
ASSET_URL = f'{ROOT_URL}/download/{TAG}/{ASSET_NAME}'


def make_zip(*, version=VERSION, members=None, protocol=True):
    initial = {
        'paper_radar/__init__.py': '__version__ = "' + version + '"\n' + ('__startup_protocol__ = 1\n' if protocol else ''),
        'paper_radar/startup_update.py': 'def main():\n    return 2\n',
        'main.py':'print("new app")\n',
        'startup.py':'print("bootstrap")\n',
        'pyproject.toml':'[project]\nname = "paper-radar-local"\nversion = "' + version + '"\n',
        'requirements.txt':'keyring>=25\n',
        'README.md':'new version\n',
        'start_windows.bat':'@echo off\nrem new bat\n',
        'start_macos_linux.sh':'#!/bin/sh\n',
    }
    if members:
        initial.update(members)
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as zf:
        for name, content in initial.items():
            zf.writestr('Paper-Radar/'+name,content)
    return stream.getvalue()


def release_payload(blob, *, tag=TAG, asset=ASSET_NAME, digest=None):
    sha = hashlib.sha256(blob).hexdigest() if digest is None else digest
    return {
        'tag_name':tag,
        'html_url':f'{ROOT_URL}/tag/{tag}',
        'draft':False,'prerelease':False,
        'assets':[{'name':asset,'state':'uploaded','size':len(blob),
                   'digest':'sha256:'+sha,
                   'browser_download_url':f'{ROOT_URL}/download/{tag}/{asset}'}],
    }


class FakeResponse(BytesIO):
    def __init__(self, body, url):
        super().__init__(body)
        self.url = url


class FakeOpen:
    def __init__(self, info, zip_blob):
        self.info=info
        self.zip_blob=zip_blob
        self.requests=[]

    def __call__(self, req, timeout=None):
        url=req.full_url
        self.requests.append((url,timeout))
        if url.startswith('https://api.github.com/'):
            return FakeResponse(json.dumps(self.info).encode('utf-8'),url)
        return FakeResponse(self.zip_blob,url)


@pytest.fixture
def installation(tmp_path):
    root=tmp_path/'app'
    (root/'paper_radar').mkdir(parents=True)
    (root/'paper_radar/__init__.py').write_text('__version__ = "1.15.0"\n__startup_protocol__ = 1\n')
    (root/'paper_radar/stale_module.py').write_text('removed from release')
    (root/'README.md').write_text('original README')
    (root/'main.py').write_text('print("old")')
    (root/'settings.json').write_text('{"api_key":"local-only","user":"test"}')
    (root/'data').mkdir()
    with sqlite3.connect(root/'data'/'papers.sqlite3') as db:
        db.execute('CREATE TABLE papers (title TEXT)')
        db.execute('INSERT INTO papers VALUES (?)',('personal-private-database',))
    (root/'downloads').mkdir()
    (root/'downloads'/'paper.pdf').write_bytes(b'%PDF-private')
    (root/'reports').mkdir()
    (root/'reports'/'weekly.md').write_text('my private report')
    (root/'start_windows.bat').write_text('old launcher')
    return root


def test_version_and_asset_selection():
    blob=make_zip()
    assert parsed_version('v1.15.1') > parsed_version('1.15.0')
    assert parsed_version('v1.15.1-rc1') is None
    assert release_asset(release_payload(blob), '1.15.0')['sha256'] == hashlib.sha256(blob).hexdigest()
    assert release_asset(release_payload(blob), VERSION) is None
    assert release_asset(release_payload(blob, tag='v1.14.0'), VERSION) is None


def test_strict_digest_and_origin():
    blob=make_zip()
    meta=release_payload(blob)
    meta['assets'][0]['digest']=None
    with pytest.raises(UpdateError,match='SHA-256'):
        release_asset(meta,'1.15.0')
    meta=release_payload(blob)
    meta['assets'][0]['browser_download_url']='https://example.com/release.zip'
    with pytest.raises(UpdateError,match='地址'):
        release_asset(meta,'1.15.0')
    meta=release_payload(blob)
    meta['draft']=True
    with pytest.raises(UpdateError):
        release_asset(meta,'1.15.0')


@pytest.mark.parametrize('name',[
    'Paper-Radar/../data/settings.json', 'Paper-Radar/data/papers.sqlite3',
    'Paper-Radar/settings.json','Paper-Radar/.venv/bin/python',
    '/etc/passwd','Paper-Radar/C:/hack','Paper-Radar/paper_radar/./foo.py',
    'Paper-Radar//paper_radar/main.py',
])
def test_unsafe_zip_member_rejected(name):
    with pytest.raises(UpdateError):
        _safe_member(name,'Paper-Radar')


def test_launchers_present_but_skipped_in_update_zip(tmp_path):
    contents=make_zip()
    path=tmp_path/'archive.zip'
    path.write_bytes(contents)
    target=tmp_path/'staged'
    unpack_verified(path,target,VERSION)
    assert (target/'paper_radar/__init__.py').exists()
    assert not (target/'start_windows.bat').exists()
    assert not (target/'start_macos_linux.sh').exists()


def test_wrong_version_or_boot_protocol_rejected(tmp_path):
    path=tmp_path/'src.zip'
    path.write_bytes(make_zip(version='1.18.0'))
    with pytest.raises(UpdateError,match='版本'):
        unpack_verified(path,tmp_path/'out',VERSION)
    path.write_bytes(make_zip(protocol=False))
    with pytest.raises(UpdateError,match='协议'):
        unpack_verified(path,tmp_path/'out2',VERSION)


def test_invalid_python_source_rejected(tmp_path):
    path=tmp_path/'src.zip'
    path.write_bytes(make_zip(members={'paper_radar/bad.py':'def bad(:\n'}))
    with pytest.raises(UpdateError,match='Python'):
        unpack_verified(path,tmp_path/'out',VERSION)


def test_offline_start_keeps_user_data(installation):
    def offline(req,timeout=None):
        raise URLError('unreachable')
    update=StartupUpdater(installation,opener=offline)
    update.is_web_running=lambda:False
    status=update.run()
    assert status['state']=='error' and '网络' in status['message']
    assert (installation/'main.py').read_text() == 'print("old")'
    with sqlite3.connect(installation/'data/papers.sqlite3') as db:
        assert db.execute('SELECT title FROM papers').fetchone()[0]=='personal-private-database'
    assert not update.journal_path.exists()


def test_auto_update_end_to_end(installation):
    blob=make_zip(members={'paper_radar/new_module.py':'value = 19\n'})
    opener=FakeOpen(release_payload(blob),blob)
    update=StartupUpdater(installation,opener=opener)
    update.is_web_running=lambda:False
    result=update.run()
    assert result['state']=='updated'
    assert result['installed_version']==VERSION
    assert (installation/'main.py').read_text()=='print("new app")\n'
    assert (installation/'paper_radar/new_module.py').exists()
    assert not (installation/'paper_radar/stale_module.py').exists()
    assert (installation/'settings.json').read_text().find('local-only')>=0
    assert (installation/'start_windows.bat').read_text()=='old launcher'
    with sqlite3.connect(installation/'data/papers.sqlite3') as db:
        assert db.execute('SELECT title FROM papers').fetchone()[0]=='personal-private-database'
    assert (installation/'downloads/paper.pdf').read_bytes()==b'%PDF-private'
    assert (installation/'reports/weekly.md').read_text()=='my private report'
    assert not update.journal_path.exists()
    assert any(update.base.joinpath('backups').iterdir())


def test_mismatched_checksum_aborts_before_install(installation):
    blob=make_zip()
    opener=FakeOpen(release_payload(blob,digest='a'*64),blob)
    update=StartupUpdater(installation,opener=opener)
    update.is_web_running=lambda:False
    assert update.run()['state']=='error'
    assert (installation/'README.md').read_text()=='original README'
    assert not update.journal_path.exists()


def test_already_running_skips_network(installation):
    def no_request(*args,**kwargs):
        raise AssertionError('web service must not query network')
    update=StartupUpdater(installation,opener=no_request)
    update.is_web_running=lambda:True
    assert update.run()['state']=='running'
    assert (installation/'main.py').read_text()=='print("old")'


def test_disabled_skips_network(installation):
    update=StartupUpdater(installation,opener=lambda *_args,**_kw:None)
    assert update.run(enabled=False)['state']=='disabled'


def test_automatic_rollback_after_failed_start(installation):
    blob=make_zip()
    update=StartupUpdater(installation,opener=FakeOpen(release_payload(blob),blob))
    update.is_web_running=lambda:False
    assert update.run()['state']=='updated'
    assert update.mark_failed_and_rollback(failed_version=VERSION,reason='missing dependency')
    assert (installation/'main.py').read_text()=='print("old")'
    assert (installation/'README.md').read_text()=='original README'
    assert read_json(update.failed_path)['version']==VERSION
    assert update.run()['state']=='skipped_failed'


def test_recover_interrupted_install(installation):
    update=StartupUpdater(installation)
    tx='20261104T100000-1234abcd'
    backup=update.base/'backups'/tx/'previous'
    backup.mkdir(parents=True)
    (backup/'main.py').write_text('print("old")')
    (installation/'main.py').write_text('print("half installed")')
    source=update.base/'staging'/tx/'source'
    source.mkdir(parents=True)
    atomic_json(update.journal_path,{'phase':'installing','id':tx,'version':VERSION,
                                    'entries':['main.py'], 'new_items':[]})
    assert update.recover_if_interrupted()
    assert (installation/'main.py').read_text()=='print("old")'
    assert not update.journal_path.exists()


def test_corrupt_journal_stops_launch(installation):
    from paper_radar.startup_update import RecoveryError
    update=StartupUpdater(installation)
    atomic_json(update.journal_path,{'phase':'installing','id':'../../unsafe','entries':['main.py']})
    update.is_web_running=lambda:False
    assert update.run(enabled=False)['state']=='recovery_failed'
    assert (installation/'main.py').read_text()=='print("old")'


def test_startup_runner_falls_back_after_failure(tmp_path):
    import startup
    outcomes=[]
    class FakeUpdater:
        def run(self,**kwargs):
            return {'state':'updated','message':'updated','previous_version':'1.15.0'}
        def local_version(self):
            return VERSION
        def mark_failed_and_rollback(self,**kwargs):
            outcomes.append('rollback')
            return True
        def status(self,*args,**kwargs):
            outcomes.append('status')
    class Result:
        returncode=0
    def runner(cmd,**kwargs):
        if cmd[-1] in ('--web','--launch'):
            outcomes.append('launch')
            return SimpleNamespace(returncode=1 if outcomes.count('launch')==1 else 0)
        return Result()
    (tmp_path/'requirements.txt').write_text('keyring>=25')
    assert startup.startup(tmp_path,updater=FakeUpdater(),runner=runner)==0
    assert outcomes==['launch','rollback','launch']


def test_startup_halts_on_inconsistent_recovery(tmp_path):
    import startup
    class FakeUpdater:
        def run(self,**kwargs):
            return {'state':'recovery_failed','message':'repair required'}
    assert startup.startup(tmp_path,updater=FakeUpdater())==2


def test_rollback_restores_sqlite_snapshot_and_settings(installation):
    blob=make_zip()
    update=StartupUpdater(installation,opener=FakeOpen(release_payload(blob),blob))
    update.is_web_running=lambda:False
    assert update.run()['state']=='updated'
    with sqlite3.connect(installation/'data/papers.sqlite3') as db:
        db.execute('UPDATE papers SET title=?',('modified by failed new version',))
    (installation/'settings.json').write_text('{"api_key":"modified"}')
    assert update.mark_failed_and_rollback(failed_version=VERSION,reason='post migration startup failed')
    with sqlite3.connect(installation/'data/papers.sqlite3') as db:
        assert db.execute('SELECT title FROM papers').fetchone()[0]=='personal-private-database'
    assert 'local-only' in (installation/'settings.json').read_text()


def test_manual_rollback_preserves_recent_user_data(installation):
    blob=make_zip()
    update=StartupUpdater(installation,opener=FakeOpen(release_payload(blob),blob))
    update.is_web_running=lambda:False
    result=update.run()
    update.status('success','new version running',transaction_id=result['transaction_id'])
    with sqlite3.connect(installation/'data/papers.sqlite3') as db:
        db.execute('UPDATE papers SET title=?',('recent work stays',))
    assert update.mark_failed_and_rollback(failed_version=VERSION,
                                          reason='manual rollback',restore_user_data=False)
    with sqlite3.connect(installation/'data/papers.sqlite3') as db:
        assert db.execute('SELECT title FROM papers').fetchone()[0]=='recent work stays'


def test_config_auto_update_switch_roundtrip():
    from paper_radar.core import validate_config, DEFAULTS
    cfg=validate_config({**DEFAULTS,'auto_update_on_startup':False})
    assert cfg['auto_update_on_startup'] is False
    assert validate_config(DEFAULTS)['auto_update_on_startup'] is False


def test_db_snapshot_too_large_cancels_update(installation,monkeypatch):
    import paper_radar.startup_update as module
    blob=make_zip()
    update=StartupUpdater(installation,opener=FakeOpen(release_payload(blob),blob))
    update.is_web_running=lambda:False
    monkeypatch.setattr(module,'MAX_DB_SNAPSHOT',0)
    assert update.run()['state']=='error'
    assert (installation/'main.py').read_text()=='print("old")'


def test_resume_post_install_boot_validation_after_power_loss(installation):
    blob=make_zip()
    update=StartupUpdater(installation,opener=FakeOpen(release_payload(blob),blob))
    update.is_web_running=lambda:False
    done=update.run()
    assert done['state']=='updated'
    network_must_not_run=StartupUpdater(installation,opener=lambda *_a,**_kw:(_ for _ in ()).throw(AssertionError('network queried')))
    network_must_not_run.is_web_running=lambda:False
    resumed=network_must_not_run.run()
    assert resumed['state']=='updated'
    assert resumed['transaction_id']==done['transaction_id']
    assert network_must_not_run.mark_failed_and_rollback(failed_version=VERSION,reason='crashed before boot')
    assert (installation/'main.py').read_text()=='print("old")'


def test_committed_transaction_recovers_pending_boot_validation(installation):
    update=StartupUpdater(installation)
    tx='20261104T100000-1234abcd'
    backup=update.base/'backups'/tx/'previous'
    backup.mkdir(parents=True)
    (backup/'main.py').write_text('print("old")')
    atomic_json(update.journal_path,{'phase':'committed','id':tx,'version':VERSION,
          'previous_version':'1.15.0','release_url':ROOT_URL+'/tag/'+TAG,
          'entries':['main.py'],'new_items':[]})
    (installation/'paper_radar/__init__.py').write_text('__version__ = "'+VERSION+'"\n__startup_protocol__ = 1\n')
    update.recover_if_interrupted()
    state=read_json(update.status_path)
    assert state['state']=='updated' and state['transaction_id']==tx
    assert not update.journal_path.exists()


def test_force_cannot_modify_running_workbench(installation):
    update=StartupUpdater(installation,opener=lambda *_a,**_k:(_ for _ in ()).throw(AssertionError('network queried')))
    update.is_web_running=lambda:True
    assert update.run(force=True)['state']=='running'


def test_background_running_lock_blocks_update(installation):
    (installation/'data/running.lock').write_text('busy')
    update=StartupUpdater(installation,opener=lambda *_a,**_k:(_ for _ in ()).throw(AssertionError('network queried')))
    update.is_web_running=lambda:False
    assert update.run(force=True)['state']=='running'
