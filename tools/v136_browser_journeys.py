# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Real Chromium UI journeys for authenticated per-topic following, using synthetic papers.

Never represents human testing. Local environment may block localhost in Chromium.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import perf_counter
from paper_radar import __version__
from paper_radar.models import Paper
from paper_radar.portal_auth import db as auth_db,create_account
from paper_radar.public_portal import Portal,make_server
from tools.v131_workspace_benchmark import seed_workspace,pct


def run(out_dir: Path,*,papers=1000,browser_path=None):
    from playwright.sync_api import sync_playwright
    out_dir.mkdir(parents=True,exist_ok=True)
    tasks=[];js_errors=[];server_errors=[]
    with TemporaryDirectory(prefix='paper-radar-follow-browser-') as tmp:
        portal=Portal(Path(tmp)/'research',embedded_workers=False)
        with auth_db(portal.auth_path) as db:
            user=create_account(db,'follow_browser_user','strong browser testing password 1234')
        seed_workspace(portal,user['id'],papers)
        def fetch(config,logger):
            return [Paper('2611.01234','Deterministic tracked arxiv paper',
                'Synthetic public abstract','Benchmark author','2026-11-08T00:00:00Z',
                '2026-11-08T02:00:00Z','cs.LG','https://arxiv.org/abs/2611.01234','',source='arxiv')]
        portal.fetcher=fetch
        assert portal.search(user['id'],'default')['following']['events']==1
        server=make_server('127.0.0.1',0,portal)
        thread=Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with sync_playwright() as pw:
                opts={'headless':True,'args':['--no-sandbox','--disable-dev-shm-usage']}
                if browser_path:opts['executable_path']=browser_path
                browser=pw.chromium.launch(**opts)
                context=browser.new_context(viewport={'width':1360,'height':850},locale='zh-CN')
                page=context.new_page();page.set_default_timeout(9500)
                page.on('pageerror',lambda error:js_errors.append(str(error)[:200]))
                page.on('response',lambda res:server_errors.append(f'{res.status} {res.url[:80]}') if res.status>=500 else None)
                base=f'http://127.0.0.1:{server.server_port}'
                try:page.goto(base,wait_until='domcontentloaded',timeout=6500)
                except Exception as exc:raise RuntimeError('Chromium localhost preflight blocked: '+str(exc)[:180]) from exc
                def step(name,fn):
                    start=perf_counter()
                    try:
                        fn();result={'task':name,'state':'pass','duration_ms':round((perf_counter()-start)*1000,2)}
                    except Exception as exc:
                        result={'task':name,'state':'fail','duration_ms':round((perf_counter()-start)*1000,2),
                                'error':f'{type(exc).__name__}: {str(exc)[:240]}'}
                        try:page.screenshot(path=str(out_dir/(name+'_failure.png')),full_page=True)
                        except Exception:pass
                    tasks.append(result)
                def login():
                    page.locator('#username').fill('follow_browser_user')
                    page.locator('#password').fill('strong browser testing password 1234')
                    page.locator('#authSubmit').click()
                    page.locator('#appView').wait_for(state='visible')
                step('authenticated_login',login)
                def follow():
                    page.locator('#primaryNav [data-view="following"]').click()
                    page.locator('#viewFollowing').wait_for(state='visible')
                    page.get_by_text('Deterministic tracked arxiv paper').wait_for()
                    assert '1' in page.locator('#publicFollowingUnread').inner_text()
                step('following_inbox',follow)
                def preference():
                    page.locator('#publicFollowNew').uncheck()
                    with page.expect_response(lambda r:'/api/public/following/preferences' in r.url and r.status==200):
                        page.locator('#publicFollowingSave').click()
                    page.reload(wait_until='domcontentloaded')
                    page.locator('#appView').wait_for(state='visible')
                    page.locator('#primaryNav [data-view="following"]').click()
                    assert not page.locator('#publicFollowNew').is_checked()
                step('preference_persists',preference)
                def seen():
                    with page.expect_response(lambda r:'/api/public/following/seen' in r.url and r.status==200):
                        page.locator('#publicFollowingSeen').click()
                    page.wait_for_function("document.querySelector('#publicFollowingUnread')?.textContent.includes('0')")
                step('seen_and_persisted',seen)
                def narrow():
                    page.set_viewport_size({'width':390,'height':844})
                    page.screenshot(path=str(out_dir/'following_mobile.png'),full_page=True)
                    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 2')
                step('mobile_following',narrow)
                context.close();browser.close()
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
    complete=sum(x['state']=='pass' for x in tasks)
    report={'version':__version__,'kind':'automated_Chromium_synthetic',
      'real_human_participants':0,'paper_metadata_count':papers,'tasks':tasks,
      'summary':{'completed':complete,'task_count':len(tasks),
                 'completion_rate':round(complete/max(1,len(tasks)),4),
                 'p95_ms':pct([x['duration_ms'] for x in tasks],95),
                 'javascript_errors':js_errors,'server_5xx':server_errors},
      'at':datetime.now(timezone.utc).isoformat(timespec='seconds')}
    (out_dir/'following_browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out-dir',type=Path,required=True)
    ap.add_argument('--papers',type=int,default=1000)
    ap.add_argument('--browser-path')
    a=ap.parse_args()
    result=run(a.out_dir,papers=a.papers,browser_path=a.browser_path)
    print(json.dumps(result['summary'],ensure_ascii=False))
    return 0 if result['summary']['completion_rate']==1 and not result['summary']['javascript_errors'] and not result['summary']['server_5xx'] else 1

if __name__=='__main__':raise SystemExit(main())
