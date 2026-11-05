// Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
'use strict';
const TOKEN = document.querySelector('meta[name="local-token"]').content;
let config = null, providers = {}, jobWasBusy = false, loading = false, schedule = null;
let zoteroCollections = [], zoteroConnected = false, zoteroPreviewReady = false;
let libraryOffset = 0, libraryTotal = 0, libraryLoading = false, libraryRequestId = 0;
let libraryPapers = [], selectedPapers = new Set(), sourceDefinitions = [], sourceAccess = {}, providerInfo = {}, providerUsage = {};
let markdownRaw = '', activeReport = '近期文献汇总.md';
let proposedPlan = null, activeTopicCache = "", latestUpdateTag = "";
let activeFulltextId = '', fulltextData = null, fulltextWaiting = false;
const $ = id => document.getElementById(id);
const escape = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeLink = value => {try{const x=new URL(value);return ['https:','http:'].includes(x.protocol)?x.href:'#'}catch{return '#'}};
const commaList = raw => String(raw||'').split(/[,，;；\n]/).map(x=>x.trim()).filter(Boolean);
// A small, no-dependency Markdown viewer. All paper metadata is escaped first;
// outbound hyperlinks are permitted only when they use HTTP(S).
function formatPlain(raw){return escape(raw).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')}
function formatInline(raw){
  let out='',pos=0;const rx=/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
  for(const match of String(raw).matchAll(rx)){
    out+=formatPlain(raw.slice(pos,match.index));
    const url=safeLink(match[2]);
    out+=url==='#'?formatPlain(match[1]):`<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${formatPlain(match[1])}</a>`;
    pos=match.index+match[0].length;
  }
  return out+formatPlain(raw.slice(pos));
}
function renderMarkdownSafe(source){
  const out=[];let list='';
  const close=()=>{if(list){out.push(`</${list}>`);list=''}};
  for(const raw of String(source||'').replace(/\r\n?/g,'\n').split('\n')){
    const line=raw.trim();
    if(!line){close();continue}
    const heading=line.match(/^(#{1,4})\s+(.+)$/);
    if(heading){close();const n=heading[1].length;out.push(`<h${n}>${formatInline(heading[2])}</h${n}>`);continue}
    if(/^---+$/.test(line)){close();out.push('<hr>');continue}
    const bullet=line.match(/^[-*]\s+(.+)$/), numbered=line.match(/^\d+[.)]\s+(.+)$/);
    if(bullet||numbered){const type=bullet?'ul':'ol';if(list!==type){close();out.push(`<${type}>`);list=type}out.push(`<li>${formatInline((bullet||numbered)[1])}</li>`);continue}
    close();out.push(`<p>${formatInline(line)}</p>`);
  }
  close();return out.join('\n');
}
async function showMarkdownPreview(name='近期文献汇总.md'){
  const dialog=$('markdownDialog');activeReport=name;
  $('markdownTitle').textContent=name;
  $('markdownRendered').textContent='正在加载 Markdown…';
  $('markdownDownload').href='/download/'+encodeURIComponent(name);
  $('markdownSize').textContent='本地报告 · 阅读模式';
  if(!dialog.open)dialog.showModal();
  try{
    const response=await api('/api/report-preview?name='+encodeURIComponent(name));
    markdownRaw=response.content||'';
    $('markdownRendered').innerHTML=renderMarkdownSafe(markdownRaw);
    $('markdownSize').textContent=`${markdownRaw.length.toLocaleString('zh-CN')} 字符 · 本地预览`;
  }catch(error){markdownRaw='';$('markdownRendered').textContent='预览失败：'+error.message;}
}

async function planResearchKeywords(){
  const button=$('aiPlanRequest'),status=$('aiPlanStatus');
  const goal=$('aiResearchGoal').value.trim();
  if(goal.length<12){notice('请先具体描述研究对象、方法和想跟踪的问题',true);return}
  proposedPlan=null;$('aiPlanResult').hidden=true;button.disabled=true;
  status.textContent='模型正在规划检索词…';
  try{
    const data=await api('/api/ai/plan',{goal});
    proposedPlan=data.plan;
    $('aiPlanTopic').textContent=`主题：${data.plan.topic||goal.slice(0,80)}`;
    $('aiPlanWords').value=(data.plan.keywords||[]).join(', ');
    const notes=(data.plan.notes||[]).join('；');
    const exclusions=(data.plan.exclusions||[]).join('、');
    $('aiPlanNotes').textContent=[notes,exclusions?`可能需要排除的概念（仅提示，暂不参与过滤）：${exclusions}`:''].filter(Boolean).join('。');
    $('aiPlanResult').hidden=false;
    status.textContent=`已提出 ${data.plan.keywords.length} 个检索词，请核对后应用。`;
  }catch(error){status.textContent='生成失败';notice(error.message,true)}
  finally{button.disabled=false}
}

function applyResearchPlan(){
  if(!proposedPlan){notice('请先生成检索方案',true);return}
  const words=commaList($('aiPlanWords').value).slice(0,20);
  if(!words.length){notice('请保留至少一个检索词',true);return}
  $('topicKeywords').value=words.join(', ');
  $('aiPlannerDisclosure').open=false;
  notice('已应用检索词。点击“开始检索并生成报告”后才会保存并执行。');
}

function renderTopics(info){
  const topics=info?.topics||[];
  const active=info?.active_topic_id||'default';
  const signature=JSON.stringify([active, topics.map(t=>[t.id,t.name])]);
  if(signature!==activeTopicCache){
    const select=$('activeTopic');select.innerHTML=topics.map(t=>`<option value="${escape(t.id)}">${escape(t.name)}</option>`).join('');
    select.value=active;activeTopicCache=signature;
  }
  $('deleteTopic').disabled=active==='default';
}
function renderUpdate(status){
  if(!status)return;
  const available=status.state==='available' && !status.dismissed && status.enabled;
  $('updateBanner').hidden=!available;
  $('updateBannerTitle').textContent=available?`发现 Paper-Radar v${status.latest_version}`:'版本状态';
  $('updateBannerText').textContent='当前版本 '+(status.current_version||'未知')+'。关闭工作台后重新启动将尝试自动升级；若安装受阻，可在官方发布页手动下载。';
  $('updateReleaseLink').href=safeLink(status.release_url||status.releases_url||'');
  latestUpdateTag=status.tag_name||'';
  const summary=status.checking?'正在后台检查 GitHub Release…':
    status.state==='available'?'发现新版本 v'+status.latest_version+(status.dismissed?'（本版本提醒已关闭）':''):
    status.state==='up_to_date'?'已核对：当前版本不低于最新公开正式版。':
    status.state==='unpublished'?'仓库尚未发布正式 Release。':
    status.state==='disabled'?'自动检查已关闭，可手动检查。':
    status.state==='error'?(status.message||'更新检查失败。'):'尚未进行更新检查。';
  $('updateStatusText').textContent=summary+(status.checked_at?' 上次检查：'+status.checked_at.slice(0,16).replace('T',' ')+' UTC':'');
  const boot=status.startup_update||{};
  $('startupUpdateStatusText').textContent='启动更新：'+(boot.message||'尚无检查记录。')+(boot.at?'（'+boot.at.slice(0,16).replace('T',' ')+' UTC）':'');
  $('checkUpdate').disabled=!!status.checking;
  $('updateLatestLink').href=safeLink(status.release_url||status.releases_url||'');
}
async function topicAction(action,topicId='',name=''){
  try{
    const data=await api('/api/topics',{action,topic_id:topicId,name});
    renderTopics(data);await updateState(true);await loadRecent();
    if(document.querySelector('#library.view.active')){selectedPapers.clear();await loadLibrary();}
    if(document.querySelector('#reports.view.active'))loadReports();
    notice(({create:'已创建并切换研究主题',switch:'已切换研究主题',rename:'主题名称已更新',delete:'主题已删除，报告历史仍保留'})[action]);
  }catch(e){notice(e.message,true);const latest=await api('/api/topics').catch(()=>null);if(latest)renderTopics(latest)}
}
function notice(text, isError=false){const el=$('toast'); el.textContent=text; el.className='visible'+(isError?' error':'');clearTimeout(notice.timer);notice.timer=setTimeout(()=>el.className='',3900)}
async function api(url, body=null){let opts={cache:'no-store'};if(body!==null){opts={method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':TOKEN},body:JSON.stringify(body)}}const res=await fetch(url,opts);let data;try{data=await res.json()}catch{throw new Error('服务连接异常')}if(!res.ok)throw new Error(data.error||'操作失败');return data}
const visible = name => {
  document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id===name));
  document.querySelectorAll('.nav-link').forEach(x=>{
    const active=x.dataset.view===name;x.classList.toggle('active',active);
    if(active)x.setAttribute('aria-current','page');else x.removeAttribute('aria-current');
  });
  if(name==='zotero'||name==='downloads')$('localExtraTools').open=true;
  $('breadcrumbLabel').textContent=({dashboard:'工作台',library:'论文库',tasks:'任务中心',
    downloads:'PDF 下载',reports:'研究报告',settings:'设置',zotero:'Zotero 研究方向'})[name];
  if(name==='library')loadLibrary();if(name==='tasks')loadTaskView();
  if(name==='reports')loadReports();if(name==='downloads')loadDownloads();
  if(name==='settings'){refreshProviderCatalog();loadAgents();}
  window.scrollTo({top:0,behavior:'smooth'});
};
function renderPaper(p,compact=false){
  const full=p.summary||p.abstract||'';
  const excerpt=full.slice(0,compact?100:175)+(full.length>(compact?100:175)?'…':'');
  const starred=!!p.is_starred;
  const feedback=Number(p.feedback||0);
  const noAbstract=!String(p.abstract||'').trim();
  const status=noAbstract?'<span class="tag">仅题录 · 待核查</span>':p.summary?'<span class="tag ai">有中文速览</span>':'<span class="tag">含原始摘要</span>';
  return `<article class="paper-item"><a class="paper-title" target="_blank" rel="noopener noreferrer" href="${escape(safeLink(p.url))}">${escape(p.title)}</a>
    <div class="paper-meta">${escape((p.published||'').slice(0,10)||'日期待核对')} · ${escape(p.source||'未知来源')} · ${escape(p.venue||p.category||'未分类')}</div>
    ${excerpt?`<p class="paper-summary">${escape(excerpt)}</p>`:'<p class="paper-summary">原始来源暂无摘要，建议打开论文主页核查。</p>'}
    ${p.recommend_reason?`<p class="paper-reason"><strong>AI 推荐理由（仅基于题名摘要）：</strong>${escape(p.recommend_reason)}${p.recommend_evidence?`<span class="minor"> · 原文依据：${escape(p.recommend_evidence)}</span>`:''}</p>`:''}
    <div class="paper-tags">${status}${String(p.source||'').includes('openalex_semantic')?'<span class="tag">语义发现 · 待核查</span>':''}${p.download_status==='downloaded'?'<span class="tag ai">PDF 已保存</span>':''}${starred?'<span class="tag">★ 已标记</span>':''}</div>
    ${compact?'':`<div class="paper-actions"><label class="select-paper"><input type="checkbox" class="paper-check" data-id="${escape(p.paper_id)}" /> 选择</label><button type="button" class="subtle-button ${starred?'selected':''}" data-action="star" data-id="${escape(p.paper_id)}" data-next="${starred?'false':'true'}">${starred?'★ 取消星标':'☆ 星标'}</button><button class="subtle-button feedback-up ${feedback===1?'selected':''}" data-id="${escape(p.paper_id)}" data-feedback="${feedback===1?0:1}" type="button">${feedback===1?'✓ 相关':'相关'}</button><button class="subtle-button feedback-down ${feedback===-1?'selected':''}" data-id="${escape(p.paper_id)}" data-feedback="${feedback===-1?0:-1}" type="button">${feedback===-1?'✓ 不相关':'不相关'}</button><select class="select read-select" style="width:116px;padding:5px 7px;font-size:11px" data-id="${escape(p.paper_id)}"><option value="unread" ${p.reading_status==='unread'?'selected':''}>未阅读</option><option value="reading" ${p.reading_status==='reading'?'selected':''}>阅读中</option><option value="done" ${p.reading_status==='done'?'selected':''}>已读完</option></select>${p.local_file&&p.download_status==='downloaded'?`<a class="subtle-button" href="/api/pdf/${encodeURIComponent(p.paper_id)}" target="_blank" rel="noopener">打开 PDF</a><button type="button" class="subtle-button fulltext-open" data-id="${escape(p.paper_id)}">全文证据</button>`:p.download_url?`<button type="button" class="subtle-button pdf-button" data-id="${escape(p.paper_id)}">下载 PDF</button>`:''}</div><details class="paper-detail"><summary>展开原始摘要与中文速览</summary>${p.summary?`<p><strong>中文速览（需核对）</strong>：${escape(p.summary)}</p>`:''}<p><strong>原始摘要</strong>：${escape(p.abstract||'未提供摘要，需查看原文。')}</p></details>`}
  </article>`;
}
// v1.12: One local fulltext drawer, opened on demand. PDF text is indexed on
// the user's computer; cloud AI receives only a capped set of selected snippets.
async function openFulltext(id){
  activeFulltextId=id;fulltextData=null;
  $('fulltextTitle').textContent='全文证据';
  $('fulltextState').textContent='读取本地 PDF 索引…';
  $('fulltextClaims').innerHTML='';$('fulltextMatches').innerHTML='';
  $('fulltextSearchInput').value='';
  $('fulltextExperimentsResults').innerHTML='<div class="empty">正在读取实验结构化结果…</div>';
  const dialog=$('fulltextDialog');if(!dialog.open)dialog.showModal();
  await refreshFulltext();
}
async function refreshFulltext(){
  if(!activeFulltextId)return;
  const current=activeFulltextId;
  try{
    const info=await api('/api/fulltext/status?paper_id='+encodeURIComponent(current));
    if(current!==activeFulltextId)return;
    fulltextData=info;
    $('fulltextTitle').textContent=info.title||'全文证据';
    const doc=info.document||{};
    const state={indexed:'全文索引已就绪',partial:'部分页面已索引',no_text:'未提取到文字',stale:'PDF 已变更，请重新解析',cached:'已缓存'}[doc.state]||'尚未建立全文索引';
    $('fulltextState').textContent=`${state} · ${Number(doc.indexed_pages||0)} / ${Number(doc.total_pages||0)} 页 · ${Number(doc.chunk_count||0)} 个片段`;
    $('fulltextNote').textContent=doc.note||info.note||'';
    $('fulltextIndex').disabled=!info.has_pdf||fulltextWaiting;
    $('fulltextAnalyze').disabled=!info.indexed||fulltextWaiting;
    $('fulltextCopy').disabled=!(info.claims||[]).length;
    $('fulltextExperiments').disabled=!info.has_pdf||fulltextWaiting;
    const frame=$('fulltextPdfViewer');
    if(info.has_pdf){
      const url='/api/pdf/'+encodeURIComponent(current)+'#page=1';
      if(frame.getAttribute('src')!==url)frame.src=url;
      $('fulltextPdfHint').textContent='点击右侧阅读卡片的页码，可同步定位当前 PDF。';
    }else{
      frame.removeAttribute('src');
      $('fulltextPdfHint').textContent='尚未获得有效 PDF。请先下载开放版本。';
    }
    const items=info.claims||[];
    await loadExperimentResults(current);
    $('fulltextClaims').innerHTML=items.length?items.map((c,i)=>`<article class="evidence-card"><div class="evidence-meta">${escape(({method:'方法',experiment:'实验',limitation:'局限'})[c.category]||'证据')} · 第 ${Number(c.page)} 页</div><p>${escape(c.statement)}</p><blockquote>${escape(c.quote)}</blockquote><a target="_blank" rel="noopener" href="/api/pdf/${encodeURIComponent(current)}#page=${Number(c.page)}">打开 PDF 第 ${Number(c.page)} 页 ↗</a></article>`).join(''):'<div class="empty">暂无经过原文逐字校验的 AI 阅读卡片。解析 PDF 后可点击“生成阅读卡片”。</div>';
  }catch(error){$('fulltextState').textContent='读取失败：'+error.message;notice(error.message,true)}
}

async function loadExperimentResults(id){
  const root=$('fulltextExperimentsResults');
  if(!root)return;
  try{
    const data=await api('/api/experiments/status?paper_id='+encodeURIComponent(id));
    if(id!==activeFulltextId)return;
    const items=data.findings||[];
    root.innerHTML=items.length?items.map(c=>`<article class="evidence-card"><div class="evidence-meta">${escape(({metric:'定量结果',ablation:'消融实验',efficiency:'效率指标'})[c.kind]||'实验数据')} · 第 ${Number(c.page)} 页</div><p><strong>${escape(c.metric)}</strong>：${escape(c.value)} ${c.dataset?' · 数据集：'+escape(c.dataset):''} ${c.baseline?' · 对照：'+escape(c.baseline):''}</p>${c.setting?`<p class="minor">条件：${escape(c.setting)}</p>`:''}<blockquote>${escape(c.quote)}</blockquote><a target="_blank" rel="noopener" href="/api/pdf/${encodeURIComponent(id)}#page=${Number(c.page)}">核对 PDF 第 ${Number(c.page)} 页 ↗</a></article>`).join(''):`<div class="empty">${data.status==='stale'?'PDF 内容已变更，请重新索引并提取。':'暂无经过原文核验的结构化实验结果。'}</div>`;
  }catch(error){if(id===activeFulltextId)root.textContent='实验信息读取失败：'+error.message}
}

async function fulltextTask(mode){
  if(!activeFulltextId||fulltextWaiting)return;
  const pid=activeFulltextId;
  if(mode==='analyze_experiments'&&!confirm('将本地 PDF 中最多16个选定原文片段发送至当前模型服务商，提取实验结果并产生可能的调用费用。需要人工核对原文表格。继续？'))return;
  if(mode==='analyze_fulltext'&&!confirm('将本地 PDF 中最多12个选定正文片段发送至当前配置的大模型服务商，可能产生调用费用。继续？'))return;
  try{
    await api('/api/job',{mode,ids:[pid]});
    fulltextWaiting=true;
    $('fulltextState').textContent=mode==='index_fulltext'?'正在本地解析 PDF，任务进度可在工作台查看…':mode==='analyze_experiments'?'正在核对原文实验结果…':'模型正在生成证据阅读卡片…';
    $('fulltextIndex').disabled=true;$('fulltextAnalyze').disabled=true;$('fulltextExperiments').disabled=true;
    const poll=async()=>{
      try{
        const state=await api('/api/state');
        if(state.runtime?.busy){setTimeout(poll,1800);return}
      }catch(error){notice('读取任务进度失败：'+error.message,true)}
      fulltextWaiting=false;
      if($('fulltextDialog').open&&activeFulltextId===pid)await refreshFulltext();
      notice(mode==='index_fulltext'?'全文索引任务已结束，请核查结果':mode==='analyze_experiments'?'实验信息提取结束，请核查原文证据':'阅读卡片任务已结束，请核查原文证据');
    };
    setTimeout(poll,1800);
  }catch(error){fulltextWaiting=false;notice(error.message,true);await refreshFulltext()}
}
async function searchFulltext(){
  const q=$('fulltextSearchInput').value.trim();
  if(!q){$('fulltextMatches').textContent='请输入需要核对的术语或研究问题。';return}
  const paperScope=$('fulltextScope').value==='paper';
  const params=new URLSearchParams({q,scope:paperScope?'all':'topic'});
  if(paperScope)params.set('paper_id',activeFulltextId);
  $('fulltextMatches').textContent='正在查找已索引正文…';
  try{
    const data=await api('/api/fulltext/search?'+params.toString());
    $('fulltextMatches').innerHTML=data.results.length?data.results.map(item=>`<article class="evidence-card"><div class="evidence-meta">${escape(item.title)} · 第 ${Number(item.page)} 页 · ${escape(item.section)}</div><blockquote>${escape(item.quote)}</blockquote><a target="_blank" rel="noopener" href="/api/pdf/${encodeURIComponent(item.paper_id)}#page=${Number(item.page)}">打开 PDF 证据 ↗</a></article>`).join(''):'<div class="empty">本次未找到相符片段。请尝试原文英文术语，或先为相关 PDF 建立索引。</div>';
  }catch(error){$('fulltextMatches').textContent='搜索失败：'+error.message}
}
async function copyFulltextCard(){
  if(!fulltextData?.claims?.length)return;
  const content=['# '+fulltextData.title,'','以下内容为 AI 辅助阅读卡片；引文来自本地 PDF 部分页码，请自行核对。',''];
  for(const c of fulltextData.claims){content.push(`## ${({method:'方法',experiment:'实验',limitation:'局限'})[c.category]||'证据'}｜第${c.page}页`,c.statement,'','> '+c.quote,'')}
  try{await navigator.clipboard.writeText(content.join('\n'));notice('已复制含页码和原文依据的阅读卡片')}catch{notice('复制失败，请手动选择文本',true)}
}
function zoteroPayload(){return {mode:$('zoteroMode').value,db_path:$('zoteroPath').value.trim(),
  collection_key:$('zoteroCollection').value,include_children:$('zoteroChildren').checked};}
function zoteroWaiting(label, waiting){
  $('zoteroActivity').hidden=!waiting;
  $('zoteroActivityText').textContent=label;
  $('zoteroConnect').disabled=waiting;
  $('zoteroPreview').disabled=waiting;
}
async function connectZotero(){
  try{
    $('zoteroConnectionState').textContent='正在读取…';
    zoteroWaiting('正在连接 Zotero 并读取收藏夹…',true);
    const res=await api('/api/zotero/collections',zoteroPayload());
    zoteroCollections=res.collections;
    $('zoteroCollection').innerHTML='<option value="">请选择收藏夹</option>'+res.collections.map(c=>`<option value="${escape(c.key)}">${escape(c.path)}</option>`).join('');
    if(config?.config?.zotero_collection_key && res.collections.some(c=>c.key===config.config.zotero_collection_key))
      $('zoteroCollection').value=config.config.zotero_collection_key;
    if(res.backend==='sqlite' && res.database_path && !$('zoteroPath').value) $('zoteroPath').value=res.database_path;
    zoteroConnected=true;zoteroPreviewReady=false;
    $('zoteroConnectionState').textContent=`已连接 · ${res.count} 个收藏夹 · ${res.backend==='api'?'本地 API':'只读数据库'}`;
    notice('已读取 Zotero 收藏夹');
  }catch(e){zoteroConnected=false;$('zoteroConnectionState').textContent='连接失败';notice(e.message,true)}
  finally{zoteroWaiting('',false)}
}
async function previewZotero(){
  if(!$('zoteroCollection').value){notice('请选择一个收藏夹',true);return}
  try{
    zoteroWaiting('正在读取参考论文、提炼检索词…',true);
    const out=await api('/api/zotero/preview',{...zoteroPayload(),keyword_limit:10});
    $('zoteroKeywords').value=out.keywords.join(', ');
    $('zoteroCollectionCount').textContent=`参考文献 ${out.count} 篇`;
    $('zoteroReferencesMeta').textContent=out.collection;
    $('zoteroReferences').innerHTML=out.references.map((p,i)=>`<div class="zotero-ref-row"><span class="zotero-ref-index">${String(i+1).padStart(2,'0')}</span><div><strong>${escape(p.title)}</strong><small>${escape(p.date||'')} ${p.has_abstract?' · 含摘要':''} ${p.tags?.length?' · '+escape(p.tags.join('、')):''}</small></div></div>`).join('')||'<div class="empty">暂无可分析文献。</div>';
    zoteroPreviewReady=true;
    notice(`已提炼 ${out.keywords.length} 个关键词`);
  }catch(e){zoteroPreviewReady=false;notice(e.message,true)}
  finally{zoteroWaiting('',false)}
}
async function applyZotero(run=false){
  if(!$('zoteroCollection').value){notice('请选择收藏夹',true);return}
  const keywords=commaList($('zoteroKeywords').value);
  if(!keywords.length){notice('请保留至少一个关键词',true);return}
  try{
    await api('/api/zotero/apply',{...zoteroPayload(),keywords,refresh_keywords:$('zoteroRefresh').checked,keyword_limit:keywords.length});
    notice('已应用 Zotero 研究方向');
    await updateState();
    $('topicKeywords').value=keywords.join(', ');
    if(run){visible('dashboard');await api('/api/job',{mode:'search'});notice('已启动检索');await updateState()}
  }catch(e){notice(e.message,true)}
}
function renderLibraryGroups(){
  const mode=$('libraryGroup').value;
  const words=commaList($('topicKeywords').value).slice(0,16);
  let html='';
  if(mode==='none')html=libraryPapers.map(p=>renderPaper(p)).join('');
  else{
    const groups=new Map();
    for(const p of libraryPapers){
      let key='其他 / 未分类';
      if(mode==='source')key=p.source||'未知来源';
      if(mode==='category')key=p.category||'未分类';
      if(mode==='year')key=/^\d{4}/.test(p.published||'')?p.published.slice(0,4)+' 年':'年份未知';
      if(mode==='initial')key=/^[a-z]/i.test((p.title||'').trim())?p.title.trim()[0].toUpperCase():'#';
      if(mode==='keyword')key=words.find(w=>(p.title+' '+p.abstract).toLowerCase().includes(w.toLowerCase()))||'其他研究方向';
      if(!groups.has(key))groups.set(key,[]);
      groups.get(key).push(p);
    }
    html=[...groups.entries()].map(([name,papers],index)=>`<details class="paper-group" ${index<2?'open':''}><summary>${escape(name)} <small>${papers.length} 篇 · 本次已加载</small></summary>${papers.map(p=>renderPaper(p)).join('')}</details>`).join('');
  }
  $('libraryList').innerHTML=html||'<div class="empty">没有找到符合条件的论文。</div>';
  document.querySelectorAll('.paper-check').forEach(node=>{node.checked=selectedPapers.has(node.dataset.id)});
  $('selectedCount').textContent=selectedPapers.size?`已选 ${selectedPapers.size} 篇`:'未选择论文';
}
function updateSourceHelp(){
  const names=[...document.querySelectorAll('.source-option:checked')].map(n=>n.value);
  const locked=sourceDefinitions.filter(meta=>names.includes(meta.name)&&meta.access!=='public');
  const target=$('sourceAccessHelp');target.hidden=!locked.length;
  if(!locked.length)return;
  target.innerHTML=locked.map(meta=>`<div><strong>${escape(meta.label)}</strong> · ${escape(meta.note)}<br>${sourceAccess[meta.name]?.key?'已保存 API Key':'未配置 API Key'} · <button class="text-button source-guide-config" type="button">前往授权设置 →</button> <a href="${escape(safeLink(meta.guide_url))}" target="_blank" rel="noopener noreferrer">官方说明 ↗</a></div>`).join('');
}
function refreshProviderLabels(){
  const provider=$('apiProvider').value;
  const info=providerInfo[provider]||{};
  $('providerHint').textContent=info.hint||'请在服务商官方网站申请 API Key。';
  $('providerConsole').href=safeLink(info.console)||'#';
  $('providerConsole').hidden=!info.console;
  const usage=providerUsage[provider]||{};
  $('providerLocalUsage').textContent=`本机成功请求：${usage.requests||0} 次；输入 ${usage.input_tokens||0} Token；输出 ${usage.output_tokens||0} Token。`;
  $('providerBalance').textContent=provider==='DeepSeek'?'可以点击刷新余额；实际用量以 DeepSeek 控制台为准。':'官方账号用量与费用请通过服务商控制台查看。';
  $('providerUsageAll').textContent='本机用量：'+(Object.keys(providerUsage).length?Object.keys(providerUsage).map(name=>`${name} ${providerUsage[name].requests||0} 次`).join(' · '):'暂无请求');
}
async function refreshProviderCatalog(){
  try{
    const res=await api('/api/provider-catalog');
    providerUsage=res.local||{};
    refreshProviderLabels();
    const configured=Object.entries(res.configured||{}).filter(([name,ready])=>ready).map(([name])=>name);
    $('providerConfigured').textContent='已配置的服务商：'+(configured.length?configured.join('、'):'尚无已保存的密钥');
  }catch(e){$('providerConfigured').textContent='服务商状态暂不可读取';}
}
async function refreshProviderUsage(){
  const provider=$('apiProvider').value;
  $('providerBalance').textContent='正在查询…';
  try{
    const data=await api('/api/provider-usage?provider='+encodeURIComponent(provider));
    providerUsage[provider]=data.local||{};
    refreshProviderLabels();
    if(data.remote?.available){
      const text=(data.remote.balances||[]).map(x=>`${x.total_balance} ${x.currency}`).join('、');
      $('providerBalance').textContent='DeepSeek 官方余额：'+(text||'未返回余额信息');
    }else $('providerBalance').textContent=(data.remote?.reason||'请到官方控制台查看用量');
  }catch(e){$('providerBalance').textContent=e.message;notice(e.message,true)}
}
async function loadLibrary(append=false){
  // A new filter starts from page one. Older in-flight replies are ignored.
  if(append && libraryLoading)return;
  const reqId=++libraryRequestId;
  const offset=append?libraryOffset:0;
  libraryLoading=true;
  const q=$('librarySearch').value, mode=$('libraryFilter').value;
  const pars=new URLSearchParams({q,offset:String(offset),limit:'30',sort:$('librarySort').value,scope:$('libraryScope').value});
  if(mode==='starred')pars.set('starred','1');else if(mode!=='all')pars.set('status',mode);
  $('libraryMore').disabled=true;
  try{
    const res=await api('/api/papers?'+pars.toString());
    if(reqId!==libraryRequestId)return;
    libraryTotal=res.total;
    if(!append){libraryPapers=[];selectedPapers.clear()}
    libraryPapers.push(...res.papers);
    renderLibraryGroups();
    libraryOffset=offset+res.papers.length;
    $('libraryMore').hidden=!res.has_more;
    $('libraryPagination').textContent=`已显示 ${libraryOffset} / ${libraryTotal} 篇`;
    $('libraryStatus').textContent=libraryTotal?`找到 ${libraryTotal} 篇论文`:'没有匹配的论文';
    $('selectedCount').textContent=selectedPapers.size?`已选 ${selectedPapers.size} 篇`:'未选择论文';
  }catch(e){if(reqId===libraryRequestId){notice(e.message,true);$('libraryStatus').textContent='读取失败，请重试'}}
  finally{if(reqId===libraryRequestId){libraryLoading=false;$('libraryMore').disabled=false}}
}
async function loadRecent(){
  try{const out=await api('/api/papers?limit=4&sort=recent');
    $('recentList').innerHTML=out.papers.length?out.papers.map(p=>renderPaper(p,true)).join(''):
      '<div class="empty">还没有论文记录。设置研究主题并运行一次检索即可开始。</div>';
  }catch{}
}
async function loadReports(){try{const res=await api('/api/reports');$('reportList').innerHTML=res.reports.length?res.reports.map(p=>`<div class="report-row"><div><div class="report-file">▱　${escape(p.name)}</div><div class="report-info">${escape(p.modified)}　·　${(p.bytes/1024).toFixed(0)} KB</div></div><div class="report-actions">${p.name.endsWith('.md')?`<button class="button secondary compact preview-md" data-name="${escape(p.name)}" type="button">预览</button>`:''}<a class="report-download" href="/download/${encodeURIComponent(p.name)}">下载 ↘</a></div></div>`).join(''):'<div class="empty">暂无报告，完成一次检索后自动生成。</div>';$('latestReport').hidden=!res.reports.some(p=>p.name==='近期文献汇总.docx');$('latestMarkdownPreview').disabled=!res.reports.some(p=>p.name==='近期文献汇总.md');}catch(e){notice(e.message,true)}}
async function downloadPapers(ids){
  ids=[...new Set(ids)].filter(Boolean);
  if(!ids.length){notice('请先勾选论文',true);return}
  if(ids.length>20){notice(`一次最多下载 20 篇，当前选中 ${ids.length} 篇。请减少选择。`,true);return}
  try{await api('/api/job',{mode:'download',ids});notice(`已提交 ${ids.length} 篇 PDF 下载任务`);updateState()}
  catch(err){notice(err.message,true)}
}
async function loadDownloads(){
  const filter=$('downloadFilter').value;
  try{
    const data=await api('/api/downloads?status='+encodeURIComponent(filter));
    $('downloadList').innerHTML=data.items.length?data.items.map(p=>`<div class="report-row"><div><div class="report-file">${escape(p.title)}</div><div class="report-info">${escape(p.source)} · ${escape(p.download_status==='downloaded'?'已下载':p.download_status==='failed'?'下载失败':'暂无可下载 PDF')} ${p.download_error?' · '+escape(p.download_error):''}</div></div><div class="download-actions">${p.download_status==='downloaded'?`<a class="report-download" href="/api/pdf/${encodeURIComponent(p.paper_id)}" target="_blank" rel="noopener">打开 PDF ↗</a>`:''}${p.download_status==='failed'&&p.download_url?`<button class="button secondary compact retry-download" type="button" data-id="${escape(p.paper_id)}">重新下载</button>`:''}</div></div>`).join(''):
      '<div class="empty">暂无符合当前筛选条件的下载记录。</div>';
  }catch(err){notice(err.message,true)}
}
function formValues(){return {keywords:commaList($('topicKeywords').value),categories:commaList($('categories').value),schedule_time:$('taskTime').value,ai_enabled:$('aiEnabled').checked,api_provider:$('apiProvider').value,api_base_url:$('apiBase').value.trim(),api_model:$('apiModel').value.trim(),llm_input_usd_per_million:Number($('llmInputRate').value),llm_output_usd_per_million:Number($('llmOutputRate').value),api_key:$('apiKey').value.trim(),lookback_days:Number($('lookbackDays').value),max_results:Number($('quickMaxResults').value||$('maxResults').value),report_limit:Number($('reportLimit').value),ai_limit:Number($('aiLimit').value),sort_mode:$('quickSort').value,sources:[...document.querySelectorAll('.source-option:checked')].map(x=>x.value),auto_download:$('autoDownload').checked,download_limit:Number($('downloadLimit').value),max_pdf_mb:Number($('maxPdfMb').value),max_queries_per_source:Number($('queryBudget').value),source_query_timeout_seconds:Number($('sourceQueryTimeout').value),source_total_timeout_seconds:Number($('sourceTotalTimeout').value),research_goal:$('aiResearchGoal').value.trim(),ai_rerank_enabled:$('aiRerankEnabled').checked,ai_rerank_limit:Number($('aiRerankLimit').value),schedule_all_topics:$('scheduleAllTopics').checked,update_check_enabled:$('updateEnabled').checked,auto_update_on_startup:$('autoUpdateStartup').checked,update_check_interval_hours:Number($('updateInterval').value)};}
async function save(silent=false){const result=await api('/api/config',formValues());$('apiKey').value='';$('keyState').textContent=result.api_key_saved?'已保存 API Key':'尚未保存 API Key';if(!silent)notice('设置已保存');return result}
async function job(mode){try{await save(true);await api('/api/job',{mode});notice(({search:'正在检索并生成报告',export:'正在从历史记录导出',test_api:'正在测试 API',export_all:'正在导出全部文献索引',export_experiments:'正在导出已核验实验结果',synthesis:'正在生成跨论文实验比较',search_all:'正在按主题依次检索'})[mode]);updateState()}catch(err){notice(err.message,true)}}
function setSettings(data){config=data;providers=data.api_presets||providers;const c=data.config;if(!$('topicKeywords').matches(':focus'))$('topicKeywords').value=(c.keywords||[]).join(', ');$('categories').value=(c.categories||[]).join(', ');$('taskTime').value=c.schedule_time||'08:30';$('lookbackDays').value=c.lookback_days;$('maxResults').value=c.max_results;$('quickMaxResults').value=c.max_results;$('reportLimit').value=c.report_limit;$('aiLimit').value=c.ai_limit;$('aiRerankEnabled').checked=!!c.ai_rerank_enabled;$('aiRerankLimit').value=c.ai_rerank_limit||15;$('scheduleAllTopics').checked=!!c.schedule_all_topics;if(!$('aiResearchGoal').matches(':focus'))$('aiResearchGoal').value=c.research_goal||'';$('queryBudget').value=c.max_queries_per_source||8;$('sourceQueryTimeout').value=c.source_query_timeout_seconds||10;$('sourceTotalTimeout').value=c.source_total_timeout_seconds||75;$('updateEnabled').checked=c.update_check_enabled!==false;$('autoUpdateStartup').checked=c.auto_update_on_startup!==false;$('updateInterval').value=c.update_check_interval_hours||24;$('apiProvider').value=c.api_provider;$('apiBase').value=c.api_base_url;$('apiModel').value=c.api_model;$('llmInputRate').value=c.llm_input_usd_per_million||0;$('llmOutputRate').value=c.llm_output_usd_per_million||0;$('sortMode').value=c.sort_mode||'relevance';$('quickSort').value=c.sort_mode||'relevance';sourceDefinitions=data.sources||[];sourceAccess=data.source_access||{};providerInfo=data.provider_info||{};providerUsage=data.api_usage_local||{};
 const providerSelect=$('apiProvider');providerSelect.innerHTML='';
 Object.keys(providers).forEach(name=>{const opt=document.createElement('option');opt.value=name;opt.textContent=name;providerSelect.appendChild(opt)});
 providerSelect.value=c.api_provider;
 $('sourceOptions').innerHTML='<span>文献来源</span>'+sourceDefinitions.map(x=>`<label title="${escape(x.note||'')}"><input class="source-option" type="checkbox" value="${escape(x.name)}" ${(c.sources||['arxiv']).includes(x.name)?'checked':''} /> ${escape(x.label)} ${x.access!=='public'?'· 需授权':x.name==='openalex_semantic'?'· 可能消耗额度':'· 开放'}</label>`).join('');
 $('springerState').textContent=sourceAccess.springer?.key?'已保存':'未配置';
 $('elsevierState').textContent=sourceAccess.elsevier?.key?'已保存':'未配置';
 updateSourceHelp();refreshProviderLabels();$('autoDownload').checked=!!c.auto_download;$('downloadLimit').value=c.download_limit||3;$('maxPdfMb').value=c.max_pdf_mb||30;$('aiEnabled').checked=!!c.ai_enabled;$('keyState').textContent=c.api_key_saved?'已保存 API Key':'尚未保存 API Key';
 $('zoteroMode').value=c.zotero_mode||'auto';$('zoteroPath').value=c.zotero_db_path||'';
 $('zoteroChildren').checked=c.zotero_include_children!==false;
 $('zoteroRefresh').checked=!!c.zotero_refresh_keywords;
 renderTopics(data.topics_info);$('appVersion').textContent='Paper-Radar v'+(data.version||'');if(c.zotero_enabled){$('zoteroConnectionState').textContent='已选择收藏夹 · 下次检索时读取文献';
   $('zoteroKeywords').value=(c.keywords||[]).join(', ');}
}
function updateSchedule(s){schedule=s||{state:'checking'};const on=s.installed===true;const pill=$('schedulePill');pill.textContent=on?'运行中':s.state==='unsupported'?'当前系统暂不支持':s.state==='checking'?'正在检查…':s.state==='legacy_installed'?'旧版任务待迁移':'未开启';pill.className='pill '+(on?'success':'muted');$('installTask').textContent=on?'更新运行时间':'开启后台更新';$('installTask').disabled=s.state==='unsupported';$('triggerTask').disabled=!on;$('removeTask').disabled=!on;$('scheduleDetails').textContent=on?`下次运行：${s.next_run||'时间待确认'}。关闭界面不影响系统定时执行。`:s.state==='unsupported'?'后台计划任务目前支持 Windows，其他系统可使用 cron 或 launchd。':s.state==='legacy_installed'?'检测到旧版定时任务，点击“开启后台更新”后将迁移为 Paper-Radar 任务。':'Windows 计划任务会在指定时间启动，执行完成后退出；无需保持网页打开。';$('scheduleLast').textContent=s.last_run?`上次执行：${s.last_run}　·　系统结果码：${s.last_result??'未知'}`:'尚无后台运行记录';}
function renderSourceDiagnostics(rows){
  const details=$('sourceDiagnosticsDetails');const target=$('sourceDiagnostics');
  if(!details||!target)return;
  if(!Array.isArray(rows)||!rows.length){details.hidden=true;target.replaceChildren();return}
  details.hidden=false;target.replaceChildren();
  for(const entry of rows){
    const row=document.createElement('div');row.className='source-diagnostic-row';
    const name=document.createElement('strong');name.textContent=entry.name||'文献来源';
    row.appendChild(name);
    const status=document.createElement('span');
    status.textContent=entry.ok?(entry.partial?'部分可用':'正常完成'):
      (entry.timed_out?'连接超时，已跳过':'无法访问，已跳过');
    status.className='source-diagnostic-state '+(entry.ok?'source-ok':'source-failed');
    row.appendChild(status);
    const info=document.createElement('small');
    info.textContent=`获取 ${entry.count||0} 篇 · ${entry.seconds||0} 秒`;
    row.appendChild(info);target.appendChild(row);
  }
}
function showTaskProgress(data){
  const active=data.runtime?.busy||data.last_run?.state==='running';
  const run=data.runtime?.busy?data.runtime:data.last_run;
  const progress=run?.progress||{};
  const hasState=active||['failed','partial'].includes(data.last_run?.state);
  $('taskCenter').hidden=!hasState;
  if(!hasState)return;
  const current=Number.isFinite(Number(progress.percent))?Math.max(0,Math.min(100,Number(progress.percent))):0;
  $('taskProgressBar').value=current;
  $('taskPercent').textContent=(data.last_run?.state==='failed'&&!active?'失败 · ':'')+current+'%';
  $('taskStage').textContent=active?(progress.stage||'任务正在运行'):data.last_run?.state==='failed'?'上次任务失败':'上次任务已完成';
  const counts=Number.isFinite(progress.current)&&Number.isFinite(progress.total)&&progress.total>0?` · ${progress.current}/${progress.total}`:'';
  const duration=Number.isFinite(progress.elapsed_seconds)?` · 已用时 ${progress.elapsed_seconds} 秒`:'';
  $('taskMeta').textContent=(progress.detail||data.runtime?.phase||'暂无详细进度')+counts+duration;
  renderSourceDiagnostics(data.runtime?.result?.source_results||data.last_run?.result?.source_results||[]);
  const lines=data.runtime?.busy?(data.runtime.logs||[]):[];
  $('taskLiveLog').textContent=lines.length?lines.join('\n'):'如需查看历史日志，请在“设置 → 运行记录”中选择手动或后台任务。';
}
async function loadTaskView(){
  const target=$('taskViewHistory');if(!target)return;
  target.textContent='正在加载本主题的任务历史…';
  try{
    const data=await api('/api/history?scope=topic');const runs=(data.runs||[]).slice(0,15);
    target.replaceChildren();
    if(!runs.length){const empty=document.createElement('p');empty.className='hint';
      empty.textContent='尚无任务记录。回到工作台启动一次检索即可。';target.appendChild(empty);return;}
    for(const run of runs){
      const row=document.createElement('div');row.className='local-task-row';
      const heading=document.createElement('strong');
      heading.textContent=(run.finished_at||run.started_at||'未知时间').slice(0,16).replace('T',' ')+
        ' · '+(run.state==='success'?'已完成':run.state==='partial'?'部分完成':run.state==='failed'?'执行失败':run.state||'未知');
      row.appendChild(heading);
      const detail=document.createElement('p');detail.className='hint';
      detail.textContent=run.state==='failed'?(run.error||'未知异常'):
        '发现 '+Number((run.summary||{}).retrieved||0)+' 篇 · 新增 '+Number((run.summary||{}).new||0)+' 篇';
      row.appendChild(detail);target.appendChild(row);
    }
  }catch(error){target.textContent='任务记录暂时不可用：'+error.message;}
}
async function loadAgents(){
  const label=$('agentOverview'),grid=$('agentGrid'),other=$('agentOnDemand'),research=$('agentResearch');
  if(!label||!grid||!other)return;
  label.textContent='读取独立 Agent 与运行状态…';
  try{
    const data=await api('/api/agents');
    const last=(data.recent_runs||[]).find(r=>(r.steps||[]).some(s=>s.agent==='discovery'));
    const researchLast=(data.recent_runs||[]).find(r=>(r.steps||[]).some(s=>s.agent==='experiment'));
    const states=new Map((last?.steps||[]).map(s=>[s.agent,s]));
    const tokens=Number(last?.api_usage?.input_tokens||0)+Number(last?.api_usage?.output_tokens||0);
    const price=last?.estimated_cost_usd===undefined?'':` · 估算 $${Number(last.estimated_cost_usd).toFixed(4)}`;
    label.textContent=`${(data.pipeline||[]).length} 个流程 Agent · ${(data.on_demand||[]).length} 个按需能力 · ${last?('最近执行：'+last.state):'尚无执行记录'}${tokens?' · Token '+tokens.toLocaleString():''}${price}`;

    grid.innerHTML=(data.pipeline||[]).map((a,i)=>{
      const run=states.get(a.name),status=run?.state||'pending';
      const stateLabel=({success:'完成',skipped:'已跳过',failed:'失败',skipped_dependency:'依赖未完成',pending:'待运行'})[status]||status;
      const elapsed=run?` · ${Math.max(0,Number(run.elapsed_ms||0))} ms`:'';
      const tries=Number(run?.attempts||0)>1?` · 重试 ${Number(run.attempts)-1} 次`:'';
      const budget=run?.budget_exceeded?' · 超出时间预算':'';
      const usage=run?.api_usage?.requests?` · ${Number(run.api_usage.input_tokens||0)+Number(run.api_usage.output_tokens||0)} Token`:'';

      return `<article class="agent-item"><div><span class="agent-index">${i+1}</span><strong>${escape(a.title)}</strong><span class="minor">v${escape(a.version)}</span></div><p>${escape(a.purpose)}</p><div class="agent-meta">${escape(a.kind==='llm_assisted'?'可选模型':'本地逻辑')} · ${escape(stateLabel)}${escape(elapsed)}${escape(tries)}${escape(budget)}${escape(usage)}</div></article>`;
    }).join('');
    research.innerHTML=(data.research_pipeline||[]).map((a,i)=>{const run=researchLast?.steps?.find(s=>s.agent===a.name);return `<article class="agent-item"><div><span class="agent-index">${i+1}</span><strong>${escape(a.title)}</strong></div><p>${escape(a.purpose)}</p><div class="agent-meta">${escape(a.sends_to_cloud?'用户确认后调用模型':'本地证据处理')} · ${escape(run?.state||'尚未执行')}${run?' · '+Number(run.elapsed_ms||0)+' ms':''}</div></article>`;}).join('');
    const synth=$('agentSynthesis');
    if(synth) synth.innerHTML=(data.synthesis_pipeline||[]).map((a,i)=>`<article class="agent-item"><div><span class="agent-index">${i+1}</span><strong>${escape(a.title)}</strong></div><p>${escape(a.purpose)}</p></article>`).join('');
    other.innerHTML=(data.on_demand||[]).map(a=>`<div>${escape(a.title)} · ${escape(a.invocation)} · ${escape(a.entrypoint)}</div>`).join('');
  }catch(error){label.textContent='Agent 状态读取失败：'+error.message;grid.textContent='';other.textContent='';research.textContent='';}
}

async function updateState(first=false){if(loading)return;loading=true;try{const data=await api('/api/state');$('statArchived').textContent=data.stats.archived;$('firstRunGuide').hidden=Number(data.stats.archived)>0;$('statMatched').textContent=data.stats.matched;const success=['success','partial'].includes(data.last_run?.state) && Number(data.stats.matched)>0;$('afterSearch').hidden=!success;$('afterSearchText').textContent=success?`当前报告包含 ${data.stats.matched} 篇论文。先读研究综述，再打开论文详目。`:'';$('statDownloaded').textContent=data.stats.downloaded||0;$('navDownloadCount').textContent=data.stats.downloaded||0;$('navLibraryCount').textContent=data.stats.archived;const last=data.last_run;
if($('taskViewStatus')){
  $('taskViewStatus').textContent=data.runtime?.busy?'正在执行科研任务':
    last.state==='success'?'最近任务已完成':last.state==='partial'?'最近任务部分完成':
    last.state==='failed'?'最近任务执行失败':'当前没有运行任务';
  $('taskViewDetail').textContent=data.runtime?.busy?(data.runtime.phase||'正在执行，请查看页面顶部进度'):
    last.state==='failed'?String(last.error||'请查看任务记录'):
    '任务记录会在成功、失败或部分完成时保存到本地。';
}
if(last.state==='success'||last.state==='partial'){$('statRun').textContent=last.state==='partial'?'部分完成':'运行成功';$('statRunDetail').textContent=(last.finished_at||last.timestamp||'').slice(0,16).replace('T',' ')}else if(last.state==='failed'){$('statRun').textContent='运行失败';$('statRunDetail').textContent=String(last.error||'请查看运行记录').slice(0,50)}else if(last.state==='running'){$('statRun').textContent='正在更新';$('statRunDetail').textContent='检索任务正在执行'}else{$('statRun').textContent='尚未运行';$('statRunDetail').textContent='完成后自动显示'}showTaskProgress(data);renderUpdate(data.update);renderTopics(data.topics_info);const busy=data.runtime.busy;$('runSearch').disabled=busy;$('runAllTopics').disabled=busy;$('activeTopic').disabled=busy;$('runExport').disabled=busy;$('exportReports').disabled=busy;$('testApi').disabled=busy;$('downloadSelected').disabled=busy;$('downloadStarred').disabled=busy;$('exportAll').disabled=busy;$('exportExperiments').disabled=busy;$('generateSynthesis').disabled=busy;$('progressLine').hidden=!busy;$('progressText').textContent=data.runtime.phase||'正在处理…';if($('logSource').value==='manual'&&busy)$('logBox').textContent=(data.runtime.logs||[]).join('\n')||'正在执行任务…';updateSchedule(data.scheduler);if(first)setSettings(data);if(jobWasBusy&&!busy){refreshHistory();loadTaskView();loadRecent();loadLibrary();loadReports();loadDownloads();if(last.state==='success'||last.state==='partial')notice(last.state==='partial'?'部分主题未完成，请检查运行记录':'检索或导出已完成');else if(last.state==='failed')notice('执行失败，请查看运行记录',true)}jobWasBusy=busy;}catch(err){if(first)notice('本地服务连接失败：'+err.message,true)}finally{loading=false}}
const hook = (id, handler) => $(id).addEventListener('click',handler);
async function scheduleAction(action){try{if(action==='install')await save(true);const res=await api('/api/schedule',{action});updateSchedule(res);notice(({install:'后台更新已设置',remove:'后台更新已关闭',run:'任务已启动'})[action])}catch(err){notice(err.message,true)}}
window.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>visible(button.dataset.view)));
  document.querySelectorAll('[data-preset]').forEach(button=>button.addEventListener('click',()=>{$('topicKeywords').value=button.dataset.preset;notice('研究主题已填写，请点击检索')}));
  hook('latestMarkdownPreview',()=>showMarkdownPreview());
  hook('aiPlanRequest',planResearchKeywords);
  hook('aiPlanApply',applyResearchPlan);
  $('activeTopic').addEventListener('change',e=>topicAction('switch',e.target.value));
  hook('createTopic',()=>{const name=prompt('新研究主题名称（不同主题分别保存检索参数）：');if(name!==null&&name.trim())topicAction('create','',name.trim())});
  hook('renameTopic',()=>{const id=$('activeTopic').value;const name=prompt('修改研究主题名称：',$('activeTopic').selectedOptions[0]?.textContent||'');if(name!==null&&name.trim())topicAction('rename',id,name.trim())});
  hook('deleteTopic',()=>{const id=$('activeTopic').value;if(id==='default')return;const label=$('activeTopic').selectedOptions[0]?.textContent||id;if(confirm(`删除研究主题“${label}”？该操作不会删除历史报告、论文或 PDF。`))topicAction('delete',id)});
  hook('dismissUpdate',async()=>{try{await api('/api/update/dismiss',{tag_name:latestUpdateTag});await updateState();notice('已关闭当前版本的提醒，新版本发布后仍会提醒')}catch(e){notice(e.message,true)}});
  hook('checkUpdate',async()=>{try{const out=await api('/api/update/check',{});notice(out.started?'已在后台检查公开 Release':'已有更新检查正在运行');renderUpdate(out.status)}catch(e){notice(e.message,true)}});
  hook('saveUpdateSettings',async()=>{try{await save();await updateState();notice('更新检查设置已保存')}catch(e){notice(e.message,true)}});

  $('aiResearchGoal').addEventListener('input',()=>{
    proposedPlan=null;$('aiPlanResult').hidden=true;$('aiPlanStatus').textContent='';
  });
  $('reportList').addEventListener('click',e=>{const b=e.target.closest('.preview-md');if(b)showMarkdownPreview(b.dataset.name)});
  hook('fulltextClose',()=>{$('fulltextDialog').close();activeFulltextId='';});
  hook('fulltextIndex',()=>fulltextTask('index_fulltext'));
  hook('fulltextAnalyze',()=>fulltextTask('analyze_fulltext'));
  hook('fulltextExperiments',()=>fulltextTask('analyze_experiments'));
  hook('fulltextSearch',searchFulltext);
  hook('fulltextCopy',copyFulltextCard);
  $('fulltextSearchInput').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();searchFulltext()}});
  $('fulltextDialog').addEventListener('click',e=>{if(e.target===$('fulltextDialog')){$('fulltextDialog').close();activeFulltextId=''}});
  $('fulltextDialog').addEventListener('close',()=>{activeFulltextId='';fulltextData=null});
  for(const id of ['fulltextClaims','fulltextExperimentsResults','fulltextMatches']){
    $(id).addEventListener('click',event=>{
      const link=event.target.closest('a[href^="/api/pdf/"]');
      if(!link||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
      event.preventDefault();
      $('fulltextPdfViewer').src=link.getAttribute('href');
    });
  }
  hook('markdownClose',()=>$('markdownDialog').close());
  $('markdownDialog').addEventListener('click',e=>{if(e.target===$('markdownDialog'))$('markdownDialog').close()});
  hook('markdownCopy',async()=>{try{await navigator.clipboard.writeText(markdownRaw);notice('Markdown 已复制')}catch{notice('复制失败，可下载原文件',true)}});
  hook('refreshAgents',loadAgents);
  hook('runSearch',()=>job('search'));hook('runAllTopics',()=>job('search_all'));hook('refreshDownloads',loadDownloads);hook('saveDownloadSettings',async()=>{try{await save()}catch(e){notice(e.message,true)}});hook('runExport',()=>job('export'));hook('exportReports',()=>job('export'));hook('exportAll',()=>job('export_all'));hook('exportExperiments',()=>job('export_experiments'));hook('generateSynthesis',()=>job('synthesis'));hook('testApi',()=>job('test_api'));hook('saveSettings',async()=>{try{await save()}catch(e){notice(e.message,true)}});
  hook('libraryMore',()=>loadLibrary(true));hook('downloadFilter',loadDownloads);hook('installTask',()=>scheduleAction('install'));hook('removeTask',()=>scheduleAction('remove'));hook('triggerTask',()=>scheduleAction('run'));
  hook('removeKey',async()=>{if(!confirm('删除当前服务商保存的 API Key？'))return;try{await api('/api/key/remove',{});$('keyState').textContent='尚未保存 API Key';notice('API Key 已删除')}catch(e){notice(e.message,true)}});
  hook('openFolder',async()=>{try{await api('/api/folder',{});}catch(e){notice(e.message,true)}});
  async function refreshHistory(){try{const info=await api('/api/history?scope=topic');const rows=info.runs||[];$('historyList').innerHTML=rows.length?rows.slice(0,10).map(r=>{const x=r.summary||{};const label=r.state==='success'?'完成':'失败';return `<div class="history-row"><span>${escape((r.finished_at||'').slice(0,16).replace('T',' '))}</span><strong>${escape(r.topic_id)} · ${label}</strong><span>${r.state==='success'?`收录 ${Number(x.retrieved||0)} · 新增 ${Number(x.new||0)}`:escape(r.error||'执行失败')}</span></div>`}).join(''):'<div class="empty">暂无当前主题的运行记录</div>'}catch(err){$('historyList').textContent=err.message}}
  async function refreshLogs(){try{const selected=$('logSource').value;let info=await api('/api/logs?source='+selected);$('logBox').textContent=info[selected]||'暂无日志。'}catch(e){notice(e.message,true)}}
  hook('refreshLogs',refreshLogs);hook('taskViewRefresh',loadTaskView);hook('refreshHistory',refreshHistory);refreshHistory();$('logSource').addEventListener('change',refreshLogs);hook('shutdown',async()=>{if(!confirm('关闭本地工作台？系统定时任务仍会保留。'))return;await api('/api/shutdown',{});notice('本地工作台已退出，可以关闭此页面。')});
  $('apiProvider').addEventListener('change',()=>{const preset=providers[$('apiProvider').value];if(preset){$('apiBase').value=preset[0];$('apiModel').value=preset[1]}$('apiKey').value='';$('keyState').textContent='切换服务商后请保存或填写新密钥';refreshProviderLabels()});
  $('libraryScope').addEventListener('change',()=>{selectedPapers.clear();loadLibrary();loadRecent()});$('libraryGroup').addEventListener('change',renderLibraryGroups);$('librarySort').addEventListener('change',()=>loadLibrary());
  $('quickMaxResults').addEventListener('input',()=>{$('maxResults').value=$('quickMaxResults').value});$('maxResults').addEventListener('input',()=>{$('quickMaxResults').value=$('maxResults').value});$('quickSort').addEventListener('change',()=>{$('sortMode').value=$('quickSort').value});$('sortMode').addEventListener('change',()=>{$('quickSort').value=$('sortMode').value});
  $('sourceOptions').addEventListener('change',()=>{updateSourceHelp();const checked=[...document.querySelectorAll('.source-option:checked')].map(n=>n.value);const missing=sourceDefinitions.filter(x=>checked.includes(x.name)&&x.access!=='public'&&!sourceAccess[x.name]?.key);if(missing.length)notice('已选择需授权来源：'+missing.map(x=>x.label).join('、')+'。请先在设置中保存授权 API Key。',true);});$('sourceAccessHelp').addEventListener('click',e=>{if(e.target.closest('.source-guide-config')){visible('settings');$('publisherPanel').scrollIntoView({behavior:'smooth'});}});
  hook('refreshProviderUsage',refreshProviderUsage);
  async function savePublisher(name,fields){try{for(const [field,id] of fields){const val=$(id).value.trim();if(val){await api('/api/source-credential',{source:name,field,value:val});$(id).value='';}}const res=await api('/api/state');sourceAccess=res.source_access||{};updateSourceHelp();$('springerState').textContent=sourceAccess.springer?.key?'已保存':'未配置';$('elsevierState').textContent=sourceAccess.elsevier?.key?'已保存':'未配置';notice('授权信息已保存到系统凭据管理器')}catch(e){notice(e.message,true)}}
  hook('saveSpringer',()=>savePublisher('springer',[['key','springerKey']]));hook('saveElsevier',()=>savePublisher('elsevier',[['key','elsevierKey'],['insttoken','elsevierToken']]));
  $('librarySearch').addEventListener('input',()=>{clearTimeout(window.searchTimer);window.searchTimer=setTimeout(loadLibrary,250)});$('libraryFilter').addEventListener('change',loadLibrary);
  $('libraryList').addEventListener('click',async e=>{const evidenceBtn=e.target.closest('button.fulltext-open');if(evidenceBtn){await openFulltext(evidenceBtn.dataset.id);return;}const feedbackBtn=e.target.closest('button[data-feedback]');if(feedbackBtn){try{await api('/api/paper',{id:feedbackBtn.dataset.id,feedback:Number(feedbackBtn.dataset.feedback)});await loadLibrary();notice('已保存当前主题的相关性反馈')}catch(err){notice(err.message,true)}return;}const pdf=e.target.closest('.pdf-button');if(pdf){await downloadPapers([pdf.dataset.id]);return;}const target=e.target.closest('button[data-action="star"]');if(!target)return;try{await api('/api/paper',{id:target.dataset.id,starred:target.dataset.next==='true'});await loadLibrary()}catch(e){notice(e.message,true)}});
  $('libraryList').addEventListener('change',async e=>{if(e.target.matches('.paper-check')){if(e.target.checked)selectedPapers.add(e.target.dataset.id);else selectedPapers.delete(e.target.dataset.id);$('selectedCount').textContent=selectedPapers.size?`已选 ${selectedPapers.size} 篇`:'未选择论文';return;}const target=e.target.closest('.read-select');if(!target)return;try{await api('/api/paper',{id:target.dataset.id,status:target.value});notice('阅读状态已更新')}catch(e){notice(e.message,true)}});
  $('downloadList').addEventListener('click',e=>{const el=e.target.closest('button.retry-download');if(el)downloadPapers([el.dataset.id])});
  hook('downloadSelected',()=>downloadPapers([...selectedPapers]));
  hook('downloadStarred',async()=>{try{const data=await api('/api/papers?scope=topic&starred=1&limit=21');if(data.total>20){notice(`共有 ${data.total} 篇星标论文。每次最多下载 20 篇，请先筛选。`,true);return}await downloadPapers(data.papers.map(p=>p.paper_id))}catch(e){notice(e.message,true)}});
  document.querySelectorAll('[data-focus]').forEach(button=>button.addEventListener('click',()=>{$(button.dataset.focus).focus();$(button.dataset.focus).scrollIntoView({block:'center',behavior:'smooth'})}));
  hook('themeToggle' ,()=>{const dark=document.documentElement.dataset.theme==='dark';document.documentElement.dataset.theme=dark?'light':'dark';try{localStorage.setItem('lt-theme',dark?'light':'dark')}catch{}});
  try{if(localStorage.getItem('lt-theme')==='dark')document.documentElement.dataset.theme='dark'}catch{}
  $('clock').textContent=new Date().toLocaleDateString('zh-CN');updateState(true).then(loadRecent);setInterval(()=>updateState(),2300);
});

// Zotero profile actions. These controls are available on the dedicated Zotero page.
$('zoteroConnect').addEventListener('click',connectZotero);
$('zoteroCollection').addEventListener('change',()=>{zoteroPreviewReady=false;$('zoteroCollectionCount').textContent='';$('zoteroKeywords').value='';$('zoteroReferencesMeta').textContent='请选择收藏夹';$('zoteroReferences').innerHTML='<div class="empty">点击分析后显示参考文献。</div>';});
$('zoteroPreview').addEventListener('click',previewZotero);
$('zoteroApply').addEventListener('click',()=>applyZotero(false));
$('zoteroApplyRun').addEventListener('click',()=>applyZotero(true));
$('zoteroPick').addEventListener('click',async()=>{
  try {const data=await api('/api/zotero/pick-database',{});
    if(data.path){$('zoteroPath').value=data.path;$('zoteroMode').value='sqlite';await connectZotero()}
  }catch(e){notice(e.message,true)}
});
$('zoteroDisable').addEventListener('click',async()=>{
  try{await api('/api/zotero/disable',{});notice('已停止使用 Zotero 收藏夹');await updateState()}
  catch(e){notice(e.message,true)}
});

for(const id of ['zoteroMode','zoteroPath','zoteroChildren']){
  $(id).addEventListener(id==='zoteroPath'?'input':'change',()=>{
    zoteroPreviewReady=false;
    $('zoteroKeywords').value='';
    $('zoteroCollectionCount').textContent='';
    $('zoteroReferences').innerHTML='<div class="empty">连接并分析收藏夹后显示文献。</div>';
    if(id!=='zoteroChildren'){$('zoteroCollection').innerHTML='<option value="">请重新连接 Zotero</option>';zoteroConnected=false;}
  });
}
