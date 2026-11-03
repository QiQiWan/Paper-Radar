// Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
'use strict';
const TOKEN = document.querySelector('meta[name="local-token"]').content;
let config = null, providers = {}, jobWasBusy = false, loading = false, schedule = null;
let zoteroCollections = [], zoteroConnected = false, zoteroPreviewReady = false;
let libraryOffset = 0, libraryTotal = 0, libraryLoading = false, libraryRequestId = 0;
let libraryPapers = [], selectedPapers = new Set(), sourceDefinitions = [], sourceAccess = {}, providerInfo = {}, providerUsage = {};
let markdownRaw = '', activeReport = '近期文献汇总.md';
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

function notice(text, isError=false){const el=$('toast'); el.textContent=text; el.className='visible'+(isError?' error':'');clearTimeout(notice.timer);notice.timer=setTimeout(()=>el.className='',3900)}
async function api(url, body=null){let opts={cache:'no-store'};if(body!==null){opts={method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':TOKEN},body:JSON.stringify(body)}}const res=await fetch(url,opts);let data;try{data=await res.json()}catch{throw new Error('服务连接异常')}if(!res.ok)throw new Error(data.error||'操作失败');return data}
const visible = name => {document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id===name));document.querySelectorAll('.nav-link').forEach(x=>x.classList.toggle('active',x.dataset.view===name));$('breadcrumbLabel').textContent=({dashboard:'工作台',library:'论文库',downloads:'PDF 下载',reports:'研究报告',settings:'设置',zotero:'Zotero 研究方向'})[name];if(name==='library')loadLibrary();if(name==='reports')loadReports();if(name==='downloads')loadDownloads();if(name==='settings')refreshProviderCatalog();window.scrollTo({top:0,behavior:'smooth'})};
function renderPaper(p,compact=false){
  const full=p.summary||p.abstract||'';
  const excerpt=full.slice(0,compact?100:175)+(full.length>(compact?100:175)?'…':'');
  const starred=!!p.is_starred;
  const noAbstract=!String(p.abstract||'').trim();
  const status=noAbstract?'<span class="tag">仅题录 · 待核查</span>':p.summary?'<span class="tag ai">有中文速览</span>':'<span class="tag">含原始摘要</span>';
  return `<article class="paper-item"><a class="paper-title" target="_blank" rel="noopener noreferrer" href="${escape(safeLink(p.url))}">${escape(p.title)}</a>
    <div class="paper-meta">${escape((p.published||'').slice(0,10)||'日期待核对')} · ${escape(p.source||'未知来源')} · ${escape(p.venue||p.category||'未分类')}</div>
    ${excerpt?`<p class="paper-summary">${escape(excerpt)}</p>`:'<p class="paper-summary">原始来源暂无摘要，建议打开论文主页核查。</p>'}
    <div class="paper-tags">${status}${p.download_status==='downloaded'?'<span class="tag ai">PDF 已保存</span>':''}${starred?'<span class="tag">★ 已标记</span>':''}</div>
    ${compact?'':`<div class="paper-actions"><label class="select-paper"><input type="checkbox" class="paper-check" data-id="${escape(p.paper_id)}" /> 选择</label><button type="button" class="subtle-button ${starred?'selected':''}" data-action="star" data-id="${escape(p.paper_id)}" data-next="${starred?'false':'true'}">${starred?'★ 取消星标':'☆ 星标'}</button><select class="select read-select" style="width:116px;padding:5px 7px;font-size:11px" data-id="${escape(p.paper_id)}"><option value="unread" ${p.reading_status==='unread'?'selected':''}>未阅读</option><option value="reading" ${p.reading_status==='reading'?'selected':''}>阅读中</option><option value="done" ${p.reading_status==='done'?'selected':''}>已读完</option></select>${p.local_file&&p.download_status==='downloaded'?`<a class="subtle-button" href="/api/pdf/${encodeURIComponent(p.paper_id)}" target="_blank" rel="noopener">打开 PDF</a>`:p.download_url?`<button type="button" class="subtle-button pdf-button" data-id="${escape(p.paper_id)}">下载 PDF</button>`:''}</div><details class="paper-detail"><summary>展开原始摘要与中文速览</summary>${p.summary?`<p><strong>中文速览（需核对）</strong>：${escape(p.summary)}</p>`:''}<p><strong>原始摘要</strong>：${escape(p.abstract||'未提供摘要，需查看原文。')}</p></details>`}
  </article>`;
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
  const pars=new URLSearchParams({q,offset:String(offset),limit:'30',sort:$('librarySort').value});
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
function formValues(){return {keywords:commaList($('topicKeywords').value),categories:commaList($('categories').value),schedule_time:$('taskTime').value,ai_enabled:$('aiEnabled').checked,api_provider:$('apiProvider').value,api_base_url:$('apiBase').value.trim(),api_model:$('apiModel').value.trim(),api_key:$('apiKey').value.trim(),lookback_days:Number($('lookbackDays').value),max_results:Number($('quickMaxResults').value||$('maxResults').value),report_limit:Number($('reportLimit').value),ai_limit:Number($('aiLimit').value),sort_mode:$('quickSort').value,sources:[...document.querySelectorAll('.source-option:checked')].map(x=>x.value),auto_download:$('autoDownload').checked,download_limit:Number($('downloadLimit').value),max_pdf_mb:Number($('maxPdfMb').value)};}
async function save(silent=false){const result=await api('/api/config',formValues());$('apiKey').value='';$('keyState').textContent=result.api_key_saved?'已保存 API Key':'尚未保存 API Key';if(!silent)notice('设置已保存');return result}
async function job(mode){try{await save(true);await api('/api/job',{mode});notice(({search:'正在检索并生成报告',export:'正在从历史记录导出',test_api:'正在测试 API',export_all:'正在导出全部文献索引'})[mode]);updateState()}catch(err){notice(err.message,true)}}
function setSettings(data){config=data;providers=data.api_presets||providers;const c=data.config;if(!$('topicKeywords').matches(':focus'))$('topicKeywords').value=(c.keywords||[]).join(', ');$('categories').value=(c.categories||[]).join(', ');$('taskTime').value=c.schedule_time||'08:30';$('lookbackDays').value=c.lookback_days;$('maxResults').value=c.max_results;$('quickMaxResults').value=c.max_results;$('reportLimit').value=c.report_limit;$('aiLimit').value=c.ai_limit;$('apiProvider').value=c.api_provider;$('apiBase').value=c.api_base_url;$('apiModel').value=c.api_model;$('sortMode').value=c.sort_mode||'relevance';$('quickSort').value=c.sort_mode||'relevance';sourceDefinitions=data.sources||[];sourceAccess=data.source_access||{};providerInfo=data.provider_info||{};providerUsage=data.api_usage_local||{};
 const providerSelect=$('apiProvider');providerSelect.innerHTML='';
 Object.keys(providers).forEach(name=>{const opt=document.createElement('option');opt.value=name;opt.textContent=name;providerSelect.appendChild(opt)});
 providerSelect.value=c.api_provider;
 $('sourceOptions').innerHTML='<span>文献来源</span>'+sourceDefinitions.map(x=>`<label title="${escape(x.note||'')}"><input class="source-option" type="checkbox" value="${escape(x.name)}" ${(c.sources||['arxiv']).includes(x.name)?'checked':''} /> ${escape(x.label)} ${x.access!=='public'?'· 需授权':'· 免费'}</label>`).join('');
 $('springerState').textContent=sourceAccess.springer?.key?'已保存':'未配置';
 $('elsevierState').textContent=sourceAccess.elsevier?.key?'已保存':'未配置';
 updateSourceHelp();refreshProviderLabels();$('autoDownload').checked=!!c.auto_download;$('downloadLimit').value=c.download_limit||3;$('maxPdfMb').value=c.max_pdf_mb||30;$('aiEnabled').checked=!!c.ai_enabled;$('keyState').textContent=c.api_key_saved?'已保存 API Key':'尚未保存 API Key';
 $('zoteroMode').value=c.zotero_mode||'auto';$('zoteroPath').value=c.zotero_db_path||'';
 $('zoteroChildren').checked=c.zotero_include_children!==false;
 $('zoteroRefresh').checked=!!c.zotero_refresh_keywords;
 if(c.zotero_enabled){$('zoteroConnectionState').textContent='已选择收藏夹 · 下次检索时读取文献';
   $('zoteroKeywords').value=(c.keywords||[]).join(', ');}
}
function updateSchedule(s){schedule=s||{state:'checking'};const on=s.installed===true;const pill=$('schedulePill');pill.textContent=on?'运行中':s.state==='unsupported'?'当前系统暂不支持':s.state==='checking'?'正在检查…':s.state==='legacy_installed'?'旧版任务待迁移':'未开启';pill.className='pill '+(on?'success':'muted');$('installTask').textContent=on?'更新运行时间':'开启后台更新';$('installTask').disabled=s.state==='unsupported';$('triggerTask').disabled=!on;$('removeTask').disabled=!on;$('scheduleDetails').textContent=on?`下次运行：${s.next_run||'时间待确认'}。关闭界面不影响系统定时执行。`:s.state==='unsupported'?'后台计划任务目前支持 Windows，其他系统可使用 cron 或 launchd。':s.state==='legacy_installed'?'检测到旧版定时任务，点击“开启后台更新”后将迁移为 Paper-Radar 任务。':'Windows 计划任务会在指定时间启动，执行完成后退出；无需保持网页打开。';$('scheduleLast').textContent=s.last_run?`上次执行：${s.last_run}　·　系统结果码：${s.last_result??'未知'}`:'尚无后台运行记录';}
function showTaskProgress(data){
  const active=data.runtime?.busy||data.last_run?.state==='running';
  const run=data.runtime?.busy?data.runtime:data.last_run;
  const progress=run?.progress||{};
  const hasState=active||data.last_run?.state==='failed';
  $('taskCenter').hidden=!hasState;
  if(!hasState)return;
  const current=Number.isFinite(Number(progress.percent))?Math.max(0,Math.min(100,Number(progress.percent))):0;
  $('taskProgressBar').value=current;
  $('taskPercent').textContent=(data.last_run?.state==='failed'&&!active?'失败 · ':'')+current+'%';
  $('taskStage').textContent=active?(progress.stage||'任务正在运行'):data.last_run?.state==='failed'?'上次任务失败':'上次任务已完成';
  const counts=Number.isFinite(progress.current)&&Number.isFinite(progress.total)&&progress.total>0?` · ${progress.current}/${progress.total}`:'';
  const duration=Number.isFinite(progress.elapsed_seconds)?` · 已用时 ${progress.elapsed_seconds} 秒`:'';
  $('taskMeta').textContent=(progress.detail||data.runtime?.phase||'暂无详细进度')+counts+duration;
  const lines=data.runtime?.busy?(data.runtime.logs||[]):[];
  $('taskLiveLog').textContent=lines.length?lines.join('\n'):'如需查看历史日志，请在“设置 → 运行记录”中选择手动或后台任务。';
}
async function updateState(first=false){if(loading)return;loading=true;try{const data=await api('/api/state');$('statArchived').textContent=data.stats.archived;$('firstRunGuide').hidden=Number(data.stats.archived)>0;$('statMatched').textContent=data.stats.matched;const success=data.last_run?.state==='success' && Number(data.stats.matched)>0;$('afterSearch').hidden=!success;$('afterSearchText').textContent=success?`当前报告包含 ${data.stats.matched} 篇论文。先读研究综述，再打开论文详目。`:'';$('statDownloaded').textContent=data.stats.downloaded||0;$('navDownloadCount').textContent=data.stats.downloaded||0;$('navLibraryCount').textContent=data.stats.archived;const last=data.last_run;if(last.state==='success'){$('statRun').textContent='运行成功';$('statRunDetail').textContent=(last.finished_at||last.timestamp||'').slice(0,16).replace('T',' ')}else if(last.state==='failed'){$('statRun').textContent='运行失败';$('statRunDetail').textContent=String(last.error||'请查看运行记录').slice(0,50)}else if(last.state==='running'){$('statRun').textContent='正在更新';$('statRunDetail').textContent='检索任务正在执行'}else{$('statRun').textContent='尚未运行';$('statRunDetail').textContent='完成后自动显示'}showTaskProgress(data);const busy=data.runtime.busy;$('runSearch').disabled=busy;$('runExport').disabled=busy;$('exportReports').disabled=busy;$('testApi').disabled=busy;$('downloadSelected').disabled=busy;$('downloadStarred').disabled=busy;$('exportAll').disabled=busy;$('progressLine').hidden=!busy;$('progressText').textContent=data.runtime.phase||'正在处理…';if($('logSource').value==='manual'&&busy)$('logBox').textContent=(data.runtime.logs||[]).join('\n')||'正在执行任务…';updateSchedule(data.scheduler);if(first)setSettings(data);if(jobWasBusy&&!busy){loadRecent();loadLibrary();loadReports();loadDownloads();if(last.state==='success')notice('检索或导出已完成');else if(last.state==='failed')notice('执行失败，请查看运行记录',true)}jobWasBusy=busy;}catch(err){if(first)notice('本地服务连接失败：'+err.message,true)}finally{loading=false}}
const hook = (id, handler) => $(id).addEventListener('click',handler);
async function scheduleAction(action){try{if(action==='install')await save(true);const res=await api('/api/schedule',{action});updateSchedule(res);notice(({install:'后台更新已设置',remove:'后台更新已关闭',run:'任务已启动'})[action])}catch(err){notice(err.message,true)}}
window.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>visible(button.dataset.view)));
  document.querySelectorAll('[data-preset]').forEach(button=>button.addEventListener('click',()=>{$('topicKeywords').value=button.dataset.preset;notice('研究主题已填写，请点击检索')}));
  hook('latestMarkdownPreview',()=>showMarkdownPreview());
  $('reportList').addEventListener('click',e=>{const b=e.target.closest('.preview-md');if(b)showMarkdownPreview(b.dataset.name)});
  hook('markdownClose',()=>$('markdownDialog').close());
  $('markdownDialog').addEventListener('click',e=>{if(e.target===$('markdownDialog'))$('markdownDialog').close()});
  hook('markdownCopy',async()=>{try{await navigator.clipboard.writeText(markdownRaw);notice('Markdown 已复制')}catch{notice('复制失败，可下载原文件',true)}});
  hook('runSearch',()=>job('search'));hook('refreshDownloads',loadDownloads);hook('saveDownloadSettings',async()=>{try{await save()}catch(e){notice(e.message,true)}});hook('runExport',()=>job('export'));hook('exportReports',()=>job('export'));hook('exportAll',()=>job('export_all'));hook('testApi',()=>job('test_api'));hook('saveSettings',async()=>{try{await save()}catch(e){notice(e.message,true)}});
  hook('libraryMore',()=>loadLibrary(true));hook('downloadFilter',loadDownloads);hook('installTask',()=>scheduleAction('install'));hook('removeTask',()=>scheduleAction('remove'));hook('triggerTask',()=>scheduleAction('run'));
  hook('removeKey',async()=>{if(!confirm('删除当前服务商保存的 API Key？'))return;try{await api('/api/key/remove',{});$('keyState').textContent='尚未保存 API Key';notice('API Key 已删除')}catch(e){notice(e.message,true)}});
  hook('openFolder',async()=>{try{await api('/api/folder',{});}catch(e){notice(e.message,true)}});
  async function refreshLogs(){try{const selected=$('logSource').value;let info=await api('/api/logs?source='+selected);$('logBox').textContent=info[selected]||'暂无日志。'}catch(e){notice(e.message,true)}}
  hook('refreshLogs',refreshLogs);$('logSource').addEventListener('change',refreshLogs);hook('shutdown',async()=>{if(!confirm('关闭本地工作台？系统定时任务仍会保留。'))return;await api('/api/shutdown',{});notice('本地工作台已退出，可以关闭此页面。')});
  $('apiProvider').addEventListener('change',()=>{const preset=providers[$('apiProvider').value];if(preset){$('apiBase').value=preset[0];$('apiModel').value=preset[1]}$('apiKey').value='';$('keyState').textContent='切换服务商后请保存或填写新密钥';refreshProviderLabels()});
  $('libraryGroup').addEventListener('change',renderLibraryGroups);$('librarySort').addEventListener('change',()=>loadLibrary());
  $('quickMaxResults').addEventListener('input',()=>{$('maxResults').value=$('quickMaxResults').value});$('maxResults').addEventListener('input',()=>{$('quickMaxResults').value=$('maxResults').value});$('quickSort').addEventListener('change',()=>{$('sortMode').value=$('quickSort').value});$('sortMode').addEventListener('change',()=>{$('quickSort').value=$('sortMode').value});
  $('sourceOptions').addEventListener('change',()=>{updateSourceHelp();const checked=[...document.querySelectorAll('.source-option:checked')].map(n=>n.value);const missing=sourceDefinitions.filter(x=>checked.includes(x.name)&&x.access!=='public'&&!sourceAccess[x.name]?.key);if(missing.length)notice('已选择需授权来源：'+missing.map(x=>x.label).join('、')+'。请先在设置中保存授权 API Key。',true);});$('sourceAccessHelp').addEventListener('click',e=>{if(e.target.closest('.source-guide-config')){visible('settings');$('publisherPanel').scrollIntoView({behavior:'smooth'});}});
  hook('refreshProviderUsage',refreshProviderUsage);
  async function savePublisher(name,fields){try{for(const [field,id] of fields){const val=$(id).value.trim();if(val){await api('/api/source-credential',{source:name,field,value:val});$(id).value='';}}const res=await api('/api/state');sourceAccess=res.source_access||{};updateSourceHelp();$('springerState').textContent=sourceAccess.springer?.key?'已保存':'未配置';$('elsevierState').textContent=sourceAccess.elsevier?.key?'已保存':'未配置';notice('授权信息已保存到系统凭据管理器')}catch(e){notice(e.message,true)}}
  hook('saveSpringer',()=>savePublisher('springer',[['key','springerKey']]));hook('saveElsevier',()=>savePublisher('elsevier',[['key','elsevierKey'],['insttoken','elsevierToken']]));
  $('librarySearch').addEventListener('input',()=>{clearTimeout(window.searchTimer);window.searchTimer=setTimeout(loadLibrary,250)});$('libraryFilter').addEventListener('change',loadLibrary);
  $('libraryList').addEventListener('click',async e=>{const pdf=e.target.closest('.pdf-button');if(pdf){await downloadPapers([pdf.dataset.id]);return;}const target=e.target.closest('button[data-action="star"]');if(!target)return;try{await api('/api/paper',{id:target.dataset.id,starred:target.dataset.next==='true'});await loadLibrary()}catch(e){notice(e.message,true)}});
  $('libraryList').addEventListener('change',async e=>{if(e.target.matches('.paper-check')){if(e.target.checked)selectedPapers.add(e.target.dataset.id);else selectedPapers.delete(e.target.dataset.id);$('selectedCount').textContent=selectedPapers.size?`已选 ${selectedPapers.size} 篇`:'未选择论文';return;}const target=e.target.closest('.read-select');if(!target)return;try{await api('/api/paper',{id:target.dataset.id,status:target.value});notice('阅读状态已更新')}catch(e){notice(e.message,true)}});
  $('downloadList').addEventListener('click',e=>{const el=e.target.closest('button.retry-download');if(el)downloadPapers([el.dataset.id])});
  hook('downloadSelected',()=>downloadPapers([...selectedPapers]));
  hook('downloadStarred',async()=>{try{const data=await api('/api/papers?starred=1&limit=21');if(data.total>20){notice(`共有 ${data.total} 篇星标论文。每次最多下载 20 篇，请先筛选。`,true);return}await downloadPapers(data.papers.map(p=>p.paper_id))}catch(e){notice(e.message,true)}});
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
