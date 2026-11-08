"""Visual regression preview of local HTML/CSS without network. Optional QA dependencies: beautifulsoup4, playwright, chromium."""
import os,json
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parent.parent
STATIC=ROOT/'paper_radar'/'static'
OUT=ROOT/'_ui_checks';OUT.mkdir(exist_ok=True)

def inline_html(file_name,css_files):
    soup=BeautifulSoup((STATIC/file_name).read_text(encoding='utf-8'),'html.parser')
    for x in soup.find_all('script'): x.decompose()
    for x in soup.find_all('link',href=True): x.decompose()
    for cssfile in css_files:
        style=soup.new_tag('style');style.string=(STATIC/cssfile).read_text(encoding='utf-8');soup.head.append(style)
    return str(soup)

fixture='''<details class="paper-group" open><summary>neural operator <small>8 篇 · 本次已加载</small></summary>
<article class="paper-item"><a class="paper-title" href="#">Deep Neural Operator Learning for Stochastic Heat Equations and Applications</a><div class="paper-meta">2026-11-03 · arxiv · cs.LG</div>
<p class="paper-summary">In this paper, we establish a universal approximation theorem for solution operators of semilinear stochastic heat equations in spatial dimensions, extending deep neural operator learning to uncertain boundary conditions...</p>
<div class="paper-tags"><span class="tag">含原始摘要</span></div>
<div class="paper-actions"><label class="select-paper"><input type="checkbox" checked> 选择</label><button class="subtle-button">☆ 星标</button><select class="select read-select" ><option>未阅读</option></select><details class="paper-extra-actions"><summary>更多操作</summary></details></div>
<details class="paper-detail"><summary>查看摘要与阅读要点</summary><p><b>原始摘要</b>：Deep neural operator learning for stochastic PDEs.</p></details>
<details class="local-reading-note" open><summary>个人阅读笔记</summary><div class="local-reading-note-fields"><label>人工阅读笔记<textarea class="input local-note-input" rows="3" placeholder="核心观点、局限、与研究课题的关系…"></textarea></label><label>下一步行动<select class="select local-note-action"><option>暂不安排</option></select></label></div><div class="local-note-footer"><button class="button secondary local-note-save">保存笔记</button><button class="button tertiary local-note-discard" disabled>丢弃本页草稿</button><span class="minor local-note-feedback">仅保存在当前研究主题；保存后可导出</span></div></details></article>
<article class="paper-item"><a class="paper-title" href="#">WaSP: Wasserstein Data Synthesis Pipeline for Solver-Free Operator Learning</a><div class="paper-meta">2026-11-01 · Crossref · Machine Learning</div><p class="paper-summary">We present a robust Wasserstein data synthesis pipeline for improving scientific machine learning generalization.</p></article></details>'''


def take_screens(tag):
    specs=[('desktop',1440,900),('laptop',1024,850),('mobile',390,844)]
    html=inline_html('index.html',['app.css','settings.css','ui_polish.css'] if (STATIC/'ui_polish.css').exists() else ['app.css','settings.css'])
    metrics=[]
    with sync_playwright() as p:
      browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
      for label,width,height in specs:
       page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
       page.set_content(html,wait_until='domcontentloaded')
       page.locator('#libraryList').evaluate('(el,html)=>el.innerHTML=html',fixture)
       page.locator('#libraryStatus').evaluate("e=>e.textContent='找到 27 篇论文'")
       page.locator('.library-more-filters').evaluate('e=>e.open=true')
       page.locator('#settings-search').evaluate('e=>e.open=true')
       for name in ['dashboard','library','tasks','following','inquiry','reports','settings','downloads','zotero']:
        page.evaluate('''(name)=>{document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===name)); document.querySelectorAll('.nav-link').forEach(a=>a.classList.toggle('active',a.dataset.view===name))}''',name)
        page.screenshot(path=str(OUT/f'{label}_{name}_{tag}.png'),full_page=False)
        metric=page.evaluate('''() => {const sel=document.querySelector('.view.active');const els=[...sel.querySelectorAll('button,a,select,input,textarea,label,p,summary,small,.paper-title')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&r.top<innerHeight&&r.bottom>0&&getComputedStyle(e).visibility!=='hidden'});return {w:document.documentElement.scrollWidth,viewport:innerWidth,count:els.length,tiny:els.filter(e=>parseFloat(getComputedStyle(e).fontSize)<13).length,buttonSmall:els.filter(e=>e.matches('button')&&e.getBoundingClientRect().height<36).length,samples:els.filter(e=>parseFloat(getComputedStyle(e).fontSize)<13).slice(0,8).map(e=>(e.textContent||'').trim().slice(0,25))}}''')
        metrics.append({'viewport':label,'page':name,**metric})
       page.close()
      # public portal
      portal=inline_html('portal.html',['portal.css','portal_polish.css'] if (STATIC/'portal_polish.css').exists() else ['portal.css'])
      page=browser.new_page(viewport={'width':1440,'height':900});page.set_content(portal)
      page.screenshot(path=str(OUT/f'desktop_portal_{tag}.png'))
      page.close()
      browser.close()
    (OUT/f'{tag}_metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(metrics,ensure_ascii=False,indent=2))
if __name__=='__main__': take_screens(os.getenv('UI_TAG','before'))
