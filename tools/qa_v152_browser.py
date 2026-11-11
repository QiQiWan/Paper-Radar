# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Isolated UI smoke test. Data is synthetic; no user content is bundled."""
from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from paper_radar.core import connect,save_papers
from paper_radar.models import Paper
from paper_radar.research_intelligence import preview_local,run_local,read_local

ROOT=Path(__file__).resolve().parents[1]
QUESTION='Neural operator mesh geometry generalization benchmark experiments'
TEXT=('We propose a neural operator across unseen geometries and changing mesh resolution. '
      'We evaluate experiments on several PDE benchmarks and compare against finite element baselines. '
      'Results demonstrate transfer under the studied mesh splits. '
      'However, no evaluation on unseen materials was reported. ')


def make_data(path):
    with connect(path) as db:
        save_papers(db,[Paper(paper_id=f'2602.{10000+i}',title=f'Neural operator mesh generalization {i}',abstract=TEXT,
            authors='Example Researcher',published='2026-02-08',updated='2026-02-08',category='cs.LG',url='',pdf_url='',source='arxiv',doi='')
            for i in range(18)])
    preview=preview_local(path,'default',QUESTION)
    report=run_local(path,'default',QUESTION,selection_mode='all')
    return preview,read_local(path,'default',report['id'])


def main():
    output=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/mnt/data/Paper-Radar_v152_UI_QA')
    output.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory() as temp:
        preview,record=make_data(Path(temp)/'papers.sqlite3')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
        try:
            for width,height,label in [(1280,900,'desktop'),(390,844,'mobile')]:
                page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content('<!doctype html><html><head><meta charset="utf-8"></head>'
                    '<body style="margin:0;background:#f3f6fa;color:#253c52;font-family:system-ui, sans-serif">'
                    '<main style="max-width:1040px;margin:18px auto;padding:22px;background:white;border-radius:14px">'
                    '<div id="localInquiryWorkbench"></div></main></body></html>')
                for name in ['app.css','ux_v149.css']:
                    page.add_style_tag(path=str(ROOT/'paper_radar/static'/name))
                page.add_script_tag(path=str(ROOT/'paper_radar/static/inquiry_workbench.js'))
                page.evaluate('''([preview,record])=>{
                  window.calls=[];
                  window.request=async(path,method,payload)=>{
                    if(path.startsWith('/api/inquiry/preview'))return preview;
                    if(path.startsWith('/api/inquiry/jobs')){
                      window.calls.push(payload);
                      return {run_id:'a'.repeat(32),state:'queued'};
                    }
                    if(path.startsWith('/api/inquiry/job?'))return {state:'completed',report_id:'b'.repeat(32),progress:100};
                    if(path.startsWith('/api/inquiry/view'))return record;
                    if(path==='/api/inquiry/latest')return {state:'none'};
                    if(path==='/api/abstracts/quality')return {total:18,needs_refresh:0,checked_without_improvement:0,by_quality:{adequate:18}};
                    if(path.startsWith('/api/trace'))return {counts:{confirmed_current:0}};
                    if(path.startsWith('/api/inquiry?')||path==='/api/inquiry')return {reports:[]};
                    throw Error('Unmocked '+path);
                  };
                  window.PRInquiry.mount({root:document.getElementById('localInquiryWorkbench'),request:window.request,
                    getTopic:()=> 'default',getModelState:()=>({enabled:true,ready:false})});
                }''',[preview,record])
                page.locator('.inquiry-question').fill(QUESTION)
                page.locator('.inquiry-scope-mode').select_option('all')
                page.get_by_role('button',name='查看分析范围').click()
                page.wait_for_timeout(120)
                assert '18 篇' in page.locator('.inquiry-scope-info').inner_text()
                page.screenshot(path=str(output/f'scope_{label}.png'),full_page=True)
                page.get_by_role('button',name='开始分析').click()
                page.wait_for_timeout(1000)
                assert page.locator('.inquiry-output h3').inner_text()=='研究分析结果'
                assert len(page.evaluate('window.calls'))==1
                assert page.evaluate('window.calls[0].selection_mode')=='all'
                page.screenshot(path=str(output/f'result_{label}.png'),full_page=True)
                overshoot=page.evaluate('document.documentElement.scrollWidth > window.innerWidth+2')
                if errors or overshoot:raise AssertionError({'errors':errors,'overflow':overshoot,'size':label})
                page.close()
            print(json.dumps({'status':'pass','screenshots':4,'console_errors':0,'overflow':0},ensure_ascii=False))
        finally:browser.close()

if __name__=='__main__':main()
