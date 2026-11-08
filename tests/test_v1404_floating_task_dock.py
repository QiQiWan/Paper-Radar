# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Regression for the local viewport-fixed task status drawer."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import re
import shutil
import pytest

STATIC = Path(__file__).resolve().parents[1] / 'paper_radar' / 'static'


def test_task_dock_is_outside_top_workspace_and_controls_are_unique():
    html = (STATIC / 'index.html').read_text('utf8')
    js = (STATIC / 'app.js').read_text('utf8')
    css = (STATIC / 'app.css').read_text('utf8')
    assert html.index('id="taskCenter"') > html.index('</main>')
    assert html.index('id="taskCenter"') < html.index('<dialog')
    assert 'id="taskDockToggle"' in html and 'aria-expanded="false"' in html
    assert 'id="taskDockBody"' in html and 'inert' in html
    assert 'id="taskDockDismiss"' in html and 'id="taskDockOpenTasks"' in html
    assert 'id="retrievalCountsDetails"' in html and 'id="retrievalTraceDetails"' in html
    assert len(re.findall(r'id="taskCenter"', html)) == 1
    assert 'position:fixed' in css and 'env(safe-area-inset-bottom' in css
    assert 'prefers-reduced-motion:reduce' in css
    assert 'setTaskDockExpanded' in js and 'dismissedTaskMarker' in js
    assert '页面顶部进度' not in js


@pytest.mark.skipif(
    importlib.util.find_spec('playwright') is None or not shutil.which('chromium'),
    reason='Playwright and Linux Chromium are optional browser QA dependencies',
)
@pytest.mark.parametrize('width,height', [(1440, 900), (375, 780)])
def test_task_drawer_interactions_and_mobile_bounds(width, height):
    from playwright.sync_api import sync_playwright

    html = (STATIC / 'index.html').read_text('utf8')
    html = re.sub(r'<script\b[^>]*\bsrc="[^"]+"[^>]*>\s*</script>', '', html)
    html = re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>', '', html)
    styles = '\n'.join((STATIC / f).read_text('utf8') for f in ('app.css', 'settings.css', 'ui_polish.css'))

    with sync_playwright() as play:
        browser = play.chromium.launch(executable_path=shutil.which('chromium'),
                                       headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': width, 'height': height})
        try:
            page.set_content(html, wait_until='domcontentloaded')
            page.add_style_tag(content=styles)
            for name in ('ui_state.js', 'reading_links.js', 'trace_workbench.js',
                         'inquiry_workbench.js', 'app.js'):
                page.add_script_tag(content=(STATIC / name).read_text('utf8'))
            page.evaluate("window.dispatchEvent(new Event('DOMContentLoaded'))")
            active = """{
              runtime:{busy:true,progress:{percent:38,stage:'正在检索文献',detail:'arXiv 8 / 30'},logs:[]},
              last_run:{state:'success',mode:'search',started_at:'2026-11-08T08:00:00',finished_at:'2026-11-08T08:03:00'}
            }"""
            page.evaluate(f'showTaskProgress({active})')
            assert page.locator('#taskCenter').is_visible()
            assert page.locator('#taskPercent').inner_text() == '38%'
            assert not page.locator('#taskDockDismiss').is_visible()
            page.wait_for_timeout(280)  # allow the bottom entry animation to finish
            # New tasks default to expanded in v1.40.5 and later.
            assert page.locator('#taskDockToggle').get_attribute('aria-expanded') == 'true'
            expanded = page.locator('#taskCenter').bounding_box()
            page.locator('#taskDockToggle').click()  # minimize
            page.wait_for_timeout(320)
            assert page.locator('#taskDockToggle').get_attribute('aria-expanded') == 'false'
            assert page.locator('#taskDockBody').get_attribute('aria-hidden') == 'true'
            collapsed = page.locator('#taskCenter').bounding_box()
            assert expanded['height'] > collapsed['height']
            page.locator('#taskDockToggle').click()  # reopen
            assert page.locator('#taskDockToggle').get_attribute('aria-expanded') == 'true'
            assert page.locator('#taskDockBody').get_attribute('aria-hidden') == 'false'
            for box in (collapsed, expanded):
                assert 0 <= box['x'] <= width
                assert 0 <= box['y'] <= height
                assert box['x'] + box['width'] <= width + 1
                assert box['y'] + box['height'] <= height + 1
            page.keyboard.press('Escape')
            assert page.locator('#taskDockToggle').get_attribute('aria-expanded') == 'false'
            assert page.locator('#taskDockBody').get_attribute('aria-hidden') == 'true'
            assert page.locator('#taskDockBody').evaluate('(el) => el.inert') is True

            finished = """{
              runtime:{busy:false,progress:{},logs:[]},
              last_run:{state:'success',mode:'search',started_at:'2026-11-08T09:00:00',
                finished_at:'2026-11-08T09:03:00',
                result:{source_results:[{ok:true,count:2}],retrieval_counts:{}}}
            }"""
            page.evaluate(f'showTaskProgress({finished})')
            assert page.locator('#taskDockDismiss').is_visible()
            page.locator('#taskDockDismiss').click()
            assert not page.locator('#taskCenter').is_visible()
            page.evaluate(f'showTaskProgress({finished})')
            assert not page.locator('#taskCenter').is_visible()
            page.evaluate("showTaskProgress({runtime:{busy:true,progress:{percent:4,stage:'新任务'},logs:[]},last_run:{state:'success'}})")
            assert page.locator('#taskCenter').is_visible()
            assert page.locator('#taskDockToggle').get_attribute('aria-expanded') == 'true'
        finally:
            browser.close()
