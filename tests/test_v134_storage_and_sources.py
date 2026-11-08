"""v1.34: startup/source isolation, safe SQLite initialization, migration."""
from __future__ import annotations
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from unittest.mock import patch

import pytest
from paper_radar.core import DEFAULTS, connect
from paper_radar.storage_layout import resolve_data_root, POINTER_FILE
from paper_radar.storage_health import storage_failure
from tools.storage_doctor import inspect, migrate, database_check
from paper_radar import webapp


def test_sqlite_init_happens_once_and_is_reused_across_readers(tmp_path,monkeypatch):
    path=tmp_path/'papers.sqlite3'
    calls=[]
    initialize=__import__('paper_radar.core',fromlist=['_initialize_schema'])._initialize_schema
    def spy(db):
        calls.append(1)
        return initialize(db)
    monkeypatch.setattr('paper_radar.core._initialize_schema',spy)
    for _ in range(5):
        with connect(path) as db:
            assert db.execute('SELECT count(*) FROM papers').fetchone()[0]==0
    assert len(calls)==1
    assert database_check(path)['quick_check']=='ok'


def test_state_is_recoverable_when_sqlite_disk_io_error(tmp_path, monkeypatch):
    from paper_radar.core import read_config
    cfg=read_config(tmp_path/'settings.json')
    runtime=webapp.Runtime()
    monkeypatch.setattr(webapp,'open_database',lambda: (_ for _ in ()).throw(sqlite3.OperationalError('disk I/O error')))
    stats=runtime.metrics(cfg)
    assert stats['unavailable'] is True
    assert runtime.storage_health['code']=='SQLITE_IO_ERROR'
    assert runtime.storage_health['ok'] is False
    assert 'OneDrive' not in runtime.storage_health['message']


def test_job_rejects_io_error_before_background_thread(monkeypatch):
    runtime=webapp.Runtime()
    # Check the storage error path with a healthy independent test config.
    # Earlier tests can exercise degraded config state in the same process.
    monkeypatch.setattr(webapp, 'read_config', lambda:dict(DEFAULTS))
    monkeypatch.setattr(webapp, 'assert_primary_healthy', lambda path:None)
    monkeypatch.setattr(webapp,'open_database',lambda: (_ for _ in ()).throw(sqlite3.OperationalError('disk I/O error')))
    with pytest.raises(ValueError,match='论文库读写失败'):
        runtime.start('search')
    assert not runtime.busy


def test_bootstrap_and_state_http_with_broken_db(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp,'read_config', lambda:dict(DEFAULTS))
    monkeypatch.setattr(webapp,'config_health', lambda _path:{'state':'ok','read_only':False,'message':''})
    monkeypatch.setattr(webapp,'open_database',lambda: (_ for _ in ()).throw(sqlite3.OperationalError('disk I/O error')))
    monkeypatch.setattr(webapp,'runtime',webapp.Runtime())
    server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
    server.token='test-csrf'
    server.desktop_instance='test'
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'
        with urlopen(base+'/api/bootstrap',timeout=8) as response:
            bootstrap=json.load(response)
        assert bootstrap['sources']
        names={src['name'] for src in bootstrap['sources']}
        assert {'arxiv','openalex','crossref'}<=names
        assert bootstrap['config']['sources']
        with urlopen(base+'/api/state',timeout=8) as response:
            state=json.load(response)
        assert state['stats']['unavailable']
        assert state['storage_health']['code']=='SQLITE_IO_ERROR'
        assert state['config']['sources']
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_migrate_preserves_original_and_sets_pointer_only_after_validation(tmp_path):
    install=tmp_path/'AI4paper'
    install.mkdir()
    storage=install/'data'
    storage.mkdir()
    with sqlite3.connect(storage/'papers.sqlite3') as db:
        db.execute('CREATE TABLE papers (id INTEGER PRIMARY KEY,title TEXT)')
        db.execute('INSERT INTO papers(title) VALUES (?)',('original data',))
    (install/'settings.json').write_text('{"keywords":["important"]}',encoding='utf-8')
    (install/'reports').mkdir()
    (install/'reports'/'readme.md').write_text('report original',encoding='utf-8')
    target=tmp_path/'PaperRadarData'
    receipt=migrate(install,target,confirmed=True)
    assert receipt['original_files_kept']
    assert (install/'settings.json').exists()
    assert (install/'data'/'papers.sqlite3').exists()
    assert (install/POINTER_FILE).read_text().strip()==str(target)
    assert resolve_data_root(install,create=False)==target
    assert (target/'settings.json').read_text()==(install/'settings.json').read_text()
    with sqlite3.connect(target/'data'/'papers.sqlite3') as db:
        assert db.execute('SELECT title FROM papers').fetchone()[0]=='original data'
    assert database_check(target/'data'/'papers.sqlite3')['quick_check']=='ok'
    assert (target/'reports'/'readme.md').read_text()=='report original'


def test_migrate_refuses_nonempty_target_without_modifying_source(tmp_path):
    install=tmp_path/'install';install.mkdir()
    (install/'settings.json').write_text('important')
    target=tmp_path/'other';target.mkdir();(target/'important.txt').write_text('keep')
    with pytest.raises(FileExistsError):migrate(install,target,confirmed=True)
    assert not (install/POINTER_FILE).exists()
    assert (target/'important.txt').read_text()=='keep'
    assert (install/'settings.json').read_text()=='important'


def test_no_overwrite_without_explicit_consent(tmp_path):
    install=tmp_path/'install';install.mkdir()
    with pytest.raises(ValueError,match='--yes'):
        migrate(install,tmp_path/'new-home')
    assert not (install/POINTER_FILE).exists()


def test_settings_source_selection_is_visible_without_state_api():
    root=Path(__file__).resolve().parent.parent
    html=(root/'paper_radar/static/index.html').read_text('utf-8')
    js=(root/'paper_radar/static/app.js').read_text('utf-8')
    assert html.index('id="sourceOptions"') < html.index('id="runSearch"')
    assert 'value="arxiv" checked' in html
    assert 'id="storageRecoveryBanner"' in html
    assert 'loadBootstrap()' in js
    assert 'requireSearchSources()' in js
    assert 'renderStorageHealth' in js


def test_storage_errors_do_not_expose_raw_database_exceptions(tmp_path):
    err=sqlite3.OperationalError('disk I/O error secret_database_filename.db')
    result=storage_failure(err,tmp_path)
    assert result['code']=='SQLITE_IO_ERROR'
    assert 'secret_database_filename' not in result['message']
