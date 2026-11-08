# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Illustrated help, safe Markdown tables, bookmarkable routes, opt-in AI smoke."""
from __future__ import annotations

from pathlib import Path
from threading import Thread
import http.client
import json
import re
import subprocess

import pytest

from paper_radar.core import DEFAULTS, read_config, validate_config
from paper_radar.help_pages import GUIDE_ASSETS, GUIDES, markdown_html, render_guide

ROOT = Path(__file__).parents[1]
STATIC = ROOT / 'paper_radar/static'


def test_help_markdown_table_is_real_table():
    text = (ROOT / 'docs/USER_GUIDE.md').read_text(encoding='utf-8').split('<!-- GUIDE:sources -->', 1)[1].split('<!-- GUIDE:model -->', 1)[0]
    result = markdown_html(text)
    assert '<table>' in result and '</tbody></table>' in result
    assert result.count('<tr>') >= 5
    assert '<th scope="col">' in result
    assert 'Springer Nature' in result
    assert '<p>| 来源 |' not in result
    assert '<p>| --- |' not in result


def test_markdown_tables_escape_injected_cells_and_preserve_escaped_pipe():
    html = markdown_html('| 名称 | 内容 |\n| --- | --- |\n| x | <img src=x onerror=alert(1)> |\n| y | a\\|b |')
    assert 'onerror=alert(1)' in html and '<img src=x' not in html
    assert 'a|b' in html and 'a</td>' not in html
    assert '<table>' in html
    assert '<script>' not in markdown_html('<script>run()</script>')
    assert '<a href="javascript:' not in markdown_html('[Bad](javascript:alert)')


def test_safe_figure_rendering_and_no_arbitrary_file_or_script():
    assert '<img src="/guide/assets/workflow.svg"' in markdown_html('![图](assets/workflow.svg)')
    assert '<img' not in markdown_html('![bad](assets/../../secrets.svg)')
    assert '<img' not in markdown_html('![bad](https://example.com/foo.svg)')
    assert '<img' not in markdown_html('![bad](assets/unlisted.svg)')
    assert '<a href="/app/library"' in markdown_html('[文献](app:library)')
    assert '<a href="/library"' in markdown_html('[文献](app:library)', public=True)


def test_all_reader_guides_have_diagram_and_internal_links():
    for slug in GUIDES:
        local = render_guide(slug)
        public = render_guide(slug, public=True)
        assert local and b'<figure' in local, slug
        assert public and b'<figure' in public, slug
        assert b'/guide/ai' in local
    assert b'href="/app/dashboard"' in render_guide('quickstart')
    assert b'href="/workspace"' in render_guide('quickstart', public=True)
    assert b'guide-mobile-nav' in render_guide('sources')
    for name in GUIDE_ASSETS:
        assert (ROOT / 'docs/assets' / name).exists()
    assert render_guide('../../README') is None


def _get(port, path):
    client = http.client.HTTPConnection('127.0.0.1', port, timeout=6)
    client.request('GET', path, headers={'Host':f'127.0.0.1:{port}'})
    response = client.getresponse()
    result = response.status, response.getheader('Content-Type'), response.read()
    client.close()
    return result


@pytest.fixture
def local_portal():
    from http.server import ThreadingHTTPServer
    from paper_radar.webapp import Handler
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.token = 'test-token'
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown();server.server_close();thread.join(timeout=3)


def test_local_pages_support_direct_url_and_refresh(local_portal):
    for page in ['dashboard','library','tasks','reports','settings','downloads','zotero']:
        status,_,body = _get(local_portal, '/app/'+page)
        assert status == 200 and b'test-token' in body, page
    status,kind,body = _get(local_portal, '/guide/sources')
    assert status == 200 and b'<table>' in body and b'/guide/assets/sources.svg' in body
    assert _get(local_portal, '/guide/assets/sources.svg')[:2] == (200,'image/svg+xml')
    assert _get(local_portal, '/guide/assets/not-listed.svg')[0] == 404
    assert _get(local_portal, '/app/no-such-route')[0] == 404


def test_public_pages_and_guides_are_bookmarkable_without_exposing_user_data(tmp_path):
    from paper_radar.public_portal import Portal, make_server
    service = Portal(tmp_path/'portal',embedded_workers=False)
    server = make_server('127.0.0.1',0,service)
    thread=Thread(target=server.serve_forever, daemon=True);thread.start()
    try:
        for page in ['workspace','library','tasks','reports','settings','account']:
            status,content,body=_get(server.server_port,'/'+page+'?topic=default')
            assert status==200 and b'portal.js' in body and content.startswith('text/html'),page
        status,kind,body=_get(server.server_port,'/guide/sources')
        assert status==200 and b'<table>' in body
        assert _get(server.server_port,'/guide/assets/portal.svg')[0]==200
        assert _get(server.server_port,'/api/public/topics')[0]==401
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_new_install_recommends_ai_but_existing_opt_out_remains(tmp_path):
    assert validate_config(DEFAULTS)['ai_enabled'] is True
    assert validate_config(DEFAULTS)['ai_rerank_enabled'] is True
    settings=tmp_path/'settings.json'
    settings.write_text(json.dumps({'ai_enabled':False, 'ai_rerank_enabled':False}),encoding='utf-8')
    config=read_config(settings)
    assert config['ai_enabled'] is False and config['ai_rerank_enabled'] is False


def test_ai_prompt_does_not_authorize_public_cloud_model():
    local=(STATIC/'index.html').read_text(encoding='utf-8')
    portal=(STATIC/'portal.html').read_text(encoding='utf-8')
    for key in ('aiDiscovery','aiDiscoverySkip','aiDiscoverySetup','aiEnabled','aiRerankEnabled'):
        assert 'id="'+key+'"' in local
    assert re.search(r'<input id="cloudAnalysis" type="checkbox"\s*/>',portal)
    assert 'apiKey' in local and '/guide/ai' in local and '/guide/ai' in portal
    js=(STATIC/'app.js').read_text(encoding='utf-8')
    assert "localStorage.setItem('paper-radar-ai-prompt-dismissed','1')" in js
    assert "window.addEventListener('popstate'" in js
    assert "R.write" in (STATIC/'portal.js').read_text(encoding='utf-8')


def test_all_frontend_files_syntax_check():
    for name in ['app.js','portal.js']:
        subprocess.run(['node','--check',str(STATIC/name)],check=True,capture_output=True)
