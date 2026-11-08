"""Release gates for Windows storage migration and local UI fault isolation."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from paper_radar.storage_layout import resolve_data_root, POINTER_FILE
from tools.storage_doctor import migrate, database_check, _copy_plain


def _make_install(tmp_path):
    root=tmp_path/'AI4paper';root.mkdir()
    (root/'data').mkdir()
    (root/'settings.json').write_text('{"keywords":["mechanics"]}',encoding='utf-8')
    return root


def test_sqlite_wal_commits_are_preserved_by_verified_backup(tmp_path):
    root=_make_install(tmp_path)
    source=root/'data'/'papers.sqlite3'
    old=sqlite3.connect(source)
    old.execute('PRAGMA journal_mode=WAL')
    old.execute('PRAGMA wal_autocheckpoint=0')
    old.execute('CREATE TABLE papers(title TEXT)')
    for i in range(37):
        old.execute('INSERT INTO papers VALUES (?)',(f'paper-{i}',))
    old.commit()
    assert source.with_name('papers.sqlite3-wal').exists()
    destination=tmp_path/'local-appdata'/'PaperRadar'
    migrate(root,destination,confirmed=True)
    with sqlite3.connect(destination/'data'/'papers.sqlite3') as new:
        assert new.execute('SELECT count(*) FROM papers').fetchone()[0]==37
    assert database_check(destination/'data'/'papers.sqlite3')['quick_check']=='ok'
    assert source.exists()
    old.close()


def test_fallback_copy_is_opt_in_and_retains_original(tmp_path,monkeypatch):
    from tools import storage_doctor
    root=_make_install(tmp_path)
    with sqlite3.connect(root/'data'/'papers.sqlite3') as conn:
        conn.execute('CREATE TABLE records(value TEXT)')
        conn.execute('INSERT INTO records VALUES ("saved")')
    def fail(*args,**kw):
        raise sqlite3.OperationalError('disk I/O error')
    monkeypatch.setattr(storage_doctor,'_copy_db',fail)
    with pytest.raises(sqlite3.OperationalError):
        migrate(root,tmp_path/'without-fallback',confirmed=True)
    assert not (root/POINTER_FILE).exists()
    target=tmp_path/'with-offline-recovery'
    migrate(root,target,confirmed=True,offline_recovery=True)
    with sqlite3.connect(target/'data'/'papers.sqlite3') as conn:
        assert conn.execute('SELECT value FROM records').fetchone()[0]=='saved'
    assert (root/'data'/'papers.sqlite3').exists()


def test_migrate_copy_error_never_switches_pointer(tmp_path,monkeypatch):
    from tools import storage_doctor
    root=_make_install(tmp_path)
    original=storage_doctor._copy_plain
    def fail(*args,**kw):
        raise PermissionError('source still syncing')
    monkeypatch.setattr(storage_doctor,'_copy_plain',fail)
    with pytest.raises(PermissionError):
        migrate(root,tmp_path/'target',confirmed=True)
    assert not (root/POINTER_FILE).exists()
    assert (root/'settings.json').exists()


def test_sync_destination_is_rejected_without_writes(tmp_path):
    root=_make_install(tmp_path)
    target=tmp_path/'OneDrive'/'PaperRadar'
    with pytest.raises(ValueError,match='同步目录'):
        migrate(root,target,confirmed=True)
    assert not (root/POINTER_FILE).exists()
    assert not target.exists()


def test_explicit_home_environment_does_not_modify_old_data(tmp_path):
    root=_make_install(tmp_path)
    chosen=tmp_path/'new-home'
    actual=resolve_data_root(root,env={'PAPER_RADAR_USER_HOME':str(chosen)},frozen=False)
    assert actual==chosen
    assert chosen.is_dir()
    assert (root/'settings.json').exists()
    assert not (root/POINTER_FILE).exists()


def test_windows_readable_files_also_work_on_python_310(tmp_path):
    source=tmp_path/'orig';target=tmp_path/'copy'
    source.write_bytes(b'config-restore-2026')
    _copy_plain(source,target)
    assert target.read_bytes()==source.read_bytes()


def test_search_preflight_accepts_healthy_database_and_dispatches(monkeypatch,tmp_path):
    """A valid source configuration with a working DB must reach task dispatch."""
    from paper_radar import core, webapp
    root=tmp_path/'data';root.mkdir()
    db_path=root/'papers.sqlite3'
    with core.open_database(db_path):
        pass
    monkeypatch.setattr(webapp,'open_database',lambda:core.open_database(db_path))
    monkeypatch.setattr(webapp,'assert_primary_healthy',lambda path:None)
    started=[]
    class StubThread:
        def __init__(self,*,target,args,daemon):
            self.target=target;self.args=args;self.daemon=daemon
        def start(self):started.append(self.args)
    monkeypatch.setattr(webapp,'Thread',StubThread)
    runtime=webapp.Runtime()
    runtime.start('search')
    assert started and started[0][0]=='search'
    assert runtime.storage_health['ok'] is True


def test_fresh_install_settings_persist_before_database_is_created(tmp_path):
    from paper_radar.core import DEFAULTS, validate_config, read_config
    from paper_radar.config_safety import initialize_fresh_workspace, config_health
    root=tmp_path/'new-install';root.mkdir()
    data=root/'data';data.mkdir()
    (data/'.desktop_supervision.key').write_text('generated-per-user-local-only')
    settings=root/'settings.json'
    assert initialize_fresh_workspace(settings,DEFAULTS,validate_config)
    assert settings.is_file()
    with sqlite3.connect(data/'papers.sqlite3') as db:
        db.execute('CREATE TABLE paper_test(title TEXT)')
    assert read_config(settings)['sources']
    assert config_health(settings)['read_only'] is False
    assert not initialize_fresh_workspace(settings,DEFAULTS,validate_config)


def test_missing_settings_with_existing_db_or_reports_is_never_recreated(tmp_path):
    from paper_radar.core import DEFAULTS,validate_config
    from paper_radar.config_safety import initialize_fresh_workspace
    root=tmp_path/'old';root.mkdir();(root/'data').mkdir()
    (root/'data'/'papers.sqlite3').write_bytes(b'old sensitive database')
    assert not initialize_fresh_workspace(root/'settings.json',DEFAULTS,validate_config)
    assert not (root/'settings.json').exists()
    (root/'data'/'papers.sqlite3').unlink()
    (root/'reports').mkdir();(root/'reports'/'important.md').write_text('research result')
    assert not initialize_fresh_workspace(root/'settings.json',DEFAULTS,validate_config)
    assert not (root/'settings.json').exists()
