# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.40.6 UI checks: asset routing, stable controls and responsive layout.

Visual checks render the actual local HTML/CSS without network dependencies.
This does not represent a live Windows-browser acceptance test.
"""
from __future__ import annotations

from pathlib import Path
import importlib.util
import shutil
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'paper_radar' / 'static'


def test_visual_assets_routed_and_included_in_document():
    local = (STATIC / 'index.html').read_text(encoding='utf-8')
    portal = (STATIC / 'portal.html').read_text(encoding='utf-8')
    server = (ROOT / 'paper_radar' / 'webapp.py').read_text(encoding='utf-8')
    public = (ROOT / 'paper_radar' / 'public_portal.py').read_text(encoding='utf-8')
    assert '<link href="/ui_polish.css" rel="stylesheet"/>' in local
    assert '<link href="/portal_polish.css" rel="stylesheet"/>' in portal
    assert "'/ui_polish.css'" in server
    assert "'/portal_polish.css'" in public
    assert (STATIC / 'ui_polish.css').is_file()
    assert (STATIC / 'portal_polish.css').is_file()
    from paper_radar import __version__
    assert f'version = "{__version__}"' in (ROOT / 'pyproject.toml').read_text(encoding='utf-8')


def test_library_toolbar_maintains_existing_action_ids():
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    library = html.split('<section class="view" id="library">', 1)[1].split('<section class="view" id="downloads">', 1)[0]
    assert 'class="library-primary-filters"' in library
    assert 'class="library-filter-grid"' in library
    for name in (
        'librarySearch', 'libraryFilter', 'libraryScope', 'libraryGroup',
        'librarySort', 'downloadSelected', 'downloadStarred', 'selectedCount',
        'localBulkStar', 'localBulkSkip', 'localBulkDone',
        'localExportStarred', 'localExportActions', 'libraryList',
        'libraryStatus', 'libraryPagination',
    ):
        assert len(re.findall(r'id="'+re.escape(name)+r'"', library)) == 1, name
    js = (STATIC / 'app.js').read_text(encoding='utf-8')
    assert 'style="width:116px;padding:5px 7px;font-size:11px"' not in js
    assert 'class="select read-select" aria-label="阅读状态"' in js


def test_guide_and_portal_font_sizes_are_part_of_the_design_system():
    local = (STATIC / 'ui_polish.css').read_text(encoding='utf-8')
    public = (STATIC / 'portal_polish.css').read_text(encoding='utf-8')
    guide = (STATIC / 'guide.css').read_text(encoding='utf-8')
    assert '--ui-space-4: 16px' in local
    assert '.library-filter-grid' in local
    assert '.local-reading-note-fields' in local
    assert '.navigation .nav-link span:not(.nav-symbol)' in local
    assert '.library-filters' in public
    assert '.report-content' in public
    assert '.guide-content p, .guide-content li' in guide


@pytest.mark.skipif(
    importlib.util.find_spec('playwright') is None or not shutil.which('chromium'),
    reason='Playwright and Chromium are optional visual QA dependencies',
)
@pytest.mark.parametrize('width,height', [(1440, 900), (1024, 800), (390, 844)])
def test_local_ui_sizes_and_mobile_overflow(width: int, height: int):
    from playwright.sync_api import sync_playwright

    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    # Visual-only view: scripts and API state are intentionally not required.
    html = re.sub(r'<script\b[^>]*\bsrc="[^"]+"[^>]*>\s*</script>', '', html)
    html = re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>', '', html)
    css = '\n'.join((STATIC / name).read_text(encoding='utf-8')
                    for name in ('app.css', 'settings.css', 'ui_polish.css'))

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=shutil.which('chromium'),
                                    headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': width, 'height': height})
        try:
            page.set_content(html, wait_until='domcontentloaded')
            page.add_style_tag(content=css)
            page.locator('.library-more-filters').evaluate('(el)=>{el.open=true}')
            page.evaluate("""() => {
              document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active',el.id==='library'));
              document.getElementById('libraryList').innerHTML = `<details class="paper-group" open>
                <summary>Operator learning</summary><article class="paper-item">
                <a class="paper-title" href="#">Neural operator benchmark</a>
                <div class="paper-meta">arXiv · 2026</div>
                <div class="paper-actions"><select class="select read-select"><option>未阅读</option></select></div>
                <details class="local-reading-note" open><summary>个人阅读笔记</summary>
                <div class="local-reading-note-fields"><label>笔记<textarea class="input local-note-input"></textarea></label>
                <label>下一步行动<select class="select local-note-action"><option>精读</option></select></label></div>
                </details></article></details>`;
            }""")
            for selector in ('#librarySearch','#libraryFilter','#libraryScope',
                             '#libraryGroup','#librarySort'):
                assert page.locator(selector).evaluate('(el)=>el.getBoundingClientRect().height') >= 41
                assert page.locator(selector).evaluate('(el)=>parseFloat(getComputedStyle(el).fontSize)') >= 14
            assert page.locator('.read-select').evaluate('(el)=>parseFloat(getComputedStyle(el).fontSize)') >= 14
            assert page.locator('.paper-title').evaluate('(el)=>parseFloat(getComputedStyle(el).fontSize)') >= 15
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            columns = page.locator('.library-filter-grid').evaluate('(el)=>getComputedStyle(el).gridTemplateColumns.split(" ").length')
            if width >= 1140:
                assert columns == 3
            if width <= 670:
                assert columns == 1
                assert page.locator('.navigation .nav-link span:not(.nav-symbol)').first.is_visible()
            # The long note remains readable on narrow displays.
            assert page.locator('.local-note-input').evaluate('(el)=>el.getBoundingClientRect().width') > 190
        finally:
            browser.close()
