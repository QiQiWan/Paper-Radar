# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Real Chromium DOM acceptance for v1.32 inbox, note, batch and export.

Automated synthetic-user journeys only, never substitute for human research.
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
from paper_radar.portal_auth import db as auth_db, create_account
from paper_radar.public_portal import Portal, make_server
from tools.v131_workspace_benchmark import seed_workspace,pct


def run(*,out_dir:Path,papers=1000,browser_path=None):
    try:from playwright.sync_api import sync_playwright
    except ImportError as exc:raise RuntimeError('playwright package unavailable') from exc
    out_dir.mkdir(parents=True,exist_ok=True)
    tasks=[];js_errors=[];server_errors=[]
    with TemporaryDirectory(prefix='paper-radar-v132-chromium-') as tmp:
        portal=Portal(Path(tmp)/'portal',embedded_workers=False)
        with auth_db(portal.auth_path) as db:
            account=create_account(db,'v132_browser','v132_browser_password_very_long')
        seed_workspace(portal,account['id'],papers)
        server=make_server('127.0.0.1',0,portal)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as pw:
                opts={'headless':True,'args':['--no-sandbox','--disable-dev-shm-usage']}
                if browser_path:opts['executable_path']=browser_path
                browser=pw.chromium.launch(**opts)
                ctx=browser.new_context(viewport={'width':1440,'height':900},locale='zh-CN',accept_downloads=True)
                page=ctx.new_page();page.set_default_timeout(14000)
                page.on('pageerror',lambda exc:js_errors.append(str(exc)[:200]))
                page.on('response',lambda res:server_errors.append(str(res.status)+':'+res.url) if res.status>=500 else None)
                base=f'http://127.0.0.1:{server.server_port}'
                try:page.goto(base,wait_until='domcontentloaded',timeout=6000)
                except Exception as exc:raise RuntimeError('Browser navigation blocked: '+str(exc)[:160]) from exc
                def step(name,fn):
                    before=perf_counter()
                    try:
                        details=fn()
                        tasks.append({'task':name,'state':'pass','duration_ms':round((perf_counter()-before)*1000,2),
                                      'details':str(details or '')[:140]})
                    except Exception as exc:
                        tasks.append({'task':name,'state':'fail','duration_ms':round((perf_counter()-before)*1000,2),
                                      'error':f'{type(exc).__name__}: {str(exc)[:190]}'})
                        try:page.screenshot(path=str(out_dir/(name+'_failure.png')),full_page=True,timeout=4000)
                        except Exception:pass
                def login():
                    page.locator('#username').fill('v132_browser')
                    page.locator('#password').fill('v132_browser_password_very_long')
                    page.locator('#authSubmit').click()
                    page.locator('#appView').wait_for(state='visible')
                    page.wait_for_function("n=>document.querySelector('#metricPapers')?.textContent.trim()===String(n)",arg=papers)
                    return 'authenticated, seeded account'
                step('login',login)
                def inbox():
                    page.locator('#openInboxQueue').click()
                    page.locator('#paperFilter').wait_for(state='visible')
                    assert page.locator('#paperFilter').input_value()=='inbox'
                    page.locator('#paperList article.paper').first.wait_for(state='visible')
                    return 'inbox opens from dashboard'
                step('inbox',inbox)
                chosen=[]
                def batch():
                    first=page.locator('#paperList .paper-selection input').first
                    second=page.locator('#paperList .paper-selection input').nth(1)
                    chosen[:]=[first.input_value(),second.input_value()]
                    first.check();second.check()
                    with page.expect_response(lambda r:'/api/public/papers/bulk' in r.url and r.status==200):
                        page.locator('#triageStar').click()
                    assert len(chosen)==2 and chosen[0]!=chosen[1]
                    return 'selected 2, submitted one atomic batch'
                step('batch_star',batch)
                def note():
                    page.locator('#paperFilter').select_option('all')
                    page.locator('#paperList article.paper').first.wait_for(state='visible')
                    p=page.locator('#paperList article.paper').first
                    pid=p.locator('.paper-selection input').input_value()
                    p.locator('.paper-note-panel summary').click()
                    p.locator('.paper-note-panel textarea').fill('人工笔记：下一步检查消融与泛化')
                    p.locator('.paper-note-panel select').select_option('replicate')
                    with page.expect_response(lambda r:'/api/public/papers/note' in r.url and r.status==200):
                        p.locator('.paper-note-panel button.primary').click()
                    page.wait_for_function("document.querySelector('.paper-note-panel .paper-note-actions .hint')?.textContent.includes('已保存')")
                    chosen.append(pid)
                    return 'manual note and action saved'
                step('save_note',note)
                def persistence():
                    page.reload(wait_until='domcontentloaded')
                    page.locator('#appView').wait_for(state='visible')
                    page.locator('#paperFilter').select_option('actionable')
                    page.locator('#paperList article.paper').first.wait_for(state='visible')
                    assert '下一步：尝试复现' in page.locator('#paperList article.paper').first.inner_text()
                    page.locator('#paperList article.paper').first.locator('.paper-note-panel summary').click()
                    assert '人工笔记：下一步检查消融与泛化' in page.locator('.paper-note-panel textarea').first.input_value()
                    return 'note survived reload, action view filters correctly'
                step('reload_filter_action',persistence)
                def export():
                    with page.expect_download() as d:
                        page.locator('#exportActionReading').click()
                    assert d.value.suggested_filename.endswith('.csv')
                    with page.expect_download() as d:
                        page.locator('#exportStarredReading').click()
                    assert d.value.suggested_filename.endswith('.md')
                    return 'action CSV and starred Markdown downloaded'
                step('download_lists',export)
                def skip():
                    with page.expect_response(lambda r:'/api/public/papers/bulk' in r.url and r.status==200):
                        page.locator('#paperList .paper-selection input').first.check()
                        page.locator('#triageSkip').click()
                    page.locator('#paperFilter').select_option('skipped')
                    page.locator('#paperList article.paper').first.wait_for(state='visible')
                    return 'skipped papers remain recoverable'
                # Confirmation dialog for bulk skip is browser-owned.
                page.on('dialog',lambda d:d.accept())
                step('skip_restore_view',skip)
                def mobile():
                    page.set_viewport_size({'width':390,'height':844})
                    page.screenshot(path=str(out_dir/'v132_mobile.png'),full_page=True)
                    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth + 2')
                    return '390px viewport without page overflow'
                step('mobile',mobile)
                ctx.close();browser.close()
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
    ms=[x['duration_ms'] for x in tasks]
    passed=sum(x['state']=='pass' for x in tasks)
    result={'version':__version__,'kind':'synthetic_automated_Chromium','real_human_participants':0,
        'at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'papers':papers,'tasks':tasks,'summary':{'tasks':len(tasks),'passed':passed,
        'task_completion_rate':round(passed/len(tasks),4),'task_error_rate':round(1-passed/len(tasks),4),
        'p50_task_ms':pct(ms,50),'p95_task_ms':pct(ms,95),'js_exceptions':js_errors,
        'http_5xx':server_errors[:10]}}
    (out_dir/'triage_browser.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result


def main(argv=None):
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--papers',type=int,default=1000)
    a.add_argument('--browser-path')
    a.add_argument('--out-dir',type=Path,required=True)
    a.add_argument('--enforce',action='store_true')
    args=a.parse_args(argv)
    try:result=run(out_dir=args.out_dir,papers=args.papers,browser_path=args.browser_path)
    except Exception as exc:
        args.out_dir.mkdir(parents=True,exist_ok=True)
        result={'version':__version__,'status':'not_executed','reason':str(exc)[:250],
                'real_human_participants':0,'at':datetime.now(timezone.utc).isoformat(timespec='seconds')}
        (args.out_dir/'triage_browser.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
        return 2
    print(json.dumps(result['summary'],ensure_ascii=False))
    return int(args.enforce and (result['summary']['task_error_rate']>0 or
            result['summary']['js_exceptions'] or result['summary']['http_5xx']))

if __name__=='__main__':raise SystemExit(main())
