"""v1.40.1: real HTTP static assets and the simplified navigation surfaces."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

from paper_radar.webapp import Handler as LocalHandler
from paper_radar.public_portal import Portal, make_server

STATIC = Path(__file__).resolve().parents[1] / 'paper_radar' / 'static'


@contextmanager
def serving(server):
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def get(url: str) -> str:
    with urlopen(url, timeout=6) as response:
        assert response.status == 200
        return response.read().decode('utf-8')


def test_local_page_and_shared_status_are_served_via_http():
    server=ThreadingHTTPServer(('127.0.0.1', 0), LocalHandler)
    server.token='test-ui-1401'
    with serving(server) as origin:
        html = get(origin+'/')
        js = get(origin+'/ui_state.js')
        manual=get(origin+'/guide/quickstart')
        assert 'id="runSearch"' in html and '更多操作' in html
        assert 'settings-storage' in html and 'followingBackup' in html
        assert 'taskSummary' in js and 'sourceAdvice' in js
        assert '快速' in manual


def test_public_page_and_shared_status_are_served_via_http(tmp_path):
    portal=Portal(tmp_path/'portal', embedded_workers=False)
    server=make_server('127.0.0.1', 0, portal)
    with serving(server) as origin:
        html=get(origin+'/')
        js=get(origin+'/ui_state.js')
        assert 'id="searchNow"' in html and 'id="jobState"' in html
        assert '/ui_state.js' in html and 'taskSummary' in js


def test_backup_is_in_storage_settings_and_home_has_one_primary_cta():
    html=(STATIC/'index.html').read_text(encoding='utf-8')
    following=html.split('<section class="view" id="following">',1)[1].split('</section>\n<section class="view" id="zotero">',1)[0]
    storage=html.split('<details class="settings-section" id="settings-storage">',1)[1].split('<details class="settings-section" id="settings-advanced">',1)[0]
    home=html.split('<section class="view active" id="home"',1)[1].split('<section class="view" id="dashboard">',1)[0]
    search=html.split('<section class="view" id="dashboard">',1)[1].split('<section class="view" id="tasks">',1)[0]
    assert 'id="followingBackup"' not in following
    assert 'id="followingBackup"' in storage
    assert home.count('id="runSearch"')==0
    assert search.count('id="runSearch"')==1
    assert '<h1>我的研究</h1>' in home
    assert '<h1>找论文</h1>' in search
