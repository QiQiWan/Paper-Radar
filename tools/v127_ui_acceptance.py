# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Offline user-interface acceptance, including real Chromium interaction.

Checks progressive disclosure, core action discoverability, topic editing and
mobile overflow. The public API is mocked; no real user data or external calls.
"""
from __future__ import annotations
from pathlib import Path
import argparse
import re

ROOT = Path(__file__).parents[1]


def _prepare_html(filename: str) -> str:
    html=(ROOT/'paper_radar/static'/filename).read_text('utf-8')
    html=re.sub(r'<script\b[^>]*\bsrc="(?:/portal\.js|app\.js)"[^>]*>\s*</script>', '', html)
    html=re.sub(r'<link\b[^>]*\bhref="(?:/portal\.css|app\.css)"[^>]*>', '', html)
    return html


def run(out:Path|None=None,browser_path='/usr/bin/chromium'):
    from playwright.sync_api import sync_playwright
    from tools.v126_offline_ui_check import STUB
    css=(ROOT/'paper_radar/static/portal.css').read_text('utf-8')
    js=(ROOT/'paper_radar/static/portal.js').read_text('utf-8')
    lc=(ROOT/'paper_radar/static/app.css').read_text('utf-8')
    output={}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=browser_path,args=['--no-sandbox','--disable-dev-shm-usage'],headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':920},locale='zh-CN')
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.set_content(_prepare_html('portal.html'),wait_until='domcontentloaded')
            page.add_style_tag(content=css)
            page.evaluate(STUB)
            page.add_script_tag(content=js)
            page.locator('#viewWorkspace').wait_for(state='visible',timeout=12000)
            assert page.locator('.next-actions').is_visible()
            assert page.locator('#topicSetup').count()==1
            assert not page.locator('#viewAccount').is_visible()
            assert not page.locator('#viewWorkspace .topic-summary-disclosure').evaluate('(el)=>el.open')
            if out:
                out.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(out/'public_desktop.png'),full_page=True)
            page.locator('#openTopicSettings').click() if page.locator('#openTopicSettings').is_visible() else page.locator('#viewWorkspace .topic-summary-disclosure summary').click()
            if not page.locator('#viewAccount').is_visible():page.locator('#openTopicSettings').click()
            page.locator('#viewAccount').wait_for(state='visible')
            assert page.locator('[data-topic-mode="edit"]').is_visible()
            assert not page.locator('[data-topic-mode="new"]').is_visible()
            page.locator('[data-topic-tab="new"]').click()
            page.locator('#newTopic').fill('科研前沿测试')
            page.locator('#newKeywords').fill('neural operator, geometry')
            page.locator('#topicAdd').click()
            page.wait_for_function("document.querySelector('#editTopicName').value==='科研前沿测试'",timeout=6000)
            if not page.locator('#topicSetup').evaluate('(el)=>el.open'):
                page.locator('#topicSetup summary').first.click()
            page.locator('#editKeywords').fill('time series, mechanistic learning')
            page.locator('#topicSave').click()
            page.wait_for_function("document.querySelector('#editKeywords').value.includes('time series')",timeout=5000)
            page.locator('[data-view="workspace"]').click()
            page.locator('#openReadingQueue').click()
            assert page.locator('#viewLibrary').is_visible()
            page.locator('[data-view="reports"]').click()
            assert page.locator('#viewReports').is_visible()
            page.locator('[data-view="workspace"]').click()
            page.set_viewport_size({'width':390,'height':844})
            page.wait_for_timeout(120)
            if out:page.screenshot(path=str(out/'public_mobile.png'),full_page=True)
            assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 2'),'public mobile overflow'
            assert not errors,errors
            output['public']='pass'
            output['public_js_errors']=errors
            # Local UI: test native disclosure without mocking heavy API state.
            local=browser.new_page(viewport={'width':1440,'height':920},locale='zh-CN')
            local.set_content(_prepare_html('index.html'),wait_until='domcontentloaded')
            local.add_style_tag(content=lc)
            assert local.locator('#runSearch').is_visible()
            assert not local.locator('#runExport').is_visible()
            assert not local.locator('#createTopic').is_visible()
            local.locator('.local-more-actions summary').click()
            assert local.locator('#runExport').is_visible()
            local.locator('.local-topic-tools summary').click()
            assert local.locator('#createTopic').is_visible()
            # Capture a first-use layout with nonessential disclosures closed.
            local.locator('.local-more-actions summary').click()
            local.locator('.local-topic-tools summary').click()
            local.screenshot(path=str(out/'local_desktop.png'),full_page=True) if out else None
            local.evaluate("""() => {document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id==='settings')); document.querySelectorAll('.nav-link').forEach(v=>v.classList.toggle('active',v.dataset.view==='settings'));document.getElementById('breadcrumbLabel').textContent='设置'}""")
            assert local.locator('#settings .settings-section').count()==6
            assert not local.locator('#apiKey').is_visible()
            local.locator('#settings-ai summary').first.click()
            assert local.locator('#apiKey').is_visible()
            local.locator('#settings-ai summary').first.click()
            assert not local.locator('#apiKey').is_visible()
            if out:
                local.screenshot(path=str(out/'local_settings.png'),full_page=True)
                local.evaluate("""() => {document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id==='dashboard'));document.querySelectorAll('.nav-link').forEach(v=>v.classList.toggle('active',v.dataset.view==='dashboard'));document.getElementById('breadcrumbLabel').textContent='工作台'}""")
                local.screenshot(path=str(out/'local_desktop.png'),full_page=True)
            local.set_viewport_size({'width':390,'height':844})
            local.wait_for_timeout(100)
            assert not local.evaluate('document.documentElement.scrollWidth > window.innerWidth + 2'),'local mobile overflow'
            if out:local.screenshot(path=str(out/'local_mobile.png'),full_page=True)
            output['local']='pass'
            output['local_mobile_overflow']=False
        finally:browser.close()
    return output


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path);ap.add_argument('--browser-path',default='/usr/bin/chromium')
    a=ap.parse_args();print(run(a.out,a.browser_path))

if __name__=='__main__':main()
