# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Optional real Chromium UX smoke; never uses production accounts.

Requires: pip install playwright; a Chromium binary present locally.
This is NOT a substitute for external HTTPS/systemd/Windows acceptance.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from paper_radar.public_portal import Portal, make_server


def run(*, screenshot: Path | None = None, browser_path: str | None = None) -> dict:
    from playwright.sync_api import sync_playwright
    with TemporaryDirectory(prefix='paper-radar-v125-browser-') as td:
        portal=Portal(Path(td)/'portal',embedded_workers=False)
        server=make_server('127.0.0.1',0,portal)
        thread=Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                launch={'headless':True,'args':['--no-sandbox','--disable-dev-shm-usage']}
                if browser_path:launch['executable_path']=browser_path
                browser=pw.chromium.launch(**launch)
                page=browser.new_page(viewport={'width':1440,'height':960},locale='zh-CN',accept_downloads=True)
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                try:
                    page.goto(f'http://127.0.0.1:{server.server_port}',wait_until='networkidle')
                    page.locator('#registerTab').click()
                    page.locator('#username').fill('v125_smoketest')
                    page.locator('#password').fill('strong browser test password 012345')
                    page.locator('#authSubmit').click()
                    page.locator('#viewWorkspace').wait_for(state='visible',timeout=10000)
                    page.locator('#openTopicSettings').click()
                    if not page.locator('#topicSetup').evaluate('(el)=>el.open'):
                        page.locator('#topicSetup summary').click()
                    page.locator('[data-topic-tab="new"]').click()
                    page.locator('#newTopic').fill('工业时序预测')
                    page.locator('#newKeywords').fill('industrial forecasting, time series')
                    page.locator('[data-sources="new"][value="openalex"]').check()
                    page.locator('#topicAdd').click()
                    page.wait_for_function("document.querySelector('#editTopicName').value === '工业时序预测'")
                    page.locator('#editKeywords').fill('mechanistic learning, industrial time series')
                    page.locator('[data-sources="edit"][value="crossref"]').check()
                    page.locator('#topicSave').click()
                    page.wait_for_timeout(200)
                    page.locator('[data-view="tasks"]').click()
                    page.locator('#viewTasks').wait_for(state='visible')
                    page.locator('[data-view="reports"]').click()
                    page.locator('#viewReports').wait_for(state='visible')
                    page.locator('.report-tools-more summary').click()
                    with page.expect_download(timeout=10000) as download:
                        page.locator('#exportEvidence').click()
                    assert download.value.suggested_filename=='paper-radar-evidence.csv'
                    page.locator('[data-view="workspace"]').click()
                    if screenshot:
                        screenshot.parent.mkdir(parents=True,exist_ok=True)
                        page.screenshot(path=str(screenshot),full_page=True)
                    page.set_viewport_size({'width':390,'height':844})
                    page.wait_for_timeout(300)
                    page.screenshot(path=str(screenshot.with_name(screenshot.stem+'_mobile.png')) if screenshot else str(Path(td)/'mobile.png'),full_page=True)
                    # no viewport-wide overflow: horizontal data tables scroll locally.
                    overflow=page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 2')
                    assert not overflow,'Mobile layout overflows the viewport'
                    assert not errors,'Browser runtime exceptions: '+repr(errors)
                    return {'login':'pass','topic_create':'pass','topic_update':'pass',
                            'navigation':'pass','csv_download':'pass',
                            'mobile_overflow':'pass','js_errors':errors}
                finally:browser.close()
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--browser-path',type=str,default=None)
    parser.add_argument('--screenshot',type=Path,default=None)
    args=parser.parse_args(argv)
    print(run(screenshot=args.screenshot,browser_path=args.browser_path))
    return 0

if __name__=='__main__':raise SystemExit(main())
