"""Offline Chromium UI and math/card checks using synthetic fixture data."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from playwright.sync_api import sync_playwright
from paper_radar.core import connect,save_papers
from paper_radar.models import Paper
from paper_radar.research_intelligence import run_local,read_local

STATIC=ROOT/'paper_radar'/'static'
OUTPUT=Path('/mnt/data/paper_radar_v151_ui')
OUTPUT.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as temp:
    path=Path(temp)/'db.sqlite3'
    with connect(path) as db:
        for i,(title,abstract) in enumerate([
            ('Neural operators across mesh resolutions and unseen geometries',
             'We compare neural operator predictions on unseen geometries and mesh resolutions, including $R^2$ evaluation.'),
            ('Fourier neural operators on unstructured meshes',
             'The neural operator solves PDEs on unstructured meshes. Generalization is assessed over grid sizes and boundary settings.'),
            ('Physics-informed neural operators for variable geometries',
             'We assess transfer to new geometries under changed boundary conditions using a neural operator.'),
            ('Neural operator baseline on elliptic equations',
             'This neural operator predicts the solution of elliptic PDE problems; no grid generalization was evaluated.'),
            ('Finite element mesh adaptation with moving boundaries',
             'A finite element algorithm for anisotropic meshes, unrelated to operator learning.'),
            ('Neural operators and sparse observations',
             'A dataset study of neural operators in physics with sparse measurements.'),
        ],1):
            save_papers(db,[Paper(paper_id=f'arxiv:{i}',title=title,abstract=abstract,
                authors='Example',published='2026-11-09',updated='2026-11-09',
                category='cs.LG',url='https://example.org/'+str(i),pdf_url='',doi='')])
    question='神经算子在不同网格和几何条件下的泛化能力，现有实验有哪些一致结果与限制？'
    data=run_local(path,'default',question)
    record=read_local(path,'default',data['id'])

    page_html='''<!doctype html><html><head><meta charset="utf-8"><style>
      :root{--surface:#fff;--bg:#f6f8fb;--muted:#60758a;--ink:#20344a;--border:#dfe6ee;--primary:#275ba4;--blue:#275ba4}
      *{box-sizing:border-box} body{background:#f6f8fb;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;color:#253a50;margin:0;padding:30px}
      #app{max-width:1100px;margin:auto;background:white;border:1px solid #dde7f0;border-radius:15px;padding:30px 35px}
      button{cursor:pointer;padding:8px 14px;border:1px solid #cad8e7;border-radius:8px;background:#fff;color:#305d8d}
      button.inquiry-primary{background:#2b5ca3;color:white} 
      .inquiry-workbench h2{font-size:20px} .inquiry-question{width:100%}
      .inquiry-muted{color:#667b90} .inquiry-actions{display:flex;gap:10px} 
      @media(max-width:700px){body{padding:8px}#app{padding:18px 14px}}
    </style></head><body><main id="app"><div id="inquiry"></div></main></body></html>'''
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
        for width in (1280,390):
            page=browser.new_page(viewport={'width':width,'height':900},device_scale_factor=1)
            page.set_content(page_html)
            page.add_style_tag(path=str(STATIC/'app.css'))
            page.add_style_tag(path=str(STATIC/'ux_v149.css'))
            page.add_script_tag(path=str(STATIC/'inquiry_workbench.js'))
            page.add_script_tag(path=str(STATIC/'math_render.js'))
            page.evaluate('''(fixture) => {
              window.fixture=fixture;
              window.inquiryUI=window.PRInquiry.mount({root:document.querySelector('#inquiry'),
                request:async(path)=>{
                  if(path==='/api/trace')return {counts:{confirmed_current:0}};
                  if(path.startsWith('/api/inquiry/view'))return window.fixture.record;
                  if(path.startsWith('/api/inquiry?'))return {reports:[{id:window.fixture.id,question:window.fixture.question,created_at:'2026-11-10T12:29:42+00:00'}]};
                  if(path==='/api/inquiry')return {reports:[]};
                  if(path.endsWith('/api/inquiry/run'))return window.fixture.result;
                  return {reports:[]};
                },getTopic:()=> 'default',getModelState:()=>({enabled:true,ready:false}),
                onNavigate:()=>{}});
              window.inquiryUI.setQuestion(fixture.question);
            }''',{'record':record,'id':data['id'],'result':data,'question':question})
            page.evaluate('(id)=>window.inquiryUI.open(id)',data['id'])
            page.wait_for_timeout(500)
            assert page.locator('.inquiry-paper-card').count()>0
            assert page.locator('.inquiry-history-disclosure').count()==1
            horizontal=page.evaluate('document.documentElement.scrollWidth-document.documentElement.clientWidth')
            assert horizontal<=1, f'Overflow at {width}: {horizontal}'
            page.screenshot(path=str(OUTPUT/f'inquiry_{width}.png'),full_page=True)
            page.close()
        public='''<!doctype html><html><head><meta charset="utf-8"><style>
        body{background:#f5f7fa;font-family:Arial;margin:0;padding:25px;color:#26394f}
        #paperList{max-width:960px;margin:auto}.paper{background:#fff}
        .paper-head{display:flex;align-items:flex-start}.paper-title-group{flex:1}
        button{padding:9px 13px;border:1px solid #d3e0ed;border-radius:8px;background:#fff;color:#2a5a90}
        @media(max-width:700px){body{padding:10px}}
        </style></head><body><div id="paperList"><article class="paper paper-card-v1493">
        <div class="paper-head"><div class="paper-title-group"><strong>Geometry transfer of neural operators with $R^2$ and $\\frac{1}{N}\\sum_i e_i^2$</strong></div>
        <label class="paper-selection"><input type="checkbox" aria-label="选择论文"></label><button class="star-toggle">☆ 收藏</button></div>
        <small>arXiv · 2026-11-10 · cs.LG</small>
        <p class="paper-preview">This study reports evaluations of geometry generalization in a neural operator framework with finite element solution fields. Its observations require verification against full PDF experiment tables.</p>
        <details class="paper-abstract-more"><summary>展开完整摘要</summary><p>Original unverified abstract text with $E=mc^2$.</p></details>
        <div class="paper-tools"><button>阅读论文</button><button>阅读状态</button><button>更多操作</button></div>
        </article></div></body></html>'''
        for width in (1280,390):
            page=browser.new_page(viewport={'width':width,'height':700})
            page.set_content(public)
            page.add_style_tag(path=str(STATIC/'ux_v149.css'))
            page.add_script_tag(path=str(STATIC/'math_render.js'))
            page.wait_for_timeout(300)
            assert page.locator('#paperList math').count()>=2
            assert page.evaluate('document.documentElement.scrollWidth-document.documentElement.clientWidth')<=1
            page.screenshot(path=str(OUTPUT/f'paper_card_{width}.png'),full_page=True)
            page.close()
        browser.close()
print('UI_VERIFIED 4 screenshots',OUTPUT)
