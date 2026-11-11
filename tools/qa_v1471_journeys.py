# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Offline Chromium UI -> Python bridge -> real isolated HTTP server and SQLite.

The managed browser environment may block navigation to all HTTP origins. This
harness therefore loads the real HTML/CSS/JS with set_content and uses a narrow
Python fetch bridge for HTTP requests. Clicks, DOM events, backend request
handlers, CSRF tokens, cookies, persistence, and error responses remain real.
URL navigation, browser-native cookies and download handoffs are NOT evaluated.
No external scholarly source, cloud LLM, personal user data or network is used.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Thread
from time import perf_counter
from urllib.parse import urljoin
from requests import Session

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'paper_radar'/'static'

@contextmanager
def serving(server):
    t=Thread(target=server.serve_forever,daemon=True)
    t.start()
    try:yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown();t.join(timeout=6);server.server_close()

def install_frontend(page,base,session,mode,fail_rules):
    """Load genuine static source, with an explicit test-only HTTP boundary."""
    asset='index.html' if mode=='local' else 'portal.html'
    html=(STATIC/asset).read_text('utf-8').replace('__LOCAL_TOKEN__','qa-v1471-token')
    html=re.sub(r'<script\b[^>]*\bsrc="[^"]+"[^>]*>\s*</script>','',html)
    html=re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>','',html)
    # In isolation there is no origin, therefore in-page URL updates are
    # disabled. Deep-link fidelity requires a separate real-navigation test.
    page.set_content(html,wait_until='domcontentloaded')
    def bridge(path,opts):
        method=str(opts.get('method') or 'GET').upper()
        if not str(path).startswith('/'):
            return {'status':400,'body':json.dumps({'error':'Unexpected external origin'})}
        for rule in fail_rules:
            if str(path).startswith(rule.get('path','/no-such-request')) and rule.get('remain',0)>0:
                rule['remain']-=1
                if rule.get('type')=='network':return {'network_failure':True}
                return {'status':rule.get('status',503),'body':json.dumps({'error':rule.get('error','Simulated outage')})}
        headers=dict(opts.get('headers') or {})
        if method!='GET':headers.setdefault('Origin',base)
        try:
            res=session.request(method,urljoin(base,str(path)),headers=headers,
                data=opts.get('body') if method!='GET' else None,timeout=12)
        except Exception as e:
            return {'network_failure':True,'error':type(e).__name__}
        if res.status_code>=400:print('QA HTTP ERROR',mode,method,path,res.status_code,res.text[:160],flush=True)
        return {'status':res.status_code,'body':res.text,
                'headers':{'Content-Type':res.headers.get('Content-Type','application/json')}}
    page.expose_function('qaHttp',bridge)
    page.evaluate('''() => {
      window.fetch=async (path,opts={})=>{
        const r=await window.qaHttp(String(path),{method:opts.method||'GET',headers:opts.headers||{},body:opts.body||null});
        if(r.network_failure)throw new TypeError('Simulated network failure');
        return new Response(r.body,{status:r.status,headers:r.headers||{'Content-Type':'application/json'}});
      };
    }''')
    css=['app.css','settings.css','ui_polish.css','ux_v147.css','research_program.css'] if mode=='local' else [
        'portal.css','portal_polish.css','portal_ux_v147.css','research_program.css']
    page.add_style_tag(content='\n'.join((STATIC/name).read_text('utf-8') for name in css))
    js=['ui_state.js','reading_links.js','trace_workbench.js','inquiry_workbench.js','research_program.js','app.js'] if mode=='local' else [
        'ui_state.js','reading_links.js','trace_workbench.js','inquiry_workbench.js','research_program.js','portal.js']
    for name in js:
        page.add_script_tag(content=(STATIC/name).read_text('utf-8'))
        if name=='reading_links.js':page.evaluate('window.PRReading.write=()=>{}')
    if mode=='local':page.evaluate("window.dispatchEvent(new Event('DOMContentLoaded'))")

def run(out_dir:Path,browser_path=None):
    from playwright.sync_api import sync_playwright,expect
    from paper_radar.core import DEFAULTS,save_config,open_database,save_papers
    from paper_radar.models import Paper
    save_config(DEFAULTS.copy())
    local_paper=Paper('synthetic:local1471','Synthetic Local Neural Operator Study','QA-only metadata: no real PDF.',
                     'QA Test','2026-11-01','2026-11-01','cs.LG',
                     'https://example.invalid/local','',source='arxiv')
    with open_database() as db:save_papers(db,[local_paper])
    from paper_radar.webapp import Handler
    from paper_radar.public_portal import Portal,make_server
    from paper_radar.portal_auth import db as auth_db,create_account,create_session
    from tools.v131_workspace_benchmark import seed_workspace
    from http.server import ThreadingHTTPServer
    out_dir.mkdir(parents=True,exist_ok=True)
    tasks=[];js_errors=[];server_errors=[]
    failed=[];pass_count=0
    temp=Path(os.environ['PAPER_RADAR_USER_HOME'])
    public_paper=Paper('synthetic:public1471','Synthetic New Operator Study v1471',
                       'QA-only generated metadata: no real PDF.','QA Test',
                       '2026-11-01','2026-11-01','cs.LG',
                       'https://example.invalid/public','',source='arxiv')
    portal=Portal(temp/'portal',embedded_workers=True,job_workers=1,fetcher=lambda _config,logger:[public_paper])
    with auth_db(portal.auth_path) as db:
        user=create_account(db,'qa_researcher','qa-password-1471-long')
        token,csrf=create_session(db,user['id'])
    seed_workspace(portal,user['id'],12)
    public=make_server('127.0.0.1',0,portal)
    local=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    local.token='qa-v1471-token'
    with ExitStack() as stack:
      public_origin=stack.enter_context(serving(public))
      local_origin=stack.enter_context(serving(local))
      with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=browser_path or shutil.which('chromium'),
                                  args=['--no-sandbox','--disable-dev-shm-usage'])
        for mode in ('local','public'):
            ctx=browser.new_context(viewport={'width':1280,'height':900},locale='zh-CN')
            page=ctx.new_page();page.set_default_timeout(7000)
            page.on('pageerror',lambda exc,m=mode:js_errors.append({'mode':m,'error':str(exc)[:300]}))
            origin=local_origin if mode=='local' else public_origin
            sess=Session()
            if mode=='public':sess.cookies.set('PRSession',token)
            fails=[]
            install_frontend(page,origin,sess,mode,fails)
            def step(label,action):
                start=perf_counter()
                try:
                    note=action()
                    tasks.append({'mode':mode,'task':label,'status':'pass','seconds':round(perf_counter()-start,3),'note':str(note or '')[:150]})
                except Exception as exc:
                    tasks.append({'mode':mode,'task':label,'status':'fail','seconds':round(perf_counter()-start,3),
                                  'error':str(exc)[:320]})
                    try:page.screenshot(path=str(out_dir/f'fail_{mode}_{label}.png'),full_page=True)
                    except:pass
            if mode=='local':
                def loaded():
                    expect(page.locator('#homeTopicName')).not_to_contain_text('正在读取')
                    expect(page.locator('#localConnectionRecovery')).to_be_hidden()
                    return 'initial state loaded'
                step('initial_load',loaded)
                def navigate_and_retry():
                    page.locator('.navigation [data-view="dashboard"]').click()
                    expect(page.locator('#dashboard')).to_have_class(re.compile('active'))
                    page.locator('#topicKeywords').fill('neural operator, finite element')
                    # A failed read-only bootstrap should be recoverable without a new task.
                    page.locator('[data-view="home"]').first.click()
                    fails.append({'path':'/api/bootstrap','remain':1,'status':503})
                    page.evaluate("showLocalConnectionError('测试服务暂时不可用')")
                    page.locator('#retryLocalConnection').click()
                    expect(page.locator('#localConnectionRecovery')).to_be_visible()
                    page.locator('#retryLocalConnection').click()
                    expect(page.locator('#localConnectionRecovery')).to_be_hidden()
                    return 'retry button recovers from failed bootstrap'
                step('connection_recovery',navigate_and_retry)
                def local_library():
                    page.locator('.navigation [data-view="library"]').click()
                    expect(page.locator('#libraryList .paper-item')).to_have_count(1)
                    page.locator('#libraryList [data-action="star"]').click()
                    expect(page.locator('#libraryList [data-action="star"]')).to_contain_text('已收藏')
                    return 'local saved paper persists through API'
                step('local_library_star',local_library)
                def inquiry_empty():
                    page.locator('.navigation [data-view="inquiry"]').click()
                    expect(page.locator('#localInquiryWorkbench textarea')).to_be_visible()
                    page.locator('#localInquiryWorkbench textarea').fill('跨网格泛化效果是否一致？')
                    page.locator('#localInquiryWorkbench button.inquiry-primary').first.click()
                    expect(page.locator('#localInquiryWorkbench .inquiry-output')).to_contain_text('可靠的实验比较结论')
                    page.locator('#localInquiryWorkbench .inquiry-next-actions button.inquiry-primary').click()
                    expect(page.locator('#library')).to_have_class(re.compile('active'))
                    return 'scientific evidence caveat and library action'
                step('inquiry_empty_evidence',inquiry_empty)
                page.screenshot(path=str(out_dir/'local_library.png'),full_page=True)
            else:
                def loaded():
                    expect(page.locator('#appView')).to_be_visible()
                    expect(page.locator('#metricPapers')).to_have_text('12')
                    return 'session authenticated; real SQLite dashboard populated'
                step('session_dashboard',loaded)
                def library():
                    page.locator('#primaryNav [data-view="library"]').click()
                    expect(page.locator('#paperList article.paper')).to_have_count(12)
                    page.locator('#paperSearch').fill('Study 000000')
                    expect(page.locator('#paperList')).to_contain_text('Study 000000')
                    page.locator('#paperSearch').fill('')
                    star=page.locator('#paperList .star-toggle').first
                    old=star.get_attribute('aria-pressed');star.click()
                    expect(star).to_have_attribute('aria-pressed','true' if old=='false' else 'false')
                    return 'paper search and persisted flag'
                step('library_search_star',library)
                def search_validation():
                    page.locator('#primaryNav [data-view="tasks"]').click()
                    expect(page.locator('#viewTasks')).to_be_visible()
                    page.locator('#portalQuickKeywords').fill('')
                    page.locator('#searchNow').click()
                    expect(page.locator('#message')).to_contain_text('请填写 1—6 组')
                    page.locator('#portalQuickKeywords').fill('neural operator')
                    return 'empty search rejected without starting task'
                step('search_validation',search_validation)
                def offline_search():
                    page.locator('#searchNow').click()
                    # Wait for the actual queued worker completion, then verify
                    # persistence. Repeated 50 ms SQLite polling needlessly competes
                    # with the worker's write transaction and source checkpoints.
                    expect(page.locator('#jobState')).to_contain_text('检索完成',timeout=25000)
                    with portal.workspace(user['id']) as db:
                        found=db.execute("SELECT COUNT(*) FROM papers WHERE title='Synthetic New Operator Study v1471'").fetchone()[0]
                    assert found==1,'worker acknowledged completion but did not persist the new paper'
                    page.locator('#refreshJob').click()
                    expect(page.locator('#message')).to_be_hidden()
                    expect(page.locator('#jobState')).to_contain_text('任务 #')
                    page.locator('#primaryNav [data-view="library"]').click()
                    expect(page.locator('#paperList')).to_contain_text('Synthetic New Operator Study v1471',timeout=10000)
                    return 'search submitted via actual API and processed by isolated worker'
                step('offline_search_and_library',offline_search)
                def pdf_upload_read():
                    # A tiny original QA-only PDF exercises the authenticated
                    # upload, SHA validation, ranged GET, reader and page jump.
                    import fitz
                    doc=fitz.open()
                    sheet=doc.new_page(width=612,height=792)
                    sheet.insert_text((72,90),'Neural operator error 0.12 across geometry.',fontsize=13)
                    pdf_data=doc.tobytes()
                    doc.close()
                    (out_dir/'synthetic_qa_pdf.pdf').write_bytes(pdf_data)
                    assert len(pdf_data)>256 and pdf_data[:5]==b'%PDF-'
                    # This PUT is binary: verify it through the real API, not
                    # a synthetic JSON response in the Chromium fetch bridge.
                    headers={'Origin':origin,'Content-Type':'application/pdf',
                        'X-Portal-Action':'1','X-CSRF-Token':csrf,
                        'X-Paper-ID':public_paper.paper_id,'X-Topic-ID':'default'}
                    result=sess.put(origin+'/api/public/pdf',data=pdf_data,headers=headers,timeout=15)
                    assert result.status_code==201,(result.status_code,result.text[:200])
                    url=f'/api/public/pdf/view?topic_id=default&paper_id={public_paper.paper_id}'
                    full=sess.get(origin+url,timeout=12)
                    assert full.status_code==200 and full.content==pdf_data
                    partial=sess.get(origin+url,headers={'Range':'bytes=0-63'},timeout=12)
                    assert partial.status_code==206 and partial.content==pdf_data[:64]
                    # Open real UI reader and inspect iframe source and side panel.
                    page.locator('#paperSearch').fill('Synthetic New Operator Study v1471')
                    expect(page.locator('#paperList .paper')).to_have_count(1,timeout=9000)
                    page.locator('#paperList button.paper-read-main').click()
                    expect(page.locator('#fulltextArea')).to_have_attribute('open','')
                    expect(page.locator('#fulltextPanel iframe.pdf-reader-frame')).to_have_count(1)
                    expect(page.locator('#fulltextPanel')).to_contain_text('PDF 原文')
                    page.screenshot(path=str(out_dir/'public_pdf_reader.png'),full_page=True,animations='disabled')
                    return 'actual PDF upload, integrity and ranged view; browser reader panel mounts'
                step('pdf_upload_read',pdf_upload_read)
                def generate_report():
                    page.locator('#primaryNav [data-view="reports"]').click()
                    page.locator('#runSynthesis').click()
                    expect(page.locator('#jobState')).to_contain_text('报告生成',timeout=25000)
                    page.locator('#refreshReports').click()
                    expect(page.locator('#reportHistory .report-entry')).to_have_count(1,timeout=12000)
                    page.locator('#reportHistory .report-entry button').first.click()
                    expect(page.locator('#reportReader')).to_be_visible()
                    expect(page.locator('#reportContent')).to_contain_text('已收录文献概览')
                    expect(page.locator('#reportReaderInfo')).to_contain_text('已收录 13 篇题录')
                    expect(page.locator('#comparisonArea')).to_be_hidden()
                    assert '**' not in page.locator('#reportContent').inner_text(), 'raw Markdown tokens leaked into report reader'
                    page.screenshot(path=str(out_dir/'public_report_reader.png'),full_page=True,animations='disabled')
                    with portal.workspace(user['id']) as db:
                        count=db.execute('SELECT COUNT(*) FROM research_reports').fetchone()[0]
                    assert count==1,'report UI opened but no durable report exists'
                    return 'real synthesis worker -> immutable SQLite report -> UI reader'
                step('generate_report_and_read',generate_report)
                def job_poll_network_recovery():
                    page.locator('#primaryNav [data-view="tasks"]').click()
                    fails.append({'path':'/api/public/jobs','remain':1,'type':'network'})
                    page.locator('#refreshJob').click()
                    expect(page.locator('#jobState')).to_contain_text('任务状态暂时不可用',timeout=8000)
                    # A retry is only a read-only status request; it must
                    # never repeat a potentially billable generation job.
                    page.locator('#refreshJob').click()
                    expect(page.locator('#jobState')).to_contain_text('报告生成完成',timeout=8000)
                    with auth_db(portal.auth_path) as db:
                        jobs=db.execute('SELECT COUNT(*) FROM portal_jobs WHERE account_id=?',(user['id'],)).fetchone()[0]
                    assert jobs==2,('poll retry unexpectedly submitted a task',jobs)
                    return 'a failed status poll recovers without resubmitting jobs'
                step('job_poll_network_recovery',job_poll_network_recovery)
                def inquiry_empty():
                    page.locator('#primaryNav [data-view="inquiry"]').click()
                    expect(page.locator('#publicInquiryWorkbench textarea')).to_be_visible()
                    page.locator('#publicInquiryWorkbench textarea').fill('目前是否有经过验证的实验比较结论？')
                    page.locator('#publicInquiryWorkbench button.inquiry-primary').first.click()
                    expect(page.locator('#publicInquiryWorkbench .inquiry-output')).to_contain_text('可靠的实验比较结论')
                    page.locator('#publicInquiryWorkbench .inquiry-next-actions button.inquiry-primary').click()
                    expect(page.locator('#viewLibrary')).to_be_visible()
                    return 'research report does not overclaim without confirmed evidence'
                step('inquiry_empty_evidence',inquiry_empty)
                page.screenshot(path=str(out_dir/'public_library.png'),full_page=True)
            ctx.close()
        browser.close()
    report={'version':'1.47.1','method':'Chromium DOM + isolated real HTTP/SQLite via Python fetch bridge',
            'scope_limitations':['HTTP origins blocked by managed browser policy; native navigation/cookies/download not tested',
                                 'no external academic search or LLM calls','not a human usability study'],
            'tasks':tasks,'js_errors':js_errors,'success':all(t['status']=='pass' for t in tasks) and not js_errors}
    (out_dir/'journey_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['success'] else 2

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out-dir',type=Path,required=True);ap.add_argument('--browser-path');a=ap.parse_args()
    with tempfile.TemporaryDirectory(prefix='pr-qa-v1471-') as td:
        # Must be set before first import of paper_radar.core / webapp.
        os.environ['PAPER_RADAR_USER_HOME']=td
        return run(a.out_dir,a.browser_path)
if __name__=='__main__':raise SystemExit(main())
