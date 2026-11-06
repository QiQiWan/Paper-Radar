# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Offline Chromium rendering with simulated same-origin API responses.

Useful in restricted sandboxes where navigation to loopback addresses is blocked.
Tests JS errors and core DOM flows, not real HTTP, cookies or TLS.
"""
from __future__ import annotations
from pathlib import Path
import argparse

ROOT=Path(__file__).parents[1]

STUB = r'''
window.history.pushState = function(){};
window.__mockTopics=[{id:'default',name:'默认研究主题',keywords:['neural operator'],sources:['arxiv']}];
window.fetch=async function(url,options={}){
 const path=String(url), method=options.method||'GET', body=JSON.parse(options.body||'{}');
 let data={}; let status=200;
 if(path.includes('/api/public/health'))data={version:'1.25.0',registration_enabled:false};
 else if(path.includes('/api/public/me'))data={user:{id:'a'.repeat(32),username:'Researcher',role:'user'},csrf:'mockcsrf'};
 else if(path.includes('/api/public/topics/update')){
  const t=window.__mockTopics.find(t=>t.id===body.topic_id);
  Object.assign(t,{name:body.name,keywords:body.keywords,sources:body.sources});data=t;
 } else if(path.endsWith('/api/public/topics')&&method==='POST'){
  data={id:'newtheme',name:body.name,keywords:body.keywords,sources:body.sources};
  window.__mockTopics.push(data);status=201;
 } else if(path.includes('/api/public/topics'))data={topics:window.__mockTopics,supported_sources:['arxiv','openalex','crossref']};
 else if(path.includes('/api/public/dashboard'))data={papers:1,starred:1,read:0,reports:0,to_read:1,pending_review:2,
   pending_papers:[{id:'2601.12345',title:'Geometry-aware neural operator',first_page:3,pending_count:2}],
   recent:[{id:'2601.12345',title:'Geometry-aware neural operator',published:'2026-11-01'}]};
 else if(path.includes('/api/public/papers'))data={papers:[{id:'2601.12345',title:'Geometry-aware neural operator',
   published:'2026-11-01',abstract:'Learning robust operators over variable geometry.',doi:'',url:'https://arxiv.org/abs/2601.12345',
   is_starred:1,reading_status:'reading',feedback:0}],total:1,limit:12,offset:0};
 else if(path.includes('/api/public/reports'))data={reports:[]};
 else if(path.includes('/api/public/jobs/history'))data={jobs:[]};
 else if(path.includes('/api/public/jobs'))data={state:'idle'};
 else if(path.includes('/api/public/quotas'))data={quotas:{search:{remaining:10},analyze:{remaining:20},synthesize:{remaining:30},model_calls:{remaining:6}}};
 else if(path.includes('/api/public/synthesis'))data={summary:{comparisons:[]},markdown:''};
 else data={error:'Unexpected mock API '+path};
 return {ok:status<400,status,json:async()=>data};
};
'''


def run(screenshot:Path|None=None,browser_path:str='/usr/bin/chromium')->dict:
    from playwright.sync_api import sync_playwright
    html=(ROOT/'paper_radar/static/portal.html').read_text(encoding='utf-8')
    html=html.replace('<link rel="stylesheet" href="/portal.css"><script defer src="/portal.js"></script>','')
    css=(ROOT/'paper_radar/static/portal.css').read_text(encoding='utf-8')
    js=(ROOT/'paper_radar/static/portal.js').read_text(encoding='utf-8')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=browser_path,args=['--no-sandbox','--disable-dev-shm-usage'],headless=True)
        page=browser.new_page(viewport={'width':1440,'height':900},locale='zh-CN')
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        try:
            page.set_content(html,wait_until='domcontentloaded')
            page.add_style_tag(content=css)
            page.evaluate(STUB)
            page.add_script_tag(content=js)
            page.locator('#viewWorkspace').wait_for(state='visible',timeout=6000)
            page.locator('#topicSetup summary').click()
            page.locator('#newTopic').fill('Industrial forecasting')
            page.locator('#newKeywords').fill('time series,mechanism learning')
            page.locator('[data-sources="new"][value="openalex"]').check()
            page.locator('#topicAdd').click()
            page.wait_for_function("document.querySelector('#editTopicName').value==='Industrial forecasting'",timeout=6000)
            if not page.locator('#topicSetup').evaluate('(el)=>el.open'):
                page.locator('#topicSetup summary').click()
            page.locator('[data-sources="edit"][value="crossref"]').check()
            page.locator('#topicSave').click()
            page.wait_for_function("document.querySelector('#homeSourceStatus').textContent.includes('Crossref')")
            assert page.locator('#homePendingPapers').is_visible()
            page.locator('#openReadingQueue').click()
            page.locator('#viewLibrary').wait_for(state='visible')
            page.locator('[data-view="reports"]').click()
            page.locator('#viewReports').wait_for(state='visible')
            page.locator('[data-view="workspace"]').click()
            if screenshot:
                screenshot.parent.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(screenshot),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.wait_for_timeout(100)
            mobile_overflow=page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 2')
            if screenshot:page.screenshot(path=str(screenshot.with_name(screenshot.stem+'_mobile.png')),full_page=True)
            assert not mobile_overflow,'Mobile layout overflow'
            assert not errors,repr(errors)
            return {'offline_ui':'pass','topic_create':'pass','topic_update':'pass',
                    'navigation':'pass','mobile_overflow':False,'javascript_errors':errors}
        finally:browser.close()


def main(argv=None):
    ap=argparse.ArgumentParser();ap.add_argument('--screenshot',type=Path);ap.add_argument('--browser-path',default='/usr/bin/chromium')
    args=ap.parse_args(argv);print(run(screenshot=args.screenshot,browser_path=args.browser_path))
    return 0

if __name__=='__main__':raise SystemExit(main())
