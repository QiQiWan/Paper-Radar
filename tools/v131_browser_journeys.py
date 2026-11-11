# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Measured Chromium UI journeys: real DOM, clicks, server, cookie and SQLite.

This executes *automated* user journeys. Human usability studies must be run
separately and must not be represented by these numbers.
Install: pip install playwright ; python -m playwright install chromium
Run: python -m tools.v131_browser_journeys --out-dir <path>
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import platform
import re
import statistics
from tempfile import TemporaryDirectory
from threading import Thread
from time import perf_counter

def classify_failed_requests(requests: list[dict], verified_downloads: set[str], *,
                             document_navigations: list[dict] | None = None,
                             site_origin: str = '') -> tuple[list[dict], int]:
    """Filter only verifiably expected browser-level request cancellations.

    Chromium may abort an in-flight GET when a successful same-origin document
    navigation replaces its page, or when a verified CSV is handed to the
    download manager. Neither indicates a failed user journey. Correlation is
    required: a same-origin 200 document navigation within one second, an
    allowed GET/XHR API path, and the precise ERR_ABORTED failure. A random
    aborted request, failed navigation, POST, timeout, 5xx or JS exception
    remains release-blocking. Return format is kept compatible with v1.40.6.
    """
    from urllib.parse import urlsplit
    unexpected = []
    verified_csv_abort_count = 0
    navigation_docs = document_navigations or []
    origin = urlsplit(site_origin)
    allowed_paths = {'/api/public/papers', '/api/public/dashboard'}
    for item in requests:
        url = str(item.get('url') or '').split('?', 1)[0]
        is_aborted = item.get('failure') == 'net::ERR_ABORTED'
        is_verified_csv_handoff = (
            verified_csv_abort_count == 0
            and 'evidence_csv' in verified_downloads
            and is_aborted and url.endswith('/api/public/evidence/export')
        )
        if is_verified_csv_handoff:
            verified_csv_abort_count += 1
            continue
        uri = urlsplit(url)
        is_expected_navigation_abort = (
            is_aborted
            and bool(origin.scheme) and uri.scheme == origin.scheme
            and uri.netloc == origin.netloc and uri.path in allowed_paths
            and item.get('method') == 'GET'
            and item.get('resource_type') in {'xhr', 'fetch'}
            and type(item.get('at')) in (float, int)
            and any(
                nav.get('status') == 200
                and str(nav.get('url') or '').startswith(site_origin.rstrip('/') + '/')
                and type(nav.get('at')) in (float, int)
                and abs(item['at'] - nav['at']) <= 1.0
                for nav in navigation_docs
            )
        )
        if not is_expected_navigation_abort:
            unexpected.append(item)
    return unexpected, verified_csv_abort_count


from paper_radar import __version__
from paper_radar.portal_auth import db as auth_db
from paper_radar.public_portal import Portal,make_server
from tools.v131_workspace_benchmark import pct,seed_workspace


def run(*,out_dir:Path, browser_path: str|None=None, papers:int=1000):
    try:from playwright.sync_api import expect, sync_playwright
    except ImportError as exc:raise RuntimeError('Playwright is required for Chromium acceptance') from exc
    out_dir=Path(out_dir)
    out_dir.mkdir(parents=True,exist_ok=True)
    tasks=[]
    javascript_errors=[]
    failed_requests=[]
    verified_downloads=set()
    verified_document_navigations=[]
    server_errors=[]
    started=perf_counter()
    with TemporaryDirectory(prefix='paper-radar-v131-browser-') as folder:
        portal=Portal(Path(folder)/'portal',embedded_workers=False)
        server=make_server('127.0.0.1',0,portal)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as pw:
                opts={'headless':True,'args':['--no-sandbox','--disable-dev-shm-usage']}
                if browser_path:opts['executable_path']=browser_path
                browser=pw.chromium.launch(**opts)
                context=browser.new_context(viewport={'width':1440,'height':900},locale='zh-CN',accept_downloads=True)
                page=context.new_page()
                page.set_default_timeout(12000)
                page.on('pageerror',lambda exc:javascript_errors.append(str(exc)[:220]))
                # Preserve the failure time, method and resource type: a bare
                # ERR_ABORTED string cannot distinguish a reload from a bug.
                def on_request_failed(req):
                    failed_requests.append({'url':req.url.split('?',1)[0],
                                            'failure':req.failure,
                                            'method':req.method,
                                            'resource_type':req.resource_type,
                                            'at':perf_counter()})
                page.on('requestfailed',on_request_failed)
                def on_response(response):
                    if response.status>=500:
                        server_errors.append({'status':response.status,
                                              'path':response.url.split('?',1)[0][-95:]})
                    # Chromium's document-navigation response confirms a real
                    # successful page transition (SPA route updates do not).
                    if response.status==200 and response.request.is_navigation_request():
                        verified_document_navigations.append({'url':response.url.split('?',1)[0],
                                                              'status':response.status,
                                                              'at':perf_counter()})
                page.on('response',on_response)
                base=f'http://127.0.0.1:{server.server_port}'
                # Detect policy/network denial before starting timed tasks.
                # Managed Chromium deployments can block localhost entirely.
                try:page.goto(base,wait_until='domcontentloaded',timeout=6000)
                except Exception as exc:raise RuntimeError('Browser navigation preflight blocked: '+str(exc)[:180]) from exc
                def step(name,action):
                    t=perf_counter()
                    try:
                        outcome=action()
                        tasks.append({'task':name,'status':'pass','seconds':round(perf_counter()-t,3),
                                      'observation':str(outcome or '')[:160]})
                    except Exception as exc:
                        tasks.append({'task':name,'status':'fail','seconds':round(perf_counter()-t,3),
                                      'error':f'{type(exc).__name__}: {str(exc)[:300]}'})
                        try:page.screenshot(path=str(out_dir/f'failure_{len(tasks):02}.png'),full_page=True,timeout=3000)
                        except Exception:pass
                def expect_count(value):
                    expect(page.locator('#paperCount')).to_have_text(f'显示 {value} 篇')
                def login():
                    page.goto(base,wait_until='domcontentloaded')
                    page.locator('#username').fill('benchmark_user')
                    page.locator('#password').fill('v131-benchmark-password-long-enough')
                    page.locator('#authSubmit').click()
                    page.locator('#appView').wait_for(state='visible')
                    expect(page.locator('#metricPapers')).to_have_text(str(papers))
                # Use the normal UI to log into the isolated pre-seeded workspace.
                from paper_radar.portal_auth import create_account
                with auth_db(portal.auth_path) as db:
                    user=create_account(db,'benchmark_user','v131-benchmark-password-long-enough')
                seed_workspace(portal,user['id'],papers)
                def home():
                    login()
                    page.screenshot(path=str(out_dir/'01_workspace.png'),full_page=True)
                    assert page.locator('#metricPapers').inner_text().strip()==str(papers)
                    return f'{papers} paper dashboard'
                step('login_and_dashboard',home)
                def library():
                    page.locator('#primaryNav [data-view="library"]').click()
                    expect_count(f'12 / {papers}')
                    assert page.locator('#paperList article.paper').count()==12
                    page.screenshot(path=str(out_dir/'02_library.png'),full_page=True)
                    return '12 cards rendered'
                step('library_first_page',library)
                def search():
                    page.locator('#paperSearch').fill('Study 000124')
                    expect_count('1 / 1')
                    assert '000124' in page.locator('#paperList article.paper').inner_text()
                    return 'search result exact match'
                step('paper_search',search)
                def star():
                    button=page.locator('#paperList .star-toggle').first
                    assert button.get_attribute('aria-pressed')=='false'
                    with page.expect_response(lambda r:'/api/public/papers/flags' in r.url and r.request.method=='POST' and r.status==200):
                        button.click()
                    expect(button).to_have_attribute('aria-pressed','true')
                    return 'star persisted to SQLite'
                step('star_paper',star)
                def mark_read():
                    select=page.locator('#paperList .reading-select').first
                    with page.expect_response(lambda r:'/api/public/papers/flags' in r.url and r.request.method=='POST' and r.status==200):
                        select.select_option('done')
                    expect(select).to_have_value('done')
                    page.reload(wait_until='domcontentloaded')
                    expect_count('1 / 1')
                    assert page.locator('#paperList .reading-select').first.input_value()=='done'
                    return 'reading state survived hard reload'
                step('mark_done_and_reload',mark_read)
                def filter_starred():
                    page.locator('#paperFilter').select_option('starred')
                    expect_count('1 / 1')
                    assert page.locator('#paperList .star-toggle').first.get_attribute('aria-pressed')=='true'
                    return 'starred + query composable'
                step('combined_filter',filter_starred)
                def share_and_restore():
                    page.locator('#sharePagePortal').click()
                    expect(page).to_have_url(re.compile(r'[?&]filter=starred(?:&|$)'))
                    url=page.url
                    assert 'q=Study+000124' in url or 'q=Study%20000124' in url
                    page.reload(wait_until='domcontentloaded')
                    expect_count('1 / 1')
                    assert page.locator('#paperSearch').input_value()=='Study 000124'
                    assert page.locator('#paperFilter').input_value()=='starred'
                    return 'URL filters survived refresh'
                step('share_url_and_restore',share_and_restore)
                def pagination():
                    page.locator('#paperSearch').fill('')
                    page.locator('#paperFilter').select_option('all')
                    expect_count(f'12 / {papers}')
                    page.locator('#loadPapers').click()
                    expect_count(f'24 / {papers}')
                    assert page.locator('#paperList article.paper').count()==24
                    return 'load-more append has no duplicate cards'
                step('load_more',pagination)
                def navigation():
                    page.locator('#primaryNav [data-view="tasks"]').click()
                    page.locator('#viewTasks').wait_for(state='visible')
                    assert '/tasks' in page.url
                    page.locator('#primaryNav [data-view="reports"]').click()
                    page.locator('#viewReports').wait_for(state='visible')
                    assert '/reports' in page.url
                    page.go_back(wait_until='domcontentloaded')
                    page.locator('#viewTasks').wait_for(state='visible')
                    return 'tasks/reports/back worked'
                step('navigation_back',navigation)
                def evidence_export():
                    page.locator('#primaryNav [data-view="reports"]').click()
                    page.locator('.report-tools-more summary').click()
                    with page.expect_download(timeout=12000) as download:
                        page.locator('#exportEvidence').click()
                    assert download.value.suggested_filename=='paper-radar-evidence.csv'
                    saved_file=Path(download.value.path())
                    assert saved_file.is_file() and saved_file.stat().st_size>0, 'CSV download is empty or absent'
                    assert download.value.failure() is None, 'CSV download failed'
                    verified_downloads.add('evidence_csv')
                    return 'CSV exported using browser cookie'
                step('csv_export',evidence_export)
                def mobile():
                    page.locator('#primaryNav [data-view="library"]').click()
                    expect_count(f'12 / {papers}')
                    page.set_viewport_size({'width':390,'height':844})
                    page.screenshot(path=str(out_dir/'03_mobile_library.png'),full_page=True)
                    assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 2'), 'mobile viewport overflow'
                    return '390px viewport has no body overflow'
                step('mobile_layout',mobile)
                def session_relogin():
                    page.set_viewport_size({'width':1440,'height':900})
                    page.locator('#logout').click()
                    page.locator('#authView').wait_for(state='visible')
                    page.locator('#username').fill('benchmark_user')
                    page.locator('#password').fill('v131-benchmark-password-long-enough')
                    page.locator('#authSubmit').click()
                    page.locator('#appView').wait_for(state='visible')
                    page.locator('#primaryNav [data-view="library"]').click()
                    page.locator('#paperFilter').select_option('starred')
                    page.locator('#paperSearch').fill('Study 000124')
                    expect_count('1 / 1')
                    return 'logout/login retained data'
                step('logout_login_persistence',session_relogin)
                def create_account_ui():
                    page.locator('#logout').click()
                    page.locator('#authView').wait_for(state='visible')
                    page.locator('#registerTab').click()
                    page.locator('#username').fill('second_ux_user')
                    page.locator('#password').fill('another-long-password-v131')
                    page.locator('#authSubmit').click()
                    page.locator('#appView').wait_for(state='visible')
                    expect(page.locator('#metricPapers')).to_have_text('0')
                    return 'registered secondary user cannot see seeded papers'
                step('registration_account_isolation',create_account_ui)
                context.close();browser.close()
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
    unexpected_requests, verified_download_aborts = classify_failed_requests(
        failed_requests, verified_downloads,
        document_navigations=verified_document_navigations, site_origin=base)
    verified_navigation_aborts = len(failed_requests)-len(unexpected_requests)-verified_download_aborts
    valid=[t['seconds']*1000 for t in tasks]
    passed=sum(t['status']=='pass' for t in tasks)
    result={'schema_version':1,'version':__version__,'kind':'automated_real_chromium',
        'not_human_study':True,'paper_count':papers,'pdf_count':0,
        'environment':{'os':platform.platform(),'browser':'Chromium Playwright','viewport_primary':[1440,900],
                        'viewport_mobile':[390,844]},'tasks':tasks,
        'summary':{'tasks':len(tasks),'completed':passed,'completion_rate':round(passed/len(tasks),4),
            'task_error_rate':round((len(tasks)-passed)/len(tasks),4),
            'p50_task_ms':pct(valid,50),'p95_task_ms':pct(valid,95),
            'max_task_ms':round(max(valid),2),'js_exception_count':len(javascript_errors),
            'failed_network_count':len(unexpected_requests),
            'verified_download_abort_count':verified_download_aborts,
            'verified_navigation_abort_count':verified_navigation_aborts,
            'http_5xx_count':len(server_errors),
            'wall_seconds':round(perf_counter()-started,2)},
        'failures':{'javascript':javascript_errors[:20], 'requests':unexpected_requests[:20], 'server':server_errors[:20]},
        'navigation_evidence':{'documents':[
            {'url':item['url'].split('?',1)[0][-95:], 'status':item['status'],
             'at_seconds':round(item['at']-started,3)}
            for item in verified_document_navigations[-40:]],
            'request_failures':[
            {'url':item['url'].split('?',1)[0][-95:], 'failure':item['failure'],
             'method':item['method'], 'resource_type':item['resource_type'],
             'at_seconds':round(item['at']-started,3)}
            for item in failed_requests[:40]]},
        'at':datetime.now(timezone.utc).isoformat(timespec='seconds')}
    (out_dir/'browser_journeys.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result


def main(argv=None):
    arg=argparse.ArgumentParser(description=__doc__)
    arg.add_argument('--out-dir',type=Path,required=True)
    arg.add_argument('--browser-path',type=str,default=None)
    arg.add_argument('--papers',type=int,default=1000)
    arg.add_argument('--enforce',action='store_true')
    arg.add_argument('--task-p95-budget-ms',type=float,default=8000)
    args=arg.parse_args(argv)
    try:
        result=run(out_dir=args.out_dir,browser_path=args.browser_path,papers=args.papers)
    except Exception as exc:
        args.out_dir.mkdir(parents=True,exist_ok=True)
        reason=str(exc)[:300]
        unavailable={'version':__version__,'status':'not_executed','reason':reason,
                     'kind':'automated_real_chromium','completion_rate':None,'task_error_rate':None,
                     'at':datetime.now(timezone.utc).isoformat(timespec='seconds')}
        (args.out_dir/'browser_journeys.json').write_text(json.dumps(unavailable,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(unavailable,ensure_ascii=False))
        return 2
    print(json.dumps(result['summary'],ensure_ascii=False))
    return int(args.enforce and (result['summary']['task_error_rate']>0 or result['summary']['js_exception_count']>0 or
          result['summary']['http_5xx_count']>0 or result['summary']['failed_network_count']>0 or
          result['summary']['p95_task_ms']>args.task_p95_budget_ms))

if __name__=='__main__':raise SystemExit(main())
