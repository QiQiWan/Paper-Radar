"""Static Chromium layout QA of both v1.47 workspaces (no backend or network)."""
from pathlib import Path
from playwright.sync_api import sync_playwright
import re,shutil,json,os
S=Path(__file__).resolve().parents[1]/'paper_radar/static'
OUT=Path(os.environ.get('PAPER_RADAR_QA_OUTPUT',str(Path(__file__).resolve().parents[1]/'qa_v147_out')));OUT.mkdir(parents=True,exist_ok=True)
results=[]
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,executable_path=shutil.which('chromium'),args=['--no-sandbox','--disable-dev-shm-usage'])
 for mode in ['local','public']:
  file='index.html' if mode=='local' else 'portal.html'
  html=(S/file).read_text('utf-8')
  html=re.sub(r'<script\b[^>]*\bsrc="[^"]+"[^>]*>\s*</script>','',html)
  html=re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>','',html)
  styles=['app.css','settings.css','ui_polish.css','ux_v147.css','research_program.css'] if mode=='local' else ['portal.css','portal_polish.css','portal_ux_v147.css','research_program.css']
  css='\n'.join((S/f).read_text('utf-8') for f in styles)
  for width,height in [(1440,900),(390,844)]:
   page=b.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
   page.set_content(html,wait_until='domcontentloaded')
   page.add_style_tag(content=css)
   if mode=='public':
    page.evaluate('''() => {document.getElementById('authView').hidden=true;document.getElementById('appView').hidden=false;
      const topic=document.getElementById('topicSelect');topic.innerHTML='<option>神经算子与多物理场学习</option>';
      document.getElementById('portalQuickKeywords').value='neural operator, model reduction';
      document.getElementById('portalSearchSummary').textContent='当前主题：神经算子与多物理场学习。检索结果自动保存到“我的文献”。';
    }''')
   else:
    page.locator('#homeTopicName').evaluate('(el)=>el.textContent="当前主题：神经算子与多物理场学习"')
    page.locator('#homeLastStatus').evaluate('(el)=>el.textContent="尚无检索记录"')
   views=[('home','home'),('dashboard','search'),('library','library'),('inquiry','analysis'),('reports','reports')] if mode=='local' else [('workspace','home'),('tasks','search'),('library','library'),('inquiry','analysis'),('reports','reports')]
   for view,name in views:
    if mode=='local':
     page.evaluate('''(id)=>{document.querySelectorAll('.view').forEach(el=>el.classList.toggle('active',el.id===id));
       document.querySelectorAll('.navigation [data-view]').forEach(el=>{el.classList.toggle('active',el.dataset.view===id);if(el.dataset.view===id)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});}''',view)
    else:
     page.evaluate('''(id)=>{document.querySelectorAll('.portal-page').forEach(el=>el.hidden=el.dataset.page!==id);
       document.querySelectorAll('.primary-nav [data-view]').forEach(el=>{el.classList.toggle('active',el.dataset.view===id);if(el.dataset.view===id)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});}''',view)
    page.screenshot(path=str(OUT/f'{mode}_{name}_{width}.png'),full_page=(width==1440),animations='disabled')
    overflow=page.evaluate('document.documentElement.scrollWidth-window.innerWidth')
    results.append({'mode':mode,'width':width,'page':name,'overflow_px':overflow})
   page.close()
 b.close()
print(json.dumps(results,ensure_ascii=False,indent=2))
(OUT/'static_layout_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),'utf-8')
