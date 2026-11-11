// Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
'use strict';
const TOKEN = document.querySelector('meta[name="local-token"]').content;
// Only report error category and source location. Never send exception text,
// model prompts, user input or page URLs to the diagnostics endpoint.
function reportLocalClientError(kind, filename='', line=0){
  const allowed=new Set(['app.js','reading_links.js','trace_workbench.js','inquiry_workbench.js','research_program.js','ui_state.js']);
  const source=String(filename||'').split(/[\/]/).pop().split('?')[0];
  fetch('/api/diagnostics/client',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':TOKEN},
    body:JSON.stringify({kind,source:allowed.has(source)?source:'unknown',line:Number.isSafeInteger(line)?Math.max(0,Math.min(100000,line)):0})}).catch(()=>{});
}
window.addEventListener('error',event=>reportLocalClientError('javascript_error',event.filename,event.lineno));
window.addEventListener('unhandledrejection',()=>reportLocalClientError('promise_error'));
let config = null, providers = {}, jobWasBusy = false, loading = false, schedule = null;
let aiReminderPaused = false;
let bootstrapReady = false, storageReady = false;
let zoteroCollections = [], zoteroConnected = false, zoteroPreviewReady = false;
let libraryOffset = 0, libraryTotal = 0, libraryLoading = false, libraryRequestId = 0;
let libraryPapers = [], selectedPapers = new Set(), sourceDefinitions = [], sourceAccess = {}, providerInfo = {}, providerUsage = {};
const localReadingDrafts=new Map(); // unsaved content remains in memory across library reloads
let markdownRaw = '', activeReport = '近期文献汇总.md';
let proposedPlan = null, activeTopicCache = "", latestUpdateTag = "";
let activeFulltextId = '', fulltextData = null, fulltextWaiting = false;
let localPaperTrace=null,localTopicTrace=null,localInquiry=null;
let localRestoring = false, localLinkTimer = null, localPaperPage = 1, localRouteNonce = 0, localReportNonce = 0;
const ignoredLocalClose={fulltext:0,markdown:0};
const R = window.PRReading;
const UI = window.PRUI;
const $ = id => document.getElementById(id);
document.querySelectorAll('.settings-shortcuts a').forEach(a=>a.addEventListener('click',e=>{
  const target=document.querySelector(a.getAttribute('href'));if(!target)return;
  e.preventDefault();for(const block of document.querySelectorAll('#settings>.settings-accordion>.settings-section'))block.open=block===target;
  target.scrollIntoView({behavior:'smooth',block:'start'});
}));
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
  const out=[],lines=String(source||'').replace(/\r\n?/g,'\n').split('\n');
  let list='',code=[];let fenced=false;
  const close=()=>{if(list){out.push(`</${list}>`);list=''}};
  const cells=line=>line.trim().replace(/^\|/,'').replace(/\|$/,'').split(/(?<!\\)\|/).map(x=>x.trim().replace(/\\\|/g,'|'));
  for(let i=0;i<lines.length;i++){
    const line=lines[i].trim();
    if(line.startsWith('```')){
      close();if(fenced){out.push(`<pre><code>${escape(code.join('\n'))}</code></pre>`);code=[]}
      fenced=!fenced;continue;
    }
    if(fenced){code.push(lines[i]);continue}
    if(!line){close();continue}
    if(line.includes('|') && i+1<lines.length){
      const head=cells(line),rule=cells(lines[i+1]);
      if(head.length>1 && head.length===rule.length && rule.every(x=>/^:?-{3,}:?$/.test(x))){
        close();out.push('<div class="report-table-scroll"><table class="guide-markdown-table"><thead><tr>'+head.map(c=>`<th>${formatInline(c)}</th>`).join('')+'</tr></thead><tbody>');
        i+=2;
        for(;i<lines.length && lines[i].includes('|');i++){
          const row=cells(lines[i]);if(!row.some(Boolean))break;
          out.push('<tr>'+Array.from({length:head.length},(_,j)=>`<td>${formatInline(row[j]||'')}</td>`).join('')+'</tr>');
        }
        out.push('</tbody></table></div>');i--;continue;
      }
    }
    const heading=line.match(/^(#{1,5})\s+(.+)$/);
    if(heading){close();out.push(`<h${heading[1].length}>${formatInline(heading[2])}</h${heading[1].length}>`);continue}
    if(/^---+$/.test(line)){close();out.push('<hr>');continue}
    const bullet=line.match(/^[-*]\s+(.+)$/),numbered=line.match(/^\d+[.)]\s+(.+)$/);
    if(bullet||numbered){const type=bullet?'ul':'ol';if(list!==type){close();out.push(`<${type}>`);list=type}out.push(`<li>${formatInline((bullet||numbered)[1])}</li>`);continue}
    close();out.push(`<p>${formatInline(line)}</p>`);
  }
  close();if(code.length)out.push(`<pre><code>${escape(code.join('\n'))}</code></pre>`);
  return out.join('\n');
}

async function showMarkdownPreview(name='近期文献汇总.md',{push=true,pos=0}={}){
  const dialog=$('markdownDialog');activeReport=name;
  const reportRequest=++localReportNonce;
  $('markdownTitle').textContent=name;
  $('markdownRendered').textContent='正在加载 Markdown…';
  $('markdownDownload').href='/download/'+encodeURIComponent(name);
  $('markdownSize').textContent='本地报告 · 阅读模式';
  if(!dialog.open)dialog.showModal();
  if(push)syncLocalLink({push:true});
  try{
    const response=await api('/api/report-preview?name='+encodeURIComponent(name));
    if(reportRequest!==localReportNonce || activeReport!==name || !dialog.open)return;
    markdownRaw=response.content||'';
    $('markdownRendered').innerHTML=renderMarkdownSafe(markdownRaw);
    markGuideStep('report');
    R.restoreProgress($('markdownRendered'),pos);
    syncLocalLink();
    $('markdownSize').textContent=`${markdownRaw.length.toLocaleString('zh-CN')} 字符 · 本地预览`;
  }catch(error){if(reportRequest!==localReportNonce||!dialog.open)return;markdownRaw='';$('markdownRendered').textContent='预览失败：'+error.message;}
}

async function planResearchKeywords(){
  const button=$('aiPlanRequest'),status=$('aiPlanStatus');
  const goal=$('aiResearchGoal').value.trim();
  if(goal.length<12){notice('请先具体描述研究对象、方法和想跟踪的问题',true);return}
  if(!getLocalModelState().ready){notice('AI 已默认开启，但还缺少模型密钥或数据授权。请按提示完成连接。',true);openAiSetup();return}
  proposedPlan=null;$('aiPlanResult').hidden=true;button.disabled=true;
  status.textContent='正在生成检索词…';
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
  }catch(error){status.textContent=error.status===424?'模型连接失败；可检查配置或手动填写检索词':'检索词生成未完成；可直接填写关键词检索';if(!(error.status===424 || error.status>=500))notice(error.message,true);if(error.status===424&&$('runtimeErrorBanner')&&!$('runtimeErrorBanner').hidden)$('runtimeErrorBanner').scrollIntoView({behavior:'smooth',block:'start'})}
  finally{button.disabled=false}
}

function applyResearchPlan(){
  if(!proposedPlan){notice('请先生成检索方案',true);return}
  const words=commaList($('aiPlanWords').value).slice(0,20);
  if(!words.length){notice('请保留至少一个检索词',true);return}
  $('topicKeywords').value=words.join(', ');
  $('aiPlannerDisclosure').open=false;
  notice('已应用检索词。点击“开始检索”后执行。');
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
  $('updateBannerText').textContent='当前版本 '+(status.current_version||'未知')+'。确认安装后会等待任务结束，安全关闭本地服务、备份升级并重启。';
  $('installUpdate').hidden=!available || status.can_install===false;
  $('installUpdateSettings').hidden=!available || status.can_install===false;
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
async function confirmAndInstall(){
  if(!latestUpdateTag){notice('请先检查正式更新版本',true);return}
  const msg='将安装 '+latestUpdateTag+'。本地服务会等待当前科研任务结束，随后停止并重启。\n请先保存其他应用中的工作，更新期间网页会暂时断开。确定继续吗？';
  if(!window.confirm(msg))return;
  try{
    const out=await api('/api/update/install',{confirm:true,tag_name:latestUpdateTag});
    try{sessionStorage.setItem('paper-radar-updating-from',config?.version||document.title)}catch{}
    notice(out.message||'已开始安全更新');
    $('handoffStatusText').textContent='已确认升级，正在等待科研任务完成。更新过程中页面可能暂时无法访问。';
    $('installUpdate').disabled=true;$('installUpdateSettings').disabled=true;
  }catch(err){notice(err.message,true)}
}
async function pollHandoff(){
  let pending=null;try{pending=sessionStorage.getItem('paper-radar-updating-from')}catch{}
  if(!pending)return;
  try{
    const info=await api('/api/update/handoff');
    const label=info.message||'正在重新连接服务…';
    $('handoffStatusText').textContent=label;
    if(info.state==='success' || info.state==='failed'){
      try{sessionStorage.removeItem('paper-radar-updating-from')}catch{}
      $('installUpdate').disabled=false;$('installUpdateSettings').disabled=false;
      if(info.state==='success'){location.reload();return}
      notice('自动更新未完成：'+label,true);
    }
  }catch{/* Service may be restarting after the confirmed shutdown. */}
}
const welcomeSteps=[
  ['快速开始研究','选择研究主题，输入关键词，点击“开始检索”。'],
  ['管理论文','进入论文库，打开摘要或 PDF，把感兴趣的论文标星，并设置阅读状态。'],
  ['模型功能（可选）','普通检索无需模型。中文速览等功能可在“设置”中启用。']
];
const GUIDE_STEPS=['topic','search','read','analysis','report'];
function guideStorageKey(){return 'paper-radar-v147-guide:'+($('activeTopic')?.value||'default')}
function readGuideSteps(){try{return JSON.parse(localStorage.getItem(guideStorageKey())||'{}')||{}}catch{return {}}}
function markGuideStep(name){
  if(!GUIDE_STEPS.includes(name))return;
  const state=readGuideSteps();if(state[name])return;
  state[name]=true;
  try{localStorage.setItem(guideStorageKey(),JSON.stringify(state))}catch{}
  refreshHomeGuide();
}
function refreshHomeGuide(remote=null){
  const saved=readGuideSteps();const info=remote?.topics_info;
  const active=info?.active_topic_id||$('activeTopic')?.value||'default';
  const topics=info?.topics||[];
  const hasTopic=active!=='default'||topics.some(x=>x.id===active&&x.name&&x.name!=='默认研究主题');
  const done={...saved,topic:hasTopic||!!saved.topic};
  let complete=0;
  for(const step of GUIDE_STEPS){
    const element=document.querySelector('[data-guide-step="'+step+'"]');if(!element)continue;
    const ok=!!done[step];element.dataset.done=String(ok);element.querySelector('.guide-complete').textContent=ok?'✓':'○';
    if(ok)complete++;
  }
  if($('homeGuideCount'))$('homeGuideCount').textContent=complete+' / '+GUIDE_STEPS.length;
}
function renderHome(data){
  const stats=data.stats||{};const active=data.topics_info?.active_topic_id||'default';
  const topic=(data.topics_info?.topics||[]).find(item=>item.id===active);
  $('homeTopicName').textContent='当前研究主题：'+(topic?.name||'默认研究主题');
  $('homeStatPapers').textContent=stats.unavailable?'—':Number(stats.archived||0).toLocaleString('zh-CN');
  $('homeStatPdf').textContent=stats.unavailable?'—':Number(stats.downloaded||0).toLocaleString('zh-CN');
  const run=data.last_run||{};const last=UI.taskSummary(run);
  $('homeLastStatus').textContent=data.runtime?.busy?'正在执行':last.label;
  $('homeTaskDetail').textContent=(data.runtime?.busy?(data.runtime?.phase||'正在运行…'):
    (last.detail||'暂无任务记录。'));
  const havePapers=Number(stats.archived||0)>0;
  const next=havePapers?{text:'已经有论文记录，可以继续筛选与阅读，或对选中的论文进行研究分析。',view:'library',button:'查看我的文献'}:
    {text:'先选择研究主题，然后输入关键词查找论文。公开检索不需要模型密钥。',view:'dashboard',button:'查找相关论文'};
  $('homeNextHint').textContent=next.text;
  $('homeNextAction').dataset.view=next.view;$('homeNextAction').textContent=next.button;
  $('homeTaskAction').dataset.view=data.runtime?.busy?'tasks':'dashboard';
  $('homeTaskAction').textContent=data.runtime?.busy?'查看任务进度':'查找新论文';
  if(['search','search_all'].includes(run.mode)&&['success','partial','empty'].includes(last.kind))markGuideStep('search');
  refreshHomeGuide(data);
}
let welcomeStep=0,welcomeSeen=false;
function showWelcome(){
  const dlg=$('welcomeWizard');if(welcomeSeen||!dlg||typeof dlg.showModal!=='function')return;
  welcomeSeen=true;welcomeStep=0;renderWelcome();dlg.showModal();
}
function renderWelcome(){
  $('welcomeStep').textContent=`第 ${welcomeStep+1} / ${welcomeSteps.length} 步`;
  $('welcomeTitle').textContent=welcomeSteps[welcomeStep][0];
  $('welcomeText').textContent=welcomeSteps[welcomeStep][1];
  $('welcomeNext').textContent=welcomeStep===welcomeSteps.length-1?'开始使用':'下一步';
}
async function finishWelcome(){
  try{await api('/api/onboarding/complete',{})}catch{notice('引导状态保存失败，下次启动可能再次显示',true)}
  $('welcomeWizard').close();
}
async function topicAction(action,topicId='',name=''){
  try{
    const data=await api('/api/topics',{action,topic_id:topicId,name});
    if((action==='switch'||action==='delete') && $('fulltextDialog').open){
      ignoredLocalClose.fulltext++;$('fulltextDialog').close();activeFulltextId='';
    }
    if((action==='switch'||action==='delete') && $('markdownDialog').open){
      ignoredLocalClose.markdown++;$('markdownDialog').close();activeReport='';
    }
    renderTopics(data);await updateState(true);refreshHomeGuide();syncLocalLink({push:true});await loadRecent();
    if(document.querySelector('#library.view.active')){selectedPapers.clear();await loadLibrary();}
    if(document.querySelector('#reports.view.active'))loadReports();
    notice(({create:'已创建并切换研究主题',switch:'已切换研究主题',rename:'主题名称已更新',delete:'主题已删除，报告历史仍保留'})[action]);
  }catch(e){notice(e.message,true);const latest=await api('/api/topics').catch(()=>null);if(latest)renderTopics(latest)}
}
function notice(text, isError=false){const el=$('toast'); el.textContent=text; el.className='visible'+(isError?' error':'');clearTimeout(notice.timer);notice.timer=setTimeout(()=>el.className='',5900)}
function showLocalConnectionError(message){
  const panel=$('localConnectionRecovery');if(!panel)return;
  panel.hidden=false;$('localConnectionDetail').textContent=message||'请确认本地服务仍在运行，然后重新连接。';
}
function clearLocalConnectionError(){if($('localConnectionRecovery'))$('localConnectionRecovery').hidden=true;}
async function retryLocalConnection(){
  const button=$('retryLocalConnection');if(button.disabled)return;
  button.disabled=true;button.textContent='正在连接…';
  try{
    await loadBootstrap();await updateState(true);await refreshStorageHealth();
    await loadRecent();clearLocalConnectionError();notice('连接已恢复，可以继续操作。');
  }catch(err){showLocalConnectionError(err.message);notice('连接仍未恢复：'+err.message,true)}
  finally{button.disabled=false;button.textContent='重新连接'}
}
async function api(url, body=null){
  const opts=body===null?{cache:'no-store'}:
    {method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':TOKEN},body:JSON.stringify(body)};
  let res;
  try{res=await fetch(url,opts)}catch{throw new Error('无法连接本地服务。请确认 Paper-Radar 仍在运行，然后点击“重新连接”；已保存的数据不会丢失。')}
  let data;try{data=await res.json()}catch{throw new Error('服务响应无法读取。请刷新状态；如刚提交任务，先查看任务记录再决定是否重试。')}
  if(!res.ok){
    const id=typeof data.request_id==='string' && /^[a-f0-9]{12}$/.test(data.request_id)?data.request_id:'';
    const advice=UI.requestMessage(res.status,data.error,'local');
    const message=id && (res.status>=500 || res.status===424 || res.status===422) ? `${advice} 错误编号：${id}`:advice;
    if(res.status>=500 || res.status===424){
      const banner=document.getElementById('runtimeErrorBanner');
      if(banner){
        banner.hidden=false;document.getElementById('runtimeErrorTitle').textContent=res.status===424?'模型连接未完成':'服务运行异常';
        document.getElementById('runtimeErrorMessage').textContent=message;
        document.getElementById('runtimeErrorFix').hidden=res.status!==424;
      }
    }
    const error=new Error(message);error.status=res.status;error.request_id=id;error.code=data.code||'';throw error;
  }
  return data;
}
const LOCAL_ROUTES = new Set(['home','dashboard','library','tasks','following','inquiry','reports','settings','zotero','downloads']);
function localRoute(){
  const part=window.location.pathname.match(/^\/app\/([a-z]+)\/?$/);
  if(part && LOCAL_ROUTES.has(part[1]))return part[1];
  const legacy=window.location.hash.replace(/^#/, '');
  return LOCAL_ROUTES.has(legacy)?legacy:'home';
}
function localLinkState(view=localRoute()){
  const state={topic:$('activeTopic')?.value||''};
  if(view==='library'){
    Object.assign(state,{q:$('librarySearch').value,filter:$('libraryFilter').value,
      sort:$('librarySort').value,scope:$('libraryScope').value,group:$('libraryGroup').value});
    if($('fulltextDialog')?.open && activeFulltextId){state.paper=activeFulltextId;state.page=localPaperPage;}
  }
  if(view==='reports' && $('markdownDialog')?.open && activeReport){
    state.report=activeReport;state.pos=R.progress($('markdownRendered'));
  }
  return state;
}
function syncLocalLink({push=false}={}){
  if(localRestoring)return;
  R.write(R.link('local',localRoute(),localLinkState()),{push});
}
function updateLocalHelp(name=localRoute()){
  $('contextHelpLocal').href=R.help('local',name);
}
function restoreLocalFilters(){
  const p=R.params();
  const controls={q:['librarySearch',''],filter:['libraryFilter','all'],sort:['librarySort','relevance'],
    scope:['libraryScope','topic'],group:['libraryGroup','keyword']};
  for(const [key,[id,defaultValue]] of Object.entries(controls)){
    const value=R.get(key)||defaultValue;
    const input=$(id);
    if(input.tagName==='SELECT' && ![...input.options].some(option=>option.value===value))input.value=defaultValue;
    else input.value=value;
  }
  $('libraryList').querySelectorAll('.paper-item.is-current').forEach(x=>x.classList.remove('is-current'));
  if(p.has('scope')||p.has('group'))document.querySelector('.library-more-filters').open=true;
}
async function restoreLocalRoute(){
  const nonce=++localRouteNonce;
  const page=localRoute();
  const paper=R.get('paper'),report=R.get('report'),pdfPage=Number(R.get('page')||1);
  const position=Number(R.get('pos')||0);
  localRestoring=true;
  const bookmarkedTopic=R.get('topic');
  if(bookmarkedTopic && bookmarkedTopic!==$('activeTopic').value && [...$('activeTopic').options].some(o=>o.value===bookmarkedTopic)){
    try{await api('/api/topics',{action:'switch',topic_id:bookmarkedTopic});await updateState()}
    catch(e){notice('切换研究主题失败：'+e.message,true)}
  }
  if(nonce!==localRouteNonce)return;
  if($('fulltextDialog').open){ignoredLocalClose.fulltext++;$('fulltextDialog').close();}
  if($('markdownDialog').open){ignoredLocalClose.markdown++;$('markdownDialog').close();}
  if(page==='library')restoreLocalFilters();
  visible(page,{push:false,scroll:false});
  localRestoring=false;
  if(nonce!==localRouteNonce)return;
  if(page==='library' && paper){
    await openFulltext(paper,{page:pdfPage,push:false});
  }else if(page==='reports' && report){
    await showMarkdownPreview(report,{push:false,pos:position});
  }
  updateLocalHelp(page);
}
function visible(name, {push=true, scroll=true}={}) {
  if(!LOCAL_ROUTES.has(name))name='home';
  document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id===name));
  document.querySelectorAll('.nav-link').forEach(x=>{
    const active=x.dataset.view===name;x.classList.toggle('active',active);
    if(active)x.setAttribute('aria-current','page');else x.removeAttribute('aria-current');
  });
  if(name!=='library' && $('fulltextDialog').open){ignoredLocalClose.fulltext++;$('fulltextDialog').close();}
  if(name!=='reports' && $('markdownDialog').open){ignoredLocalClose.markdown++;$('markdownDialog').close();}
  if(['zotero','downloads','tasks','following'].includes(name))$('localExtraTools').open=true;
  $('breadcrumbLabel').textContent=({home:'首页',dashboard:'找论文',library:'我的文献',tasks:'任务记录',
    following:'论文更新',inquiry:'研究分析',downloads:'PDF 下载',reports:'我的报告',settings:'设置',zotero:'Zotero 文献导入'})[name];
  if(name==='library')loadLibrary();if(name==='tasks')loadTaskView();
  if(name==='following')loadFollowing();
  if(name==='reports')loadReports();if(name==='inquiry')loadLocalInquiry();if(name==='downloads')loadDownloads();
  if(name==='settings'){refreshProviderCatalog();loadAgents();}
  if(name==='zotero')loadZoteroPaperChoices();
  const target=R.link('local',name,localLinkState(name));
  if(push)R.write(target,{push:true});
  updateLocalHelp(name);
  document.title=({home:'首页',dashboard:'找论文',library:'我的文献',tasks:'任务记录',following:'论文更新',inquiry:'研究分析',reports:'我的报告',settings:'设置',zotero:'Zotero',downloads:'PDF 下载'})[name]+' · Paper-Radar';
  if(scroll)window.scrollTo({top:0,behavior:'smooth'});
}
const localNextActions=[['none','暂不安排'],['read','精读原文'],['replicate','尝试复现'],['cite','准备引用'],['discuss','与同事讨论']];
function renderLocalNotePanel(p){
  const key=($('activeTopic')?.value||'default')+':'+p.paper_id;
  const draft=localReadingDrafts.get(key);
  const action=String(draft?.action??p.next_action??'none');
  const revision=Number(draft?.revision??p.note_revision)||0;
  const options=localNextActions.map(([value,title])=>`<option value="${value}" ${action===value?'selected':''}>${title}</option>`).join('');
  return `<details class="local-reading-note"><summary>个人阅读笔记${p.note?' · 已记录':''}${action!=='none'?' · 有后续行动':''}</summary>
    <div class="local-reading-note-fields"><label>人工阅读笔记<textarea class="input local-note-input" maxlength="1600" rows="3" placeholder="核心观点、局限、与研究课题的关系…">${escape(draft?.note??p.note??'')}</textarea></label>
    <label>下一步行动<select class="select local-note-action">${options}</select></label></div>
    <div class="local-note-footer"><button class="button secondary local-note-save" type="button" data-id="${escape(p.paper_id)}" data-revision="${revision}">保存笔记</button><button class="button tertiary local-note-discard" type="button" ${draft?'':'disabled'}>丢弃本页草稿</button><span class="minor local-note-feedback" role="status">${draft?'有未保存草稿；保存后可导出':'仅保存在当前研究主题；保存后可导出'}</span></div>
  </details>`;
}
function renderPaper(p,compact=false){
  const full=p.summary||p.abstract||'';
  const excerpt=full.slice(0,compact?110:260)+(full.length>(compact?110:260)?'…':'');
  const starred=!!p.is_starred;
  const feedback=Number(p.feedback||0);
  const noAbstract=!String(p.abstract||'').trim();
  const status=noAbstract?'<span class="tag">仅题录 · 待核查</span>':p.summary?'<span class="tag ai">有中文速览</span>':'<span class="tag">含原始摘要</span>';
  return `<article class="paper-item paper-card-v1493"><a class="paper-title" target="_blank" rel="noopener noreferrer" href="${escape(safeLink(p.url))}">${escape(p.title)}</a>
    <div class="paper-meta">${escape((p.published||'').slice(0,10)||'日期待核对')} · ${escape(p.source||'未知来源')} · ${escape(p.venue||p.category||'未分类')}</div>
    ${excerpt?`<p class="paper-summary">${escape(excerpt)}</p>`:'<p class="paper-summary">原始来源暂无摘要，建议打开论文主页核查。</p>'}
    ${p.recommend_reason?`<details class="paper-reason-detail"><summary>查看入选原因</summary><p class="paper-reason"><strong>匹配依据：</strong>${escape(p.recommend_reason)}${p.recommend_evidence?`<span class="minor"> · 摘要相关内容：${escape(p.recommend_evidence)}</span>`:''}</p></details>`:''}
    <div class="paper-tags">${status}${String(p.source||'').includes('openalex_semantic')?'<span class="tag">语义发现 · 待核查</span>':''}${p.download_status==='downloaded'?'<span class="tag ai">PDF 已保存</span>':''}${starred?'<span class="tag">★ 已标记</span>':''}</div>
    ${compact?'':`<div class="paper-actions"><label class="select-paper"><input type="checkbox" class="paper-check" data-id="${escape(p.paper_id)}" /> 选择</label><button type="button" class="paper-primary-action fulltext-open" data-id="${escape(p.paper_id)}">阅读论文</button><button type="button" class="subtle-button ${starred?'selected':''}" data-action="star" data-id="${escape(p.paper_id)}" data-next="${starred?'false':'true'}">${starred?'★ 已收藏':'☆ 收藏'}</button><select class="select read-select" aria-label="阅读状态" data-id="${escape(p.paper_id)}"><option value="unread" ${p.reading_status==='unread'?'selected':''}>未阅读</option><option value="reading" ${p.reading_status==='reading'?'selected':''}>阅读中</option><option value="done" ${p.reading_status==='done'?'selected':''}>已读</option></select><details class="paper-extra-actions"><summary>更多操作</summary><button class="subtle-button feedback-up ${feedback===1?'selected':''}" data-id="${escape(p.paper_id)}" data-feedback="${feedback===1?0:1}" type="button">${feedback===1?'✓ 相关':'相关'}</button><button class="subtle-button feedback-down ${feedback===-1?'selected':''}" data-id="${escape(p.paper_id)}" data-feedback="${feedback===-1?0:-1}" type="button">${feedback===-1?'✓ 不相关':'不相关'}</button>${p.local_file&&p.download_status==='downloaded'?`<a class="subtle-button" href="/api/pdf/${encodeURIComponent(p.paper_id)}" target="_blank" rel="noopener">打开 PDF</a>`:p.download_url?`<button type="button" class="subtle-button pdf-button" data-id="${escape(p.paper_id)}">下载 PDF</button>`:''}</details></div><details class="paper-detail"><summary>查看摘要与阅读要点</summary>${p.summary?`<p><strong>阅读要点（模型生成，需核对）</strong>：${escape(p.summary)}</p>`:''}<p><strong>原始摘要</strong>：${escape(p.abstract||'当前索引未提供摘要，可以尝试从公开论文索引补取。')}</p>${noAbstract?`<button type="button" class="subtle-button abstract-refresh" data-id="${escape(p.paper_id)}">从公开来源补取摘要</button>`:''}</details>${$('libraryScope').value==='topic'?renderLocalNotePanel(p):'<p class="minor">个人笔记与批量操作仅支持当前研究主题</p>'}`}
  </article>`;
}
// v1.12: One local fulltext drawer, opened on demand. PDF text is indexed on
// the user's computer; cloud AI receives only a capped set of selected snippets.
function moveLocalPdfPage(page,{push=true}={}){
  const input=$('fulltextPdfPage');
  const upper=Number(fulltextData?.document?.total_pages)||9999;
  const value=Math.max(1,Math.min(upper,9999,Math.floor(Number(page)||1)));
  localPaperPage=value;
  if(input)input.value=String(value);
  const frame=$('fulltextPdfViewer');
  const url='/api/pdf/'+encodeURIComponent(activeFulltextId)+'#page='+value;
  if(activeFulltextId && frame.getAttribute('src')!==url)frame.src=url;
  if(localPaperTrace&&activeFulltextId)localPaperTrace.updatePage(value);
  syncLocalLink({push});
}
async function openFulltext(id,{page=1,push=true}={}){
  if(!id || id.length>150)return;
  activeFulltextId=id;fulltextData=null;localPaperTrace=null;localPaperPage=Math.max(1,Math.floor(Number(page)||1));
  $('fulltextTitle').textContent='阅读论文';
  $('fulltextState').textContent='读取本地 PDF 索引…';
  $('fulltextClaims').innerHTML='';$('fulltextMatches').innerHTML='';
  $('readerQuestionPanel').hidden=true;$('readerQuestionOutput').replaceChildren();$('readerQuestion').value='';
  $('fulltextSearchInput').value='';
  $('fulltextExperimentsResults').innerHTML='<div class="empty">正在读取实验结构化结果…</div>';
  $('fulltextHighlightPanel').hidden=true;$('fulltextHighlightImage').removeAttribute('src');$('fulltextTableResults').textContent='';
  const dialog=$('fulltextDialog');if(!dialog.open)dialog.showModal();
  moveLocalPdfPage(localPaperPage,{push});
  await refreshFulltext();
}
async function refreshFulltext(){
  if(!activeFulltextId)return;
  const current=activeFulltextId;
  try{
    const info=await api('/api/fulltext/status?paper_id='+encodeURIComponent(current));
    if(current!==activeFulltextId)return;
    fulltextData=info;
    $('fulltextTitle').textContent=info.title||'阅读论文';
    const doc=info.document||{};
    const state={indexed:'全文索引已就绪',partial:'部分页面已索引',no_text:'未提取到文字',stale:'PDF 已变更，请重新解析',cached:'已缓存'}[doc.state]||'尚未建立全文索引';
    $('fulltextState').textContent=info.has_pdf?(doc.state==='indexed'?'PDF 已准备好，可以阅读和查看分析结果。':
      `PDF 已保存，${state}（${Number(doc.indexed_pages||0)} / ${Number(doc.total_pages||0)} 页）`):
      '还没有保存这篇论文的 PDF。可以先下载开放获取版本，或打开论文原始网页。';
    $('fulltextDownload').hidden=!!info.has_pdf;
    $('fulltextDownload').disabled=fulltextWaiting;
    $('fulltextNote').textContent=doc.note||info.note||'';
    $('fulltextPdfPage').max=String(Math.min(9999,Number(doc.total_pages)||9999));
    if(localPaperPage>Number($('fulltextPdfPage').max))moveLocalPdfPage(localPaperPage,{push:false});
    $('fulltextIndex').disabled=!info.has_pdf||fulltextWaiting;
    $('fulltextAnalyze').disabled=!info.indexed||fulltextWaiting;
    $('fulltextCopy').disabled=!(info.claims||[]).length;
    $('fulltextExperiments').disabled=!info.has_pdf||fulltextWaiting;
    const frame=$('fulltextPdfViewer');
    if(info.has_pdf){
      markGuideStep('read');
      const url='/api/pdf/'+encodeURIComponent(current)+'#page='+localPaperPage;
      if(frame.getAttribute('src')!==url)frame.src=url;
      $('fulltextPdfHint').textContent='点击右侧阅读卡片的页码，可同步定位当前 PDF。';
    }else{
      frame.removeAttribute('src');
      $('fulltextPdfHint').textContent='当前论文尚无已保存的 PDF。如没有开放下载链接，请通过学校图书馆或出版社查看原文。';
    }
    const items=info.claims||[];
    const traceRoot=$('localTraceWorkbench');
    if(traceRoot){
      localPaperTrace=window.PRTrace.mount({root:traceRoot,request:(path,method='GET',body)=>
        method==='GET'?api(path):api(path,body||{}),mode:'local',
        paperId:current,page:localPaperPage,sha:doc.pdf_sha256||'',
        onMessage:(m,error)=>{if(error)notice(m,true)},
        onChanged:()=>localTopicTrace?.refresh()});
    }
    await loadExperimentResults(current);
    // Reading a paper must remain useful when no paid model is available.
    // The bibliographic abstract is explicitly distinguished from PDF-grounded claims.
    const entry=libraryPapers.find(p=>p.paper_id===current);
    const abstract=String(entry?.abstract||'').trim().slice(0,5000);
    const bibliographic=abstract?`<article class="evidence-card"><div class="evidence-meta">原始摘要 · 来源题录 · 尚未核对 PDF</div><p>${escape(abstract)}</p></article>`:'';
    $('fulltextClaims').innerHTML=items.length?items.map((c,i)=>`<article class="evidence-card"><div class="evidence-meta">${escape(({method:'方法',experiment:'实验',limitation:'局限'})[c.category]||'证据')} · 第 ${Number(c.page)} 页</div><p>${escape(c.statement)}</p><blockquote>${escape(c.quote)}</blockquote><button class="button secondary compact quote-location" type="button" data-page="${Number(c.page)}" data-quote="${escape(c.quote)}">高亮原文</button>
<a target="_blank" rel="noopener" href="/api/pdf/${encodeURIComponent(current)}#page=${Number(c.page)}">打开 PDF 第 ${Number(c.page)} 页 ↗</a></article>`).join(''):
      bibliographic+'<div class="empty">暂无经过原文逐字校验的 AI 阅读卡片。读取 PDF 后可选择“生成阅读要点”。</div>';
  }catch(error){$('fulltextState').textContent='读取失败：'+error.message;notice(error.message,true)}
}

async function loadExperimentResults(id){
  const root=$('fulltextExperimentsResults');
  if(!root)return;
  try{
    const data=await api('/api/experiments/status?paper_id='+encodeURIComponent(id));
    if(id!==activeFulltextId)return;
    const items=data.findings||[];
    root.innerHTML=items.length?items.map(c=>`<article class="evidence-card"><div class="evidence-meta">${escape(({metric:'定量结果',ablation:'消融实验',efficiency:'效率指标'})[c.kind]||'实验数据')} · 第 ${Number(c.page)} 页</div><p><strong>${escape(c.metric)}</strong>：${escape(c.value)} ${c.dataset?' · 数据集：'+escape(c.dataset):''} ${c.baseline?' · 对照：'+escape(c.baseline):''}</p>${c.setting?`<p class="minor">条件：${escape(c.setting)}</p>`:''}<blockquote>${escape(c.quote)}</blockquote>${c.id?`<button type="button" class="trace-mini local-experiment-trace" data-id="${Number(c.id)}" data-page="${Number(c.page)}" data-quote="${escape(c.quote)}">关联这条实验原文</button>`:''}<a target="_blank" rel="noopener" href="/api/pdf/${encodeURIComponent(id)}#page=${Number(c.page)}">核对 PDF 第 ${Number(c.page)} 页 ↗</a></article>`).join(''):`<div class="empty">${data.status==='stale'?'PDF 内容已变更，请重新索引并提取。':'暂无经过原文核验的结构化实验结果。'}</div>`;
  }catch(error){if(id===activeFulltextId)root.textContent='实验信息读取失败：'+error.message}
}

async function fulltextTask(mode){
  if(!activeFulltextId||fulltextWaiting)return;
  const pid=activeFulltextId;
  if(mode!=='index_fulltext'&&!$('llmConsent').checked){notice('请先在设置 → 模型服务中确认原文发送授权。',true);return}
  if(mode==='analyze_experiments'&&!confirm('将本地 PDF 中最多16个选定原文片段发送至当前模型服务商，提取实验结果并产生可能的调用费用。需要人工核对原文表格。继续？'))return;
  if(mode==='analyze_fulltext'&&!confirm('将本地 PDF 中最多12个选定正文片段发送至当前配置的大模型服务商，可能产生调用费用。继续？'))return;
  try{
    await api('/api/job',{mode,ids:[pid],confirm_data_transfer:mode==='analyze_fulltext'||mode==='analyze_experiments'});
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
async function askCurrentPaper(){
  const id=activeFulltextId,question=$('readerQuestion').value.trim(),ai=$('readerQuestionUseAi').checked;
  const target=$('readerQuestionOutput'),button=$('readerQuestionRun');
  if(!id)return;
  if(question.length<5){target.textContent='请先输入至少五个字符的研究问题。';return}
  const state=getLocalModelState();
  if(ai&&!state.ready){target.textContent='AI 尚未连接，下面可先使用原文定位。请连接模型或取消 AI 选项。';openAiSetup();return}
  if(ai&&!confirm('本次将发送你的研究问题和最多六段已检索到的 PDF 原文给模型服务商，可能产生费用。是否继续？'))return;
  button.disabled=true;target.textContent='正在寻找与问题相关的 PDF 原文…';
  try{
    const result=await api('/api/fulltext/ask',{paper_id:id,question,use_model:ai,confirm_transfer:ai});
    if(id!==activeFulltextId)return;
    target.replaceChildren();
    const answer=document.createElement('p');answer.className='reader-answer';answer.textContent=result.answer||'暂无相关原文';target.appendChild(answer);
    if(result.model_used){const tag=document.createElement('small');tag.textContent='AI 组织的文字，所有引用编号对应下方 PDF 原文；请核对具体结论。';target.appendChild(tag)}
    for(const item of result.references||[]){
      const card=document.createElement('article');card.className='evidence-card';
      const title=document.createElement('strong');title.textContent=`[${item.id}] PDF 第 ${item.page} 页${item.section?' · '+item.section:''}`;card.appendChild(title);
      const block=document.createElement('blockquote');block.textContent=item.quote;card.appendChild(block);
      const jump=document.createElement('button');jump.type='button';jump.className='button secondary compact';jump.textContent='定位至 PDF 第 '+item.page+' 页';
      jump.addEventListener('click',()=>{moveLocalPdfPage(Number(item.page));$('fulltextPdfViewer').scrollIntoView({behavior:'smooth',block:'nearest'});});
      card.appendChild(jump);target.appendChild(card);
    }
  }catch(err){target.textContent='这次问答没有完成：'+err.message+'。你仍可通过原文搜索查看已解析内容。'}
  finally{button.disabled=false}
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
function chosenSources(){
  return [...document.querySelectorAll('.source-option:checked')].map(x=>x.value);
}
function updateSourceSelection(){
  const count=chosenSources().length, message=$('sourceSelectionMessage');
  if(message){
    message.textContent=count?`已选 ${count} 个来源`:'请至少选择一个文献来源';
    message.classList.toggle('error',!count);
  }
  return count;
}
function requireSearchSources(){
  if(!bootstrapReady)throw new Error('设置尚未加载完成，请稍后刷新页面。');
  if(!updateSourceSelection()){
    $('homeSourcePicker')?.scrollIntoView({behavior:'smooth',block:'center'});
    throw new Error('请先勾选至少一个文献来源，例如 arXiv。');
  }
}
function renderStorageHealth(state){
  const healthyState=state?.ok!==false;
  storageReady=healthyState;
  const box=$('storageRecoveryBanner');
  if(box){box.hidden=healthyState;
    if(!healthyState)$('storageRecoveryText').textContent=state.message||'论文库文件暂时无法访问。';}
  const path=$('storageSettingsPath');
  if(path && state?.data_directory)path.textContent='数据目录：'+state.data_directory;
  const summary=$('storageSettingsStatus');
  if(summary)summary.textContent=healthyState?'连接正常':(state?.message||'论文库暂不可用');
  const run=$('runSearch');
  if(run){run.disabled=!bootstrapReady || !healthyState || Boolean(config?.config_health?.read_only);
    run.title=healthyState?'':'论文库暂时无法访问，请检查数据目录';}
  const hint=$('searchAvailability');
  if(hint)hint.textContent=!bootstrapReady?'正在读取设置…':healthyState?'可直接开始':'论文库不可用，检索已暂停';
}
async function loadBootstrap(){
  const data=await api('/api/bootstrap');
  setSettings(data);
  bootstrapReady=true;
  updateSourceSelection();
  renderStorageHealth({ok:true}); // real status comes from /api/state
  return data;
}
async function refreshStorageHealth(){
  try{const data=await api('/api/storage-health');renderStorageHealth(data);}
  catch(err){const summary=$('storageSettingsStatus');if(summary)summary.textContent='状态检查失败：'+err.message;}
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
  const newKeyLink=$('aiProviderGetKey');if(newKeyLink){newKeyLink.hidden=!info.console;if(info.console)newKeyLink.href=safeLink(info.console);}
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
async function refreshModelPreflight(){
  const action=$('modelEstimateAction').value;
  $('modelStatusText').textContent='正在检查本地模型配置…';
  try{
    const res=await api('/api/model/preflight?action='+encodeURIComponent(action));
    const m=res.model,e=res.estimate;
    $('modelStatusText').textContent=(m.configured?'已保存模型配置':'尚未配置模型')+' · '+m.suggestion;
    $('modelDataNotice').textContent='可能发送：'+e.transmitted_fields.join('、')+'。'+e.transmission_notice;
    const t=e.prompt_tokens_range,o=e.output_tokens_range;
    $('modelEstimateValue').textContent=e.estimated_cost?`粗略预估 $${e.estimated_cost.min}～$${e.estimated_cost.max} USD`:'尚未填写完整的模型输入/输出单价，无法估价';
    $('modelEstimateWarning').textContent=`按 ${e.documents} 次任务估算；输入 ${t[0]}～${t[1]} Token，输出 ${o[0]}～${o[1]} Token。${e.warning}`;
  }catch(err){$('modelStatusText').textContent='检查暂不可用：'+err.message}
}
async function loadLibrary(append=false){
  // A new filter starts from page one. Older in-flight replies are ignored.
  if(append && libraryLoading)return;
  const reqId=++libraryRequestId;
  const offset=append?libraryOffset:0;
  libraryLoading=true;
  const q=$('librarySearch').value, mode=$('libraryFilter').value;
  const pars=new URLSearchParams({q,offset:String(offset),limit:'30',sort:$('librarySort').value,scope:$('libraryScope').value});
  if(mode==='starred')pars.set('starred','1');else if(['inbox','skipped','actionable'].includes(mode))pars.set('triage',mode);else if(mode!=='all')pars.set('status',mode);
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
      '<div class="empty">还没有论文记录。输入关键词并检索即可开始。</div>';
    $('homeRecentList').innerHTML=out.papers.length?out.papers.map(p=>
      `<article class="paper-item"><strong class="paper-title">${escape(p.title)}</strong><div class="paper-meta">${escape((p.published||'').slice(0,10))} · ${escape(p.source||'未知来源')}</div><button class="button secondary compact home-open-paper" type="button" data-id="${escape(p.paper_id)}">阅读论文</button></article>`
      ).join(''):'<div class="empty">还没有论文。点击“查找相关论文”开始。</div>';
  }catch{}
}
function refreshLocalTopicTrace(){
  const root=$('localTopicTraceWorkbench');if(!root)return;
  localTopicTrace=window.PRTrace.mount({root,request:(path,method='GET',body)=>
    method==='GET'?api(path):api(path,body||{}),mode:'local',
    onMessage:(m,error)=>{if(error)notice(m,true)},
    onChanged:()=>localPaperTrace?.refresh()});
}
function loadLocalInquiry(){
  if(!window.PRInquiry)return;
  if(!localInquiry)localInquiry=window.PRInquiry.mount({
    root:$('localInquiryWorkbench'),mode:'local',getTopic:()=>($('activeTopic')?.value||'default'),
    request:(path,method='GET',body=null)=>api(path,method==='GET'?null:body),
    onCompleted:()=>markGuideStep('analysis'),onNavigate:view=>visible(view),
    getModelState:getLocalModelState,onModelSetup:openAiSetup
  });
  localInquiry.refresh();
  if(window.PRResearchProgram){
    if(!window._localResearchProgram)window._localResearchProgram=window.PRResearchProgram.mount({
      root:$('localResearchProgram'),mode:'local',getTopic:()=>($('activeTopic')?.value||'default'),
      request:(path,method='GET',body=null)=>api(path,method==='GET'?null:body),
      getSharedQuestion:()=>localInquiry?.getQuestion()||'',onAnalysisComplete:()=>markGuideStep('analysis'),
      getModelState:getLocalModelState,onModelSetup:openAiSetup
    });
    window._localResearchProgram.refresh();
  }
}
async function loadReports(){refreshLocalTopicTrace();try{const res=await api('/api/reports');$('reportList').innerHTML=res.reports.length?res.reports.map(p=>`<div class="report-row"><div><div class="report-file">▱　${escape(p.name)}</div><div class="report-info">${escape(p.modified)}　·　${(p.bytes/1024).toFixed(0)} KB</div></div><div class="report-actions">${p.name.endsWith('.md')?`<button class="button secondary compact preview-md" data-name="${escape(p.name)}" type="button">预览</button>`:''}<a class="report-download" href="/download/${encodeURIComponent(p.name)}">下载 ↘</a></div></div>`).join(''):'<div class="empty">暂无报告，完成一次检索后自动生成。</div>';$('latestReport').hidden=!res.reports.some(p=>p.name==='近期文献汇总.docx');$('latestMarkdownPreview').disabled=!res.reports.some(p=>p.name==='近期文献汇总.md');}catch(e){notice(e.message,true)}}
async function loadZoteroPaperChoices(){
  const select=$('zoteroSyncPaper');const prev=select.value;
  try{
    const result=await api('/api/papers?scope=topic&sort=recent&limit=100');
    select.replaceChildren();
    const first=document.createElement('option');first.value='';first.textContent='请选择要同步笔记的论文';select.appendChild(first);
    for(const paper of result.papers||[]){
      const item=document.createElement('option');item.value=paper.paper_id;
      item.textContent=(paper.title||'未命名论文').slice(0,120);
      select.appendChild(item);
    }
    if([...select.options].some(x=>x.value===prev))select.value=prev;
  }catch(error){notice('暂时无法读取论文清单：'+error.message,true)}
}
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
async function checkSourceConnectivity(){
  const button=$('sourceConnectivityCheck'),out=$('sourceConnectivityResults');
  if(!button||!out)return;
  button.disabled=true;out.textContent='正在测试已启用的公开来源…';
  try{
    const info=await api('/api/source-connectivity');
    const lines=(info.checks||[]).map(x=>{
      const detail=x.ok?'连接正常':x.message;
      return `${x.name}：${detail}${x.http_status?'（HTTP '+x.http_status+'）':''} · ${x.seconds}s`;
    });
    out.textContent=(lines.length?lines.join('；'):'没有可检查的公开来源。')+
      (info.proxy_configured?' 检测到系统代理配置。':'');
  }catch(err){out.textContent='连接检查失败：'+err.message}
  finally{button.disabled=false}
}
function formValues(){return {keywords:commaList($('topicKeywords').value),categories:commaList($('categories').value),schedule_time:$('taskTime').value,ai_enabled:$('aiEnabled').checked,api_provider:$('apiProvider').value,api_base_url:$('apiBase').value.trim(),api_model:$('apiModel').value.trim(),llm_input_usd_per_million:Number($('llmInputRate').value),llm_output_usd_per_million:Number($('llmOutputRate').value),llm_data_consent:$('llmConsent').checked,api_key:$('apiKey').value.trim(),lookback_days:Number($('lookbackDays').value),max_results:Number($('quickMaxResults').value||$('maxResults').value),report_limit:Number($('reportLimit').value),ai_limit:Number($('aiLimit').value),sort_mode:$('quickSort').value,sources:[...document.querySelectorAll('.source-option:checked')].map(x=>x.value),auto_download:$('autoDownload').checked,download_limit:Number($('downloadLimit').value),max_pdf_mb:Number($('maxPdfMb').value),max_queries_per_source:Number($('queryBudget').value),source_query_timeout_seconds:Number($('sourceQueryTimeout').value),source_total_timeout_seconds:Number($('sourceTotalTimeout').value),research_goal:$('aiResearchGoal').value.trim(),openreview_invitation:$('openreviewInvitation').value.trim(),ai_rerank_enabled:$('aiRerankEnabled').checked,ai_rerank_limit:Number($('aiRerankLimit').value),schedule_all_topics:$('scheduleAllTopics').checked,update_check_enabled:$('updateEnabled').checked,auto_update_on_startup:$('autoUpdateStartup').checked,update_check_interval_hours:Number($('updateInterval').value),service_run_mode:$('serviceRunMode').value};}
async function save(silent=false){requireSearchSources();const result=await api('/api/config',formValues());$('apiKey').value='';$('keyState').textContent=result.api_key_saved?'已保存 API Key':'尚未保存 API Key';if(!silent)notice('设置已保存');return result}
function searchValues(){
  return {keywords:commaList($('topicKeywords').value),sources:[...document.querySelectorAll('.source-option:checked')].map(x=>x.value),
    max_results:Number($('quickMaxResults').value),sort_mode:$('quickSort').value,
    max_queries_per_source:Number($('queryBudget').value)};
}
async function saveSearchOnly(){
  const payload=searchValues();
  if(!payload.keywords.length)throw Error('请先输入至少一个研究关键词。可以使用下方关键词示例。');
  requireSearchSources();
  // Partial config update is supported by /api/config; never re-submit hidden model or service preferences.
  await api('/api/config',payload);
}
async function job(mode){
  const search=['search','search_all'].includes(mode);
  if(mode==='test_api'&&!confirm('连接测试会向当前模型服务商发送固定测试文字，可能产生费用。继续吗？'))return;
  try{
    if(search)await saveSearchOnly();
    // Export, synthesis and API tests use the settings already saved by the user.
    await api('/api/job',{mode,confirm_remote_test:mode==='test_api'});
    notice(({search:'已开始查找论文，可在右下角查看进度',export:'正在生成报告',test_api:'正在测试模型连接',
      export_all:'正在导出文献清单',export_experiments:'正在导出实验数据',
      synthesis:'正在整理跨论文实验比较',search_all:'正在查找全部研究主题的论文'})[mode]||'任务已开始');
    await updateState();
  }catch(err){notice('操作未开始：'+err.message,true)}
}
function getLocalModelState(){
  const c=config?.config||{};
  const enabled=!!(c.ai_enabled||c.ai_rerank_enabled);
  return {enabled,keySaved:!!c.api_key_saved,consent:!!c.llm_data_consent,
    ready:enabled && !!c.api_key_saved && !!c.llm_data_consent};
}
function refreshAiDiscovery(c){
  // Legacy per-page prompt is replaced by the single global setup banner.
  const old=$('aiDiscovery');if(old)old.hidden=true;
  const status=$('aiConnectionBanner');if(!status)return;
  refreshAiSetupState(c);
  const enabled=!!(c.ai_enabled||c.ai_rerank_enabled),hasKey=!!c.api_key_saved;
  if(!enabled || (hasKey && c.llm_data_consent) || aiReminderPaused){status.hidden=true;return;}
  status.hidden=false;
  $('aiBannerTitle').textContent=!hasKey?'AI 功能已开启，尚未添加模型密钥':'模型密钥已保存，等待确认数据发送范围';
  $('aiBannerDetail').textContent=!hasKey?
    '添加密钥后可以生成检索词、中文摘要和智能排序。现在仍能正常检索、阅读和整理文献。':
    '请在模型设置中确认允许发送的研究内容。未经授权不会发送论文资料。';
  $('aiBannerSetup').textContent=!hasKey?'添加模型密钥':'查看数据授权';
}
function refreshAiSetupState(c=config?.config||{}){
  const status=$('aiSetupState');if(!status)return;
  status.textContent=!c.api_key_saved?
    '第 1 步：选择模型服务商。第 2 步：在服务商控制台创建 API Key 并填写下方密钥。第 3 步：确认授权并点击“保存并启用模型”。':
    !c.llm_data_consent?'密钥已保存。请阅读数据传输说明，勾选授权后保存即可使用 AI。':
    '模型配置已保存。可测试连接；测试会发送固定文字，可能产生少量费用。';
  const info=providerInfo[$('apiProvider').value]||{};
  const link=$('aiProviderGetKey');link.hidden=!info.console;
  if(info.console)link.href=safeLink(info.console);
}
function openAiSetup(){
  if(window.PRModelWizard149){window.PRModelWizard149.open();return}
  aiReminderPaused=false;
  visible('settings');$('settings-ai').open=true;
  refreshAiSetupState();
  const target=getLocalModelState().keySaved?$('llmConsent'):$('apiKey');
  $('settings-ai').scrollIntoView({behavior:'smooth',block:'start'});
  if(target)target.focus({preventScroll:true});
}
async function saveAiOnly({silent=false}={}){
  const c={ai_enabled:$('aiEnabled').checked,ai_rerank_enabled:$('aiRerankEnabled').checked,
    ai_rerank_limit:Number($('aiRerankLimit').value),ai_limit:Number($('aiLimit').value),
    api_provider:$('apiProvider').value,api_base_url:$('apiBase').value.trim(),
    api_model:$('apiModel').value.trim(),llm_data_consent:$('llmConsent').checked,
    llm_input_usd_per_million:Number($('llmInputRate').value),
    llm_output_usd_per_million:Number($('llmOutputRate').value)};
  const key=$('apiKey').value.trim();if(key)c.api_key=key;
  const result=await api('/api/config',c);
  $('apiKey').value='';
  // Retrieve persisted readiness; an API key must never be echoed back into an input.
  const current=await api('/api/state');setSettings(current);
  aiReminderPaused=false;refreshAiDiscovery(current.config);
  if(!silent)notice(!result.api_key_saved?'模型偏好已保存。添加 API Key 后可连接模型':!current.config.llm_data_consent?'模型密钥已保存。确认数据传输范围后才能使用 AI':'模型设置已保存，现可使用 AI 辅助功能');
  return result;
}
function setSettings(data){config=data;providers=data.api_presets||providers;const c=data.config;if(!$('topicKeywords').matches(':focus'))$('topicKeywords').value=(c.keywords||[]).join(', ');$('categories').value=(c.categories||[]).join(', ');$('openreviewInvitation').value=c.openreview_invitation||'';$('taskTime').value=c.schedule_time||'08:30';$('lookbackDays').value=c.lookback_days;$('maxResults').value=c.max_results;$('quickMaxResults').value=c.max_results;$('reportLimit').value=c.report_limit;$('aiLimit').value=c.ai_limit;$('aiRerankEnabled').checked=!!c.ai_rerank_enabled;$('aiRerankLimit').value=c.ai_rerank_limit||15;$('scheduleAllTopics').checked=!!c.schedule_all_topics;if(!$('aiResearchGoal').matches(':focus'))$('aiResearchGoal').value=c.research_goal||'';$('queryBudget').value=c.max_queries_per_source||8;$('sourceQueryTimeout').value=c.source_query_timeout_seconds||35;$('sourceTotalTimeout').value=c.source_total_timeout_seconds||210;$('updateEnabled').checked=c.update_check_enabled!==false;$('autoUpdateStartup').checked=c.auto_update_on_startup===true;$('serviceRunMode').value=c.service_run_mode||'foreground';$('updateInterval').value=c.update_check_interval_hours||24;$('apiProvider').value=c.api_provider;$('apiBase').value=c.api_base_url;$('apiModel').value=c.api_model;$('llmInputRate').value=c.llm_input_usd_per_million||0;$('llmOutputRate').value=c.llm_output_usd_per_million||0;$('llmConsent').checked=!!c.llm_data_consent;$('sortMode').value=c.sort_mode||'relevance';$('quickSort').value=c.sort_mode||'relevance';sourceDefinitions=data.sources||[];sourceAccess=data.source_access||{};providerInfo=data.provider_info||{};providerUsage=data.api_usage_local||{};
 const providerSelect=$('apiProvider');providerSelect.innerHTML='';
 Object.keys(providers).forEach(name=>{const opt=document.createElement('option');opt.value=name;opt.textContent=name;providerSelect.appendChild(opt)});
 providerSelect.value=c.api_provider;
 $('sourceOptions').innerHTML=sourceDefinitions.map(x=>`<label class="source-choice" title="${escape(x.note||'')}"><input class="source-option" type="checkbox" value="${escape(x.name)}" ${(c.sources||['arxiv','openalex','crossref']).includes(x.name)?'checked':''} /><span>${escape(x.label)}</span>${x.access!=='public'?'<small>需密钥</small>':''}</label>`).join('');
 updateSourceSelection();
 $('openalexState').textContent=sourceAccess.openalex?.key?'已保存':'未配置';
 $('springerState').textContent=sourceAccess.springer?.key?'已保存':'未配置';
 $('elsevierState').textContent=sourceAccess.elsevier?.key?'已保存':'未配置';
 updateSourceHelp();refreshProviderLabels();$('autoDownload').checked=!!c.auto_download;$('downloadLimit').value=c.download_limit||3;$('maxPdfMb').value=c.max_pdf_mb||30;$('aiEnabled').checked=!!c.ai_enabled;$('keyState').textContent=c.api_key_saved?'已保存 API Key':'尚未保存 API Key；常规检索仍可使用';refreshAiDiscovery(c);refreshModelPreflight();
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
    status.textContent=UI.sourceState(entry);
    status.className='source-diagnostic-state '+(entry.ok?'source-ok':'source-failed');
    row.appendChild(status);
    const info=document.createElement('small');
    let detail=`获取 ${entry.count||0} 篇 · ${entry.seconds||0} 秒`;
    const remote=(entry.queries||[]).reduce((n,q)=>n+(q.stats?.remote_hits||0),0);
    const removed=(entry.queries||[]).reduce((n,q)=>n+(q.stats?.date_rejected||0),0);
    if(entry.name==='openreview'&&remote)detail+=` · 服务端返回 ${remote} 条，日期排除 ${removed} 条`;
    if(entry.rate_limit_remaining!==undefined)detail+=` · API 剩余额度 ${entry.rate_limit_remaining}`;
    if(entry.retry_after_seconds!==undefined)detail+=` · 建议等待 ${entry.retry_after_seconds} 秒`;
    info.textContent=detail;
    row.appendChild(info);
    if(!entry.ok||entry.partial||entry.cache_state==='stale'){const advice=document.createElement('small');advice.className='source-next-action';advice.textContent=UI.sourceAdvice(entry);row.appendChild(advice)}
    target.appendChild(row);
  }
}
function renderRetrievalCounts(c){
  const panel=$('retrievalCountsDetails'),out=$('retrievalCounts');
  if(!panel||!out)return;
  const rows=[['来源候选',c?.source_candidates],['日期合格',c?.date_passed],
    ['日期剔除',c?.date_removed],['跨源去重后',c?.cross_source_unique],
    ['重复剔除',c?.duplicate_removed],['排除 Zotero 后',c?.zotero_passed],
    ['Zotero 排除',c?.zotero_removed],['相关性合格',c?.relevance_passed],
    ['相关性剔除',c?.relevance_removed],['排序截取后',c?.rank_limit],
    ['本轮写入',c?.stored],['真正新增',c?.new_added],['报告选用',c?.report_selected]]
    .filter(x=>Number.isInteger(x[1])&&x[1]>=0);
  panel.hidden=!rows.length;
  out.replaceChildren();
  for(const [label,count] of rows){const row=document.createElement('div');
    row.className='source-diagnostic-row';
    const title=document.createElement('strong');title.textContent=label;row.appendChild(title);
    const value=document.createElement('span');value.textContent=`${count} 篇`;row.appendChild(value);
    out.appendChild(row);
  }
}
const TRACE_EVENT_LABEL={run_start:'开始检索',run_plan:'检索参数',run_finish:'检索结束',source_start:'开始来源',
  source_finish:'来源结束',query_start:'开始查询组',query_finish:'查询完成',query_error:'查询失败',
  child_spawn:'启动检索子进程',child_exit:'子进程返回',child_error:'子进程错误',
  http_start:'开始网络请求',http_connected:'服务器已响应',http_read:'读取响应完成',http_error:'网络请求失败',stage_count:'筛选数量'};
let retrievalTraceId='',retrievalTraceAt=0;
function traceLine(event){
  let when=String(event.at||'').slice(11,19);
  try{if(event.at)when=new Date(event.at).toLocaleTimeString('zh-CN',{hour12:false});}catch{}
  const src=event.source||'系统';
  const head=`${when}  ${src} · ${TRACE_EVENT_LABEL[event.event]||'诊断事件'}`;
  const parts=[];
  if(event.host)parts.push(event.host);
  if(event.query_index)parts.push(`第 ${event.query_index}/${event.query_total||'?'} 组`);
  if(event.http_status)parts.push(`HTTP ${event.http_status}`);
  if(event.lookback_days)parts.push(`近 ${event.lookback_days} 天`);
  if(event.max_results)parts.push(`上限 ${event.max_results} 篇`);
  if(event.proxy_configured!==undefined)parts.push(`代理${event.proxy_configured?'已配置':'未配置'}`);
  if(event.exit_code!==undefined)parts.push(`退出码 ${event.exit_code}`);
  if(event.stdout_bytes!==undefined)parts.push(`输出 ${event.stdout_bytes} 字节`);
  if(event.stderr_bytes!==undefined)parts.push(`错误输出 ${event.stderr_bytes} 字节`);
  if(event.stage)parts.push(`阶段 ${event.stage}`);
  if(event.filter_stage)parts.push(`步骤 ${event.filter_stage}`);
  if(event.count!==undefined)parts.push(`${event.count} 篇`);
  if(event.removed!==undefined)parts.push(`排除 ${event.removed} 篇`);
  if(event.remote_hits!==undefined)parts.push(`远端命中 ${event.remote_hits} 条`);
  if(event.date_rejected!==undefined)parts.push(`日期排除 ${event.date_rejected} 条`);
  if(event.retry_after_seconds!==undefined)parts.push(`建议等待 ${event.retry_after_seconds} 秒`);
  if(event.rate_limit_remaining!==undefined)parts.push(`剩余额度 ${event.rate_limit_remaining}`);
  if(event.rate_limit_scope)parts.push(`限流类别 ${event.rate_limit_scope}`);
  if(event.error_code&&event.error_code!=='OK')parts.push(`${event.error_code}（${UI.sourceState({error_code:event.error_code})}）`);
  if(event.elapsed_ms!==undefined)parts.push(`${event.elapsed_ms}ms`);
  return `${head}${parts.length?' · '+parts.join(' · '):''}`;
}
async function loadRetrievalTrace(id,{force=false}={}){
  const details=$('retrievalTraceDetails');if(!details||!id)return;
  if(!force&&id===retrievalTraceId&&Date.now()-retrievalTraceAt<8000)return;
  retrievalTraceId=id;retrievalTraceAt=Date.now();
  $('downloadRetrievalTrace').href='/api/retrieval-trace/export?run_id='+encodeURIComponent(id);
  try{
    const res=await api('/api/retrieval-trace?run_id='+encodeURIComponent(id));
    if(retrievalTraceId!==id)return;
    $('retrievalTraceSummary').textContent=`编号 ${id} · ${res.event_count||0} 条事件 · ${res.error_count||0} 条错误`;
    $('retrievalTraceEvents').textContent=(res.events||[]).slice(-65).map(traceLine).join('\n')||'暂无线索。';
  }catch(error){if(retrievalTraceId!==id)return;
    $('retrievalTraceSummary').textContent='诊断记录暂不可读：'+error.message;
    $('retrievalTraceEvents').textContent='可稍后刷新；若磁盘不可写，请检查本地数据目录。';}
}
$('refreshRetrievalTrace').addEventListener('click',()=>{
 const id=retrievalTraceId;if(id)loadRetrievalTrace(id,{force:true});
});
let dismissedTaskMarker='';
let taskDockAutoExpandedFor='';
let taskDockWasRunning=false;
let lastTaskSnapshot=null;
function taskMarker(run){
  return [run?.started_at||'',run?.finished_at||'',run?.timestamp||'',run?.mode||'',run?.state||''].join('|');
}
function setTaskDockExpanded(expanded){
  const open=Boolean(expanded);
  const panel=$('taskCenter'),body=$('taskDockBody'),toggle=$('taskDockToggle');
  panel.dataset.expanded=open?'true':'false';
  toggle.setAttribute('aria-expanded',String(open));
  toggle.setAttribute('aria-label',open?'最小化任务面板':'展开查看任务详情');
  toggle.title=open?'最小化任务面板':'展开查看任务详情';
  $('taskDockToggleLabel').textContent=open?'最小化':'查看详情';
  body.setAttribute('aria-hidden',String(!open));
  body.inert=!open;
}
function showTaskProgress(data){
  const active=Boolean(data.runtime?.busy||data.last_run?.state==='running');
  const run=active?data.runtime:data.last_run;
  const progress=run?.progress||{};
  const last=data.last_run||{};
  lastTaskSnapshot=last;
  const result=UI.taskSummary(last);
  const hasState=active||['success','failed','partial'].includes(last.state);
  const marker=taskMarker(last);
  const activeMarker=active?taskMarker(run||last):'';
  const newRunningTask=active&&(!taskDockWasRunning||taskDockAutoExpandedFor!==activeMarker);
  taskDockWasRunning=active;
  $('taskCenter').hidden=!hasState||(!active&&dismissedTaskMarker===marker);
  $('taskDockDismiss').hidden=active;
  if(!hasState)return;
  if(newRunningTask){
    taskDockAutoExpandedFor=activeMarker;
    dismissedTaskMarker='';
    setTaskDockExpanded(true);
  }
  const current=Number.isFinite(Number(progress.percent))?Math.max(0,Math.min(100,Number(progress.percent))):0;
  $('taskProgressBar').value=current;
  $('taskPercent').textContent=active?current+'%':'已结束';
  $('taskStage').textContent=active?(progress.stage||'正在执行'):result.label;
  $('taskCenter').dataset.outcome=active?'running':result.kind;
  const counts=Number.isFinite(progress.current)&&Number.isFinite(progress.total)&&progress.total>0?` · ${progress.current}/${progress.total}`:'';
  const duration=Number.isFinite(progress.elapsed_seconds)?` · 已用时 ${progress.elapsed_seconds} 秒`:'';
  $('taskMeta').textContent=active?(progress.detail||data.runtime?.phase||'正在处理')+counts+duration:
    result.detail+(last.error&&!result.detail?' · '+String(last.error).slice(0,120):'');
  renderSourceDiagnostics(data.runtime?.result?.source_results||last.result?.source_results||[]);
  renderRetrievalCounts(data.runtime?.result?.retrieval_counts||last.result?.retrieval_counts||{});
  const diagnosticId=(active?data.runtime?.diagnostic_run_id:null)||last.diagnostic_run_id||'';
  $('retrievalTraceDetails').hidden=!diagnosticId;
  if(diagnosticId){
    const changed=diagnosticId!==retrievalTraceId;
    if(changed){$('retrievalTraceSummary').textContent='正在读取诊断…';$('retrievalTraceEvents').textContent='';}
    loadRetrievalTrace(diagnosticId,{force:changed});
  }

  const lines=active?(data.runtime.logs||[]):[];
  $('taskLiveLog').textContent=lines.length?lines.join('\n'):'历史记录请到“任务中心”查看。';
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
      row.appendChild(detail);
      const id=String((run.summary||{}).diagnostic_run_id||'');
      if(/^\d{8}T\d{6}Z-[a-f0-9]{10}$/.test(id)){
        const link=document.createElement('a');
        link.className='text-button';link.textContent='下载本次检索诊断';
        link.href='/api/retrieval-trace/export?run_id='+encodeURIComponent(id);
        link.download='Paper-Radar-Retrieval-Diagnostics.zip';row.appendChild(link);
      }
      target.appendChild(row);
    }
  }catch(error){target.textContent='任务记录暂时不可用：'+error.message;}
}
async function loadAgents(){
  const label=$('agentOverview'),grid=$('agentGrid'),other=$('agentOnDemand'),research=$('agentResearch');
  if(!label||!grid||!other)return;
  label.textContent='正在读取后台状态…';
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
  }catch(error){label.textContent='后台状态读取失败：'+error.message;grid.textContent='';other.textContent='';research.textContent='';}
}

function renderConfigRecovery(health){
  const banner=$('configRecoveryBanner'),message=$('configRecoveryText');
  if(!banner||!message)return;
  const blocked=Boolean(health?.read_only);
  banner.hidden=!blocked;
  if(blocked)message.textContent=health.message||'主配置无法读取。现有设置和研究资料已保留，当前只能查看资料。';
}

async function updateState(first=false){if(loading)return;loading=true;try{const data=await api('/api/state');renderConfigRecovery(data.config_health);renderStorageHealth(data.storage_health||{ok:true});$('statArchived').textContent=data.stats.unavailable?'—':data.stats.archived;$('firstRunGuide').hidden=true;renderHome(data);$('statMatched').textContent=data.stats.unavailable?'—':data.stats.matched;const recent=data.last_run||{};const recentSummary=UI.taskSummary(recent);const searchFinished=['search','search_all'].includes(recent.mode) && !['idle','running'].includes(recentSummary.kind);
$('afterSearch').hidden=!searchFinished;
$('afterSearch').querySelector('strong').textContent=recentSummary.label;
const advice=UI.retrievalAdvice(recent);
$('afterSearchText').textContent=searchFinished?(advice?advice.detail+' '+advice.action:recentSummary.detail):'';
const nextSearch=$('afterSearch').querySelector('.next-step-buttons .button.primary');
nextSearch.dataset.view=recentSummary.kind==='failed'||recentSummary.kind==='empty'?'dashboard':'library';
nextSearch.textContent=recentSummary.kind==='failed'||recentSummary.kind==='empty'?'调整关键词后重试':'查看找到的论文';$('statDownloaded').textContent=data.stats.unavailable?'—':(data.stats.downloaded||0);$('navDownloadCount').textContent=data.stats.downloaded||0;$('navLibraryCount').textContent=data.stats.unavailable?'—':data.stats.archived;const last=data.last_run||{};const taskResult=UI.taskSummary(last);
if($('taskViewStatus')){
  $('taskViewStatus').textContent=data.runtime?.busy?'正在执行':taskResult.label;
  $('taskViewDetail').textContent=data.runtime?.busy?(data.runtime.phase||'正在执行，请查看右下角任务面板'):
    taskResult.detail||String(last.error||'暂无任务记录');
}
$('statRun').textContent=data.runtime?.busy?'正在执行':taskResult.label;$('statRunDetail').textContent=taskResult.detail||(last.finished_at||last.timestamp||'').slice(0,16).replace('T',' ');showTaskProgress(data);renderUpdate(data.update);renderTopics(data.topics_info);const diagnosis=last.result?.model_diagnosis;const panel=$('modelFailureHelp');if(panel){panel.hidden=!diagnosis;if(diagnosis)panel.textContent=`${diagnosis.title}：${diagnosis.action}`}const busy=data.runtime.busy;const recovery=Boolean(data.config_health?.read_only);$('runSearch').disabled=busy||recovery||!bootstrapReady||!storageReady;$('runAllTopics').disabled=busy||recovery||!bootstrapReady||!storageReady;$('activeTopic').disabled=busy||recovery;$('runExport').disabled=busy;$('exportReports').disabled=busy;$('testApi').disabled=busy;$('downloadSelected').disabled=busy;$('downloadStarred').disabled=busy;$('exportAll').disabled=busy;$('exportExperiments').disabled=busy;$('generateSynthesis').disabled=busy;$('progressLine').hidden=!busy;$('progressText').textContent=data.runtime.phase||'正在处理…';if($('logSource').value==='manual'&&busy)$('logBox').textContent=(data.runtime.logs||[]).join('\n')||'正在执行任务…';updateSchedule(data.scheduler);if(first){setSettings(data);if(data.onboarding_needed && !readGuideSteps().search)$('homeGuide').open=true;}if(data.runtime?.maintenance)$('processState').textContent='正在准备安全更新，停止接收新任务';if(jobWasBusy&&!busy){refreshHistory();loadTaskView();loadFollowing();loadRecent();loadLibrary();loadReports();loadDownloads();if(taskResult.kind!=='idle')notice(taskResult.label+(taskResult.detail?' · '+taskResult.detail:''),taskResult.kind==='failed')}jobWasBusy=busy;}catch(err){if(first)notice('本地服务连接失败：'+err.message,true)}finally{loading=false}}
const followingLabels={new:'新收录',revised:'新版本',metadata_updated:'元数据变化',
  published_version:'发表信息',withdrawal_signal:'撤稿线索（待确认）'};
let followingData=null;
function followingFriendlyTime(x){return x?String(x).slice(0,16).replace('T',' '):'尚未完成'}
async function loadFollowing(){
  const box=$('followingEvents');if(!box)return;
  try{
    const data=await api('/api/following');followingData=data;
    $('followingUnread').textContent=data.unread;
    $('followingPending').textContent=data.pending_sources;
    $('followNavCount').textContent=data.unread||'0';
    $('followingLast').textContent=followingFriendlyTime(data.last_run?.completed_at);
    const pending=Number(data.pending_sources||0)>0;
    $('followingStatus').textContent=!data.checkpoints?.length?'尚未检索':pending?'部分来源待补检':'来源检查完成';
    $('followingIntro').textContent=pending?
      '仍有来源待补检。':
      '各来源最近检查记录可在下方查看。';
    const checkpoints=$('followingCheckpoints');checkpoints.replaceChildren();
    if(!data.checkpoints.length)checkpoints.textContent='首次检索后会显示各来源的检查进度。';
    for(const x of (data.checkpoints||[])){
      const line=document.createElement('div');line.className='follow-row';
      const strong=document.createElement('strong');strong.textContent=x.source_name;
      const detail=document.createElement('span');detail.textContent=(x.status==='complete'?'已覆盖':'待补检')+
        ' · 上次成功 '+followingFriendlyTime(x.last_success)+
        (x.status==='complete'?'':' · '+(x.error_code||'部分结果未获取'));
      line.append(strong,detail);checkpoints.appendChild(line);
    }
    box.replaceChildren();
    if(!data.events.length)box.textContent='尚无论文变更；检索完成后会在这里记录。';
    for(const x of data.events){
      const line=document.createElement('div');line.className='follow-row';
      const strong=document.createElement('strong');strong.textContent=x.title;
      const detail=document.createElement('span');detail.textContent=
        (followingLabels[x.kind]||x.kind)+' · '+x.source_name+' · '+followingFriendlyTime(x.observed_at)+
        (x.notification_id&&!x.seen_at?' · 未读':'');
      line.append(strong,detail);box.appendChild(line);
    }
    const prefs=data.preferences||{};
    for(const [key,id] of [['enabled','followingEnabled'],['notify_new','followingAlertNew'],
      ['notify_updated','followingAlertUpdated'],['email_enabled','followingEmailEnabled']])$(id).checked=Boolean(prefs[key]);
    $('followingEmailTo').value=prefs.email_to||'';
  }catch(error){box.textContent='跟进记录加载失败：'+error.message;}
}
async function saveFollowingPreferences(){
  try{
    const result=await api('/api/following/preferences',{
      enabled:$('followingEnabled').checked,notify_new:$('followingAlertNew').checked,
      notify_updated:$('followingAlertUpdated').checked,
      email_enabled:$('followingEmailEnabled').checked,email_to:$('followingEmailTo').value.trim()});
    $('followingSaved').textContent='已保存';await loadFollowing();
  }catch(error){notice('保存失败：'+error.message,true)}
}
async function markFollowingSeen(){
  try{
    const ids=(followingData?.events||[]).filter(x=>x.notification_id&&!x.seen_at).map(x=>x.id);
    if(!ids.length)return;
    const result=await api('/api/following/seen',{event_ids:ids});
    notice('已处理 '+result.changed+' 条提醒');await loadFollowing();
  }catch(error){notice(error.message,true)}
}
async function followBackup(full){
  const el=$('followingBackupStatus');el.textContent='正在核对并备份，请保持服务运行…';
  try{
    const res=await api('/api/backups/create',{include_pdfs:full});
    el.textContent='备份完成：'+res.backup.files+' 个文件。位置：'+res.backup.path;
  }catch(error){el.textContent='备份未完成：'+error.message;notice(error.message,true)}
}
const hook = (id, handler) => $(id).addEventListener('click',handler);
async function scheduleAction(action){try{
  if(action==='install')await api('/api/config',{
    schedule_time:$('taskTime').value,schedule_all_topics:$('scheduleAllTopics').checked
  });
  const res=await api('/api/schedule',{action});updateSchedule(res);
  if(action==='install'&&!res?.installed){
    throw Error(res?.state==='unsupported'?'当前系统不支持内置自动计划；Windows 以外平台请使用系统计划任务。':'系统没有确认每天跟踪已开启，请检查任务计划设置。');
  }
  notice(({install:'后台更新已设置',remove:'后台更新已关闭',run:'任务已启动'})[action]);
  return res;
}catch(err){notice(err.message,true);throw err;}}
// v1.49.0 — user-facing model setup and scheduled follow-up, without extra dependencies.
// All secret storage and validation continue to use the existing local API.
(function(){
  'use strict';
  const el=id=>document.getElementById(id);
  const keyPages={
    'DeepSeek':'https://platform.deepseek.com/api_keys',
    'OpenAI / ChatGPT API':'https://platform.openai.com/api-keys',
    'OpenAI':'https://platform.openai.com/api-keys',
    'Google Gemini':'https://aistudio.google.com/api-keys',
    'SiliconFlow':'https://cloud.siliconflow.cn/account/ak',
    '智谱 GLM':'https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys',
    '阿里云通义千问':'https://bailian.console.aliyun.com/',
    '火山引擎豆包':'https://console.volcengine.com/ark',
    'Claude (Anthropic)':'https://platform.claude.com/settings/keys',
  };
  let step=1,busy=false;
  const stage=(stepNumber)=>{
    step=Math.min(3,Math.max(1,stepNumber));
    for(const section of document.querySelectorAll('[data-model-step]'))section.hidden=Number(section.dataset.modelStep)!==step;
    for(const chip of document.querySelectorAll('[data-model-progress]'))chip.classList.toggle('current',Number(chip.dataset.modelProgress)===step);
    el('modelWizardBack').hidden=step===1;
    el('modelWizardNext').hidden=step===3;
    el('modelWizardSave').hidden=step!==3;
    el('modelWizardMessage').textContent='';
  };
  function providerChanged(){
    const name=el('modelWizardProvider').value;
    const preset=providers[name]||['',''];
    const previous=config?.config||{};
    el('modelWizardBase').value=name===previous.api_provider?previous.api_base_url||preset[0]:preset[0];
    el('modelWizardModel').value=name===previous.api_provider?previous.api_model||preset[1]:preset[1];
    el('modelWizardKey').value='';
    const page=keyPages[name]||providerInfo[name]?.console||'';
    const link=el('modelWizardGetKey');link.hidden=!page;
    if(page)link.href=page;
    el('modelWizardProviderHint').textContent=(providerInfo[name]?.hint||'请选择已经开通的模型平台。')+
      (preset[1]?' 系统已填写常见模型，可按平台实际可用型号调整。':' 请在第二步填写平台提供的模型名称。');
    const hasSaved=name===previous.api_provider && previous.api_key_saved;
    el('modelWizardKeyHint').textContent=hasSaved?'当前平台已保存过密钥；不需要更换时，可以留空。':'请粘贴这个模型平台的 API Key。密钥不会写入项目源代码。';
  }
  function show(){
    if(!el('modelConnectWizard'))return;
    const wizard=el('modelConnectWizard');
    const old=config?.config||{};
    const select=el('modelWizardProvider');select.replaceChildren();
    for(const name of Object.keys(providers||{})){
      const option=document.createElement('option');option.value=name;option.textContent=name;select.appendChild(option);
    }
    select.value=old.api_provider||'DeepSeek';
    providerChanged();
    el('modelWizardConsent').checked=!!old.llm_data_consent;
    el('modelWizardPostSave').hidden=true;
    el('modelWizardKey').type='password';
    el('modelWizardSave').disabled=false;
    busy=false;
    stage(!old.api_key_saved?1:old.llm_data_consent?1:3);
    if(!wizard.open)wizard.showModal();
    const focus=step===3?el('modelWizardConsent'):el('modelWizardProvider');focus.focus({preventScroll:true});
  }
  function close(){
    el('modelWizardKey').value='';
    if(el('modelConnectWizard').open)el('modelConnectWizard').close();
  }
  function advance(){
    if(step===2){
      const s=el('modelWizardProvider').value;
      const old=config?.config||{};
      const existing=old.api_key_saved && s===old.api_provider;
      if(!existing && !el('modelWizardKey').value.trim()){
        el('modelWizardMessage').textContent='请先填写 API Key；如果暂时没有密钥，可以选择普通检索。';
        el('modelWizardKey').focus();return;
      }
      if(!el('modelWizardModel').value.trim()||!el('modelWizardBase').value.trim()){
        el('modelWizardMessage').textContent='请在“模型名称和接口地址”中补充此平台提供的模型信息。';return;
      }
    }
    stage(step+1);
  }
  async function save(){
    if(busy)return;
    if(!el('modelWizardConsent').checked){
      el('modelWizardMessage').textContent='请先阅读并确认发送范围。也可以关闭向导继续普通检索。';return;
    }
    const name=el('modelWizardProvider').value;
    const old=config?.config||{};
    if(!el('modelWizardKey').value.trim()&&!(old.api_key_saved&&old.api_provider===name)){
      el('modelWizardMessage').textContent='尚未输入这个模型平台的 API Key，请返回第二步填写。';return;
    }
    busy=true;el('modelWizardSave').disabled=true;el('modelWizardMessage').textContent='正在保存连接信息…';
    try{
      el('apiProvider').value=name;
      el('apiBase').value=el('modelWizardBase').value.trim();
      el('apiModel').value=el('modelWizardModel').value.trim();
      el('apiKey').value=el('modelWizardKey').value.trim();
      el('llmConsent').checked=true;
      el('aiEnabled').checked=true;
      el('aiRerankEnabled').checked=true;
      const result=await saveAiOnly({silent:true});
      if(!result.api_key_saved)throw Error('未能确认密钥已经保存，请检查系统凭据管理器。');
      el('modelWizardKey').value='';
      el('modelWizardMessage').textContent='密钥与授权已保存。连接尚未测试，测试可能产生少量费用。';
      el('modelWizardPostSave').hidden=false;
      el('modelWizardSave').hidden=true;
      el('modelWizardNext').hidden=true;
      el('modelWizardBack').hidden=true;
    }catch(err){el('modelWizardMessage').textContent='保存失败：'+err.message}
    finally{busy=false;el('modelWizardSave').disabled=false;}
  }
  let followStep=1;
  function followShow(stepNumber=1){
    followStep=stepNumber;
    for(const section of document.querySelectorAll('[data-follow-step]'))section.hidden=Number(section.dataset.followStep)!==stepNumber;
    for(const chip of document.querySelectorAll('[data-follow-progress]'))chip.classList.toggle('current',Number(chip.dataset.followProgress)===stepNumber);
    el('followWizardBack').hidden=stepNumber===1;
    el('followWizardNext').hidden=stepNumber===3;
    el('followWizardSave').hidden=stepNumber!==3;
    el('followWizardMessage').textContent='';
    if(stepNumber===3){
      const scope=document.querySelector('[name="followScope"]:checked')?.value==='all'?'所有研究主题':'当前研究主题';
      el('followWizardConfirm').textContent=`每天 ${el('followWizardTime').value||'08:30'} 检查${scope}。检查到的论文自动收录在文献库。`;
    }
  }
  function openFollow(){
    el('followWizardTime').value=el('taskTime').value||'08:30';
    const choice=document.querySelector(`[name="followScope"][value="${el('scheduleAllTopics').checked?'all':'current'}"]`);
    if(choice)choice.checked=true;
    followShow(1);
    if(!el('followScheduleWizard').open)el('followScheduleWizard').showModal();
  }
  async function submitFollow(){
    const button=el('followWizardSave');button.disabled=true;
    el('followWizardMessage').textContent='正在保存计划…';
    const previousTime=el('taskTime').value,previousScope=el('scheduleAllTopics').checked;
    el('taskTime').value=el('followWizardTime').value;
    el('scheduleAllTopics').checked=document.querySelector('[name="followScope"]:checked')?.value==='all';
    try{
      const result=await scheduleAction('install');
      if(!result)throw Error('无法创建后台任务。');
      el('followScheduleWizard').close();
      notice('每天跟踪已保存，运行时间和状态可在“设置 → 自动跟踪”查看。');
    }catch(error){
      el('taskTime').value=previousTime;el('scheduleAllTopics').checked=previousScope;
      el('followWizardMessage').textContent='暂时无法开启：'+error.message;
    }finally{button.disabled=false}
  }
  window.PRModelWizard149={open:show};
  window.addEventListener('DOMContentLoaded',()=>{
    // Physically relocate existing advanced controls, preserving their IDs and backend behavior.
    const schedule=el('scheduleDisclosure'),slot=el('scheduleExpertContainer');
    if(schedule&&slot){slot.appendChild(schedule);schedule.open=false;}
    const advanced=el('searchAdvanced'),sourceSettings=el('settings-search');
    if(advanced&&sourceSettings){sourceSettings.querySelector('.settings-section-body')?.appendChild(advanced);advanced.open=false;}
    el('modelWizardClose').onclick=close;
    el('modelWizardSkip').onclick=()=>{close();visible('dashboard')};
    el('modelWizardBack').onclick=()=>stage(step-1);
    el('modelWizardNext').onclick=advance;
    el('modelWizardSave').onclick=save;
    el('modelWizardDone').onclick=()=>{close();visible('dashboard')};
    el('modelWizardProvider').onchange=providerChanged;
    el('modelWizardReveal').onclick=()=>{const f=el('modelWizardKey');f.type=f.type==='password'?'text':'password'};
    el('modelWizardTest').onclick=async()=>{
      if(!window.confirm('连接测试会向模型服务商发送一句固定文字，可能产生少量费用。确认执行吗？'))return;
      const button=el('modelWizardTest');button.disabled=true;
      try{await api('/api/job',{mode:'test_api',confirm_remote_test:true});el('modelWizardMessage').textContent='测试任务已提交。可在任务记录查看结果；这不代表已经测试成功。';}
      catch(err){el('modelWizardMessage').textContent='无法提交测试：'+err.message}
      finally{button.disabled=false;}
    };
    el('openModelWizardAdvanced').onclick=show;
    el('followWizardClose').onclick=()=>el('followScheduleWizard').close();
    el('followWizardLater').onclick=()=>el('followScheduleWizard').close();
    el('followWizardBack').onclick=()=>followShow(followStep-1);
    el('followWizardNext').onclick=()=>followShow(followStep+1);
    el('followWizardSave').onclick=submitFollow;
    el('openFollowWizard').onclick=openFollow;
    el('openFollowWizardSearch').onclick=openFollow;
    // Settings can be reached without exposing query budgets on the search page.
  });
})();

window.addEventListener('DOMContentLoaded',()=>{
  hook('followingRefresh',loadFollowing);
  hook('followingSave',saveFollowingPreferences);
  hook('followingMarkSeen',markFollowingSeen);
  hook('followingBackup',()=>followBackup(false));
  hook('followingFullBackup',()=>followBackup(true));
  hook('followingEmailRetry',async()=>{try{const result=await api('/api/following/send-digest',{});
    notice(result.state==='sent'?`已发送 ${result.sent} 条更新`:'邮件未发送：'+result.state)}catch(e){notice(e.message,true)}});
  hook('aiBannerSetup',openAiSetup);
  hook('aiBannerLater',()=>{aiReminderPaused=true;$('aiConnectionBanner').hidden=true;notice('稍后可以从“设置 → 模型服务”继续连接 AI。')});
  hook('aiDiscoverySetup',openAiSetup);
  hook('aiDiscoverySkip',()=>{$('aiDiscovery').hidden=true;});
  window.addEventListener('popstate',()=>restoreLocalRoute().catch(err=>notice(err.message,true)));
  $('homeRecentList').addEventListener('click',event=>{
    const b=event.target.closest('.home-open-paper');if(!b)return;
    visible('library');openFulltext(b.dataset.id).catch(e=>notice('打开论文失败：'+e.message,true));
  });
  hook('retryLocalConnection',retryLocalConnection);
  hook('hideHomeGuide',()=>{$('homeGuide').open=false;notice('入门步骤已收起，可以随时重新展开');});
  hook('fulltextDownload',()=>downloadPapers([activeFulltextId]));
  hook('readerShowSummary',()=>{
    $('readerQuestionPanel').hidden=true;$('readerComparePanel').open=false;
    $('readerSummaryPanel').scrollIntoView({behavior:'smooth',block:'nearest'});
  });
  hook('readerShowCompare',()=>{
    $('readerQuestionPanel').hidden=true;$('readerComparePanel').open=true;
    $('readerComparePanel').scrollIntoView({behavior:'smooth',block:'nearest'});
  });
  hook('readerShowAsk',()=>{
    $('readerQuestionPanel').hidden=false;
    $('readerQuestionPanel').scrollIntoView({behavior:'smooth',block:'nearest'});
    $('readerQuestion').focus({preventScroll:true});
  });
  hook('readerQuestionRun',askCurrentPaper);

  $('latestReport').addEventListener('click',()=>{if(!$('latestReport').hidden)markGuideStep('report')});
  $('reportList').addEventListener('click',event=>{if(event.target.closest('a.report-download'))markGuideStep('report')});
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
  hook('installUpdate',confirmAndInstall);hook('installUpdateSettings',confirmAndInstall);
  hook('welcomeSkip',finishWelcome);hook('welcomeNext',()=>{if(welcomeStep<welcomeSteps.length-1){welcomeStep++;renderWelcome()}else finishWelcome()});
  $('welcomeWizard').addEventListener('cancel',e=>{e.preventDefault();finishWelcome()});
  hook('checkUpdate',async()=>{try{const out=await api('/api/update/check',{});notice(out.started?'已在后台检查公开 Release':'已有更新检查正在运行');renderUpdate(out.status)}catch(e){notice(e.message,true)}});
  hook('saveUpdateSettings',async()=>{try{await save();await updateState();notice('更新检查设置已保存')}catch(e){notice(e.message,true)}});

  $('aiResearchGoal').addEventListener('input',()=>{
    proposedPlan=null;$('aiPlanResult').hidden=true;$('aiPlanStatus').textContent='';
  });
  $('reportList').addEventListener('click',e=>{const b=e.target.closest('.preview-md');if(b)showMarkdownPreview(b.dataset.name)});
  $('fulltextExperimentsResults').addEventListener('click',event=>{
    const btn=event.target.closest('.local-experiment-trace');
    if(!btn||!localPaperTrace)return;
    localPaperTrace.setAnchor({quote:btn.dataset.quote,page:Number(btn.dataset.page),
      experimentId:btn.dataset.id});
  });
  hook('fulltextClose',()=>$('fulltextDialog').close());
  hook('fulltextIndex',()=>fulltextTask('index_fulltext'));
  hook('fulltextAnalyze',()=>fulltextTask('analyze_fulltext'));
  hook('fulltextExperiments',()=>fulltextTask('analyze_experiments'));
  hook('fulltextSearch',searchFulltext);
  $('fulltextClaims').addEventListener('click',async event=>{
    const button=event.target.closest('.quote-location');if(!button||!activeFulltextId)return;
    const page=Number(button.dataset.page), quote=button.dataset.quote;
    const query=new URLSearchParams({paper_id:activeFulltextId,page:String(page),quote});
    button.disabled=true;
    try{
      const position=await api('/api/fulltext/locate?'+query);
      if(!position.matched){
        if(position.text_matched){
          moveLocalPdfPage(page,{push:true});
          notice('已在当前 PDF 找到引文文字，但词序坐标存在歧义，已跳转至原文页；请人工核对。');
        }else notice('当前 PDF 文字层未找到完整引文，请打开原文核对。',true);
        return;
      }
      const panel=$('fulltextHighlightPanel');panel.hidden=false;panel.open=true;
      const formula=position.formula_context;
      const note=$('fulltextEvidenceContext');
      if(note)note.textContent=formula?.nearby_lines?.length?
        '检测到公式上下文候选，仍需人工核对公式含义和引用关系。':'文本坐标已定位；表格数值与实验条件需要人工核对。';
      $('fulltextHighlightImage').src='/api/fulltext/highlight?'+query.toString();
      moveLocalPdfPage(page,{push:true});panel.scrollIntoView({block:'nearest',behavior:'smooth'});
    }catch(e){notice('原文高亮不可用：'+e.message,true)}finally{button.disabled=false}
  });
  hook('fulltextPageTables',async()=>{
    if(!activeFulltextId)return;
    const output=$('fulltextTableResults');output.textContent='正在检查本页表格…';
    const query=new URLSearchParams({paper_id:activeFulltextId,page:String(localPaperPage)});
    try{
      const response=await api('/api/fulltext/tables?'+query);
      output.innerHTML=response.tables.length?response.tables.map(table=>
        `<article class="evidence-card"><strong>表格 ${table.id} · ${table.row_count} 行 × ${table.column_count} 列（位置候选，未核实数值关系）</strong><div style="overflow-x:auto"><table><thead><tr><th>行 / 列</th>${Array.from({length:table.column_count},(_,i)=>`<th>列 ${i+1}</th>`).join('')}</tr></thead><tbody>${table.rows.map((row,index)=>
        `<tr><th scope="row">行 ${index+1}</th>${row.map((cell,c)=>`<td title="行 ${index+1}，列 ${c+1}">${escape(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div><small class="hint">合并单元格内可能包含多个模型和数值，请对照原始 PDF 判定。</small></article>`).join('')
        :'<p class="hint">本页未检测到可靠的规则线表格结构，可直接查看 PDF 原文。</p>';
    }catch(e){output.textContent='无法读取本页表格：'+e.message}
  });
  hook('fulltextCopy',copyFulltextCard);
  $('fulltextSearchInput').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();searchFulltext()}});
  $('fulltextDialog').addEventListener('click',e=>{if(e.target===$('fulltextDialog')){$('fulltextDialog').close()}});
  $('fulltextDialog').addEventListener('close',()=>{
    if(ignoredLocalClose.fulltext>0){ignoredLocalClose.fulltext--;return;}
    const matching=localRoute()==='library' && R.get('paper')===activeFulltextId;
    activeFulltextId='';fulltextData=null;localPaperTrace=null;
    if(matching && !localRestoring)syncLocalLink({push:true});
  });
  for(const id of ['fulltextClaims','fulltextExperimentsResults','fulltextMatches']){
    $(id).addEventListener('click',event=>{
      const link=event.target.closest('a[href^="/api/pdf/"]');
      if(!link||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
      event.preventDefault();
      const address=new URL(link.href,location.href);
      const id=decodeURIComponent(address.pathname.slice('/api/pdf/'.length));
      const page=Number(new URLSearchParams(address.hash.slice(1)).get('page'))||1;
      if(id===activeFulltextId)moveLocalPdfPage(page,{push:true});
      else openFulltext(id,{page}).catch(e=>notice(e.message,true));
    });
  }
  hook('markdownClose',()=>$('markdownDialog').close());
  $('markdownDialog').addEventListener('click',e=>{if(e.target===$('markdownDialog'))$('markdownDialog').close()});
  $('markdownDialog').addEventListener('close',()=>{
    localReportNonce++;
    if(ignoredLocalClose.markdown>0){ignoredLocalClose.markdown--;return;}
    if(localRoute()==='reports' && R.get('report')===activeReport && !localRestoring)syncLocalLink({push:true});
  });
  $('markdownRendered').addEventListener('scroll',()=>{
    clearTimeout(localLinkTimer);localLinkTimer=setTimeout(()=>syncLocalLink(),140);
  },{passive:true});
  hook('sharePageLocal',async()=>{syncLocalLink();try{await R.share();notice('链接已复制（本地地址仅当前电脑可访问）')}catch{notice('请从地址栏复制链接',true)}});
  hook('shareFulltextLocal',async()=>{syncLocalLink();try{await R.share();notice('论文页码链接已复制（仅本机可打开）')}catch{notice('请从地址栏复制链接',true)}});
  hook('shareMarkdownLocal',async()=>{syncLocalLink();try{await R.share();notice('报告阅读位置链接已复制（仅本机可打开）')}catch{notice('请从地址栏复制链接',true)}});
  $('fulltextPdfPage').addEventListener('change',()=>moveLocalPdfPage($('fulltextPdfPage').value,{push:true}));
  hook('fulltextPagePrev',()=>moveLocalPdfPage(localPaperPage-1,{push:true}));
  hook('fulltextPageNext',()=>moveLocalPdfPage(localPaperPage+1,{push:true}));
  hook('markdownCopy',async()=>{try{await navigator.clipboard.writeText(markdownRaw);notice('Markdown 已复制')}catch{notice('复制失败，可下载原文件',true)}});
  hook('refreshAgents',loadAgents);
  hook('taskDockToggle',()=>setTaskDockExpanded($('taskCenter').dataset.expanded!=='true'));
  hook('taskDockDismiss',()=>{
    if($('taskCenter').dataset.outcome==='running')return;
    dismissedTaskMarker=taskMarker(lastTaskSnapshot||{});
    $('taskCenter').hidden=true;
    setTaskDockExpanded(false);
  });
  hook('taskDockOpenTasks',()=>{setTaskDockExpanded(false);visible('tasks')});
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape' && $('taskCenter').dataset.expanded==='true' && !document.querySelector('dialog[open]')){
      setTaskDockExpanded(false);$('taskDockToggle').focus();
    }
  });
  setTaskDockExpanded(false);
  hook('runSearch',()=>job('search'));hook('runAllTopics',()=>job('search_all'));hook('refreshDownloads',loadDownloads);hook('saveDownloadSettings',async()=>{try{await save()}catch(e){notice(e.message,true)}});hook('runExport',()=>job('export'));hook('exportReports',()=>job('export'));hook('exportAll',()=>job('export_all'));hook('exportExperiments',()=>job('export_experiments'));hook('generateSynthesis',()=>job('synthesis'));hook('testApi',async()=>{
    if(!window.confirm('连接测试会向所选模型服务发送一条固定测试文字，可能产生少量费用。继续吗？'))return;
    try{await saveAiOnly({silent:true});await api('/api/job',{mode:'test_api',confirm_remote_test:true});
      notice('已开始测试模型连接，结果可在任务记录中查看。');await updateState();
    }catch(e){notice('连接测试未启动：'+e.message,true)}
  });hook('saveSettings',async()=>{try{await save()}catch(e){notice(e.message,true)}});hook('saveAiSettings',async()=>{try{await saveAiOnly()}catch(e){notice(e.message,true)}});
  hook('storageRefresh',async()=>{await updateState();await refreshStorageHealth()});
  hook('storageRetry',async()=>{await updateState();await refreshStorageHealth()});
  hook('libraryMore',()=>loadLibrary(true));hook('downloadFilter',loadDownloads);hook('installTask',()=>scheduleAction('install'));hook('removeTask',()=>scheduleAction('remove'));hook('triggerTask',()=>scheduleAction('run'));
  hook('removeKey',async()=>{if(!confirm('删除当前服务商保存的 API Key？'))return;try{await api('/api/key/remove',{});$('keyState').textContent='尚未保存 API Key';const latest=await api('/api/state');setSettings(latest);refreshModelPreflight();notice('API Key 已删除')}catch(e){notice(e.message,true)}});
  hook('openFolder',async()=>{try{await api('/api/folder',{});}catch(e){notice(e.message,true)}});
  async function refreshHistory(){try{const info=await api('/api/history?scope=topic');const rows=info.runs||[];$('historyList').innerHTML=rows.length?rows.slice(0,10).map(r=>{const x=r.summary||{};const label=r.state==='success'?'完成':r.state==='partial'?'部分完成':'失败';return `<div class="history-row"><span>${escape((r.finished_at||'').slice(0,16).replace('T',' '))}</span><strong>${escape(r.topic_id)} · ${label}</strong><span>${r.state==='success'?`收录 ${Number(x.retrieved||0)} · 新增 ${Number(x.new||0)}`:escape(r.error||'执行失败')}</span></div>`}).join(''):'<div class="empty">暂无当前主题的运行记录</div>'}catch(err){$('historyList').textContent=err.message}}
  async function downloadRuntimeDiagnostics(){
    const include=Boolean($('includeTaskLogs')?.checked);
    try{
      const response=await fetch('/api/diagnostics/export',{method:'POST',
        headers:{'Content-Type':'application/json','X-Local-Token':TOKEN},
        body:JSON.stringify({include_task_logs:include})});
      if(!response.ok)throw new Error('无法生成日志包，请尝试 start_windows.bat --diagnostics');
      const archive=await response.blob();
      const link=document.createElement('a');const objectUrl=URL.createObjectURL(archive);
      link.href=objectUrl;link.download='Paper-Radar-Runtime-Diagnostics.zip';
      document.body.appendChild(link);link.click();link.remove();
      setTimeout(()=>URL.revokeObjectURL(objectUrl),4000);
      notice('运行日志包已下载。分享附加任务日志之前请检查研究内容。');
    }catch(error){notice('日志下载失败：'+error.message,true)}
  }
  async function refreshLogs(){
    try{
      const selected=$('logSource').value;
      const [info,summary]=await Promise.all([
        api('/api/logs?source='+selected),api('/api/diagnostics/summary')]);
      $('logBox').textContent=info[selected]||'暂无日志。';
      const r=summary.summary||{};
      const issues=(r.errors_by_code||[]).slice(0,5).map(x=>`${x.code} ${x.count} 次`).join('；');
      $('diagnosticSummary').textContent=`最近 ${r.sampled_events||0} 条日志：请求错误 ${r.requests_http_4xx_5xx||0} 次，模型异常 ${r.model_calls_failed||0} 次，客户端中断 ${r.client_disconnections||0} 次，超过 5 秒的请求 ${r.slow_requests_over_5s||0} 次。${issues?'主要错误：'+issues+'。':'暂无已分类错误。'}统计只覆盖最近记录。`;
      const target=$('diagnosticResearchRuns');
      if(target){
        target.replaceChildren();
        const runs=(r.research_runs||[]).slice(-6).reverse();
        if(!runs.length){target.textContent='暂无研究分析阶段记录；新版日志会从下一次分析开始记录。';}
        for(const run of runs){
          const item=document.createElement('div');item.className='diagnostic-run';target.appendChild(item);
          const heading=document.createElement('strong');heading.textContent='任务 '+run.run_id+' · '+
            (run.outcome_code==='CANDIDATE_ONLY'?'题录分析完成':run.outcome_code==='CITED_EVIDENCE'?'包含已核对证据':'尚未完成');item.appendChild(heading);
          const counts=run.counts||{},model=(run.model_code||'');
          const note=document.createElement('p');note.textContent=`收录 ${counts.papers??'—'} 篇 · 候选 ${counts.candidate_papers??'—'} 篇 · 原文证据 ${counts.anchors??'—'} 条 · `+
            (model==='MODEL_USED'?'模型已使用':model==='MODEL_DISABLED'?'未启用模型':model==='MODEL_SKIPPED'?'模型没有可处理材料':model.startsWith('MODEL_')?'模型环节异常：'+model:'模型状态待检查');item.appendChild(note);
          const stages=document.createElement('small');stages.textContent=(run.stages||[]).map(s=>s.stage+': '+s.status+' '+s.duration_ms+'ms').join('  |  ')||'暂无阶段记录';item.appendChild(stages);
        }
      }

    }catch(e){$('diagnosticSummary').textContent='运行概况暂不可用';notice(e.message,true)}
  }
  hook('runtimeErrorDownload',downloadRuntimeDiagnostics);hook('runtimeErrorClose',()=>{$('runtimeErrorBanner').hidden=true});hook('runtimeErrorFix',openAiSetup);
  hook('refreshLogs',refreshLogs);hook('downloadRuntimeLogsTop',downloadRuntimeDiagnostics);hook('downloadRuntimeLogsSettings',downloadRuntimeDiagnostics);hook('downloadRuntimeLogsDock',downloadRuntimeDiagnostics);hook('taskViewRefresh',loadTaskView);hook('refreshHistory',refreshHistory);refreshHistory();$('logSource').addEventListener('change',refreshLogs);hook('shutdown',async()=>{if(!confirm('关闭本地工作台？系统定时任务仍会保留。'))return;await api('/api/shutdown',{});notice('本地工作台已退出，可以关闭此页面。')});
  $('apiProvider').addEventListener('change',()=>{const preset=providers[$('apiProvider').value];if(preset){$('apiBase').value=preset[0];$('apiModel').value=preset[1]}$('apiKey').value='';$('keyState').textContent='切换服务商后请保存或填写新密钥';refreshProviderLabels();refreshAiSetupState()});
  $('libraryScope').addEventListener('change',()=>{selectedPapers.clear();if($('libraryScope').value==='all'&&['inbox','skipped','actionable'].includes($('libraryFilter').value))$('libraryFilter').value='all';syncLocalLink({push:true});loadLibrary();loadRecent()});
  $('libraryGroup').addEventListener('change',()=>{renderLibraryGroups();syncLocalLink({push:true})});
  $('librarySort').addEventListener('change',()=>{syncLocalLink({push:true});loadLibrary()});
  $('quickMaxResults').addEventListener('input',()=>{$('maxResults').value=$('quickMaxResults').value});$('maxResults').addEventListener('input',()=>{$('quickMaxResults').value=$('maxResults').value});$('quickSort').addEventListener('change',()=>{$('sortMode').value=$('quickSort').value});$('sortMode').addEventListener('change',()=>{$('quickSort').value=$('sortMode').value});
  $('sourceOptions').addEventListener('change',()=>{updateSourceSelection();updateSourceHelp();const checked=[...document.querySelectorAll('.source-option:checked')].map(n=>n.value);const missing=sourceDefinitions.filter(x=>checked.includes(x.name)&&x.access!=='public'&&!sourceAccess[x.name]?.key);if(missing.length)notice('已选择需授权来源：'+missing.map(x=>x.label).join('、')+'。请先在设置中保存授权 API Key。',true);});$('sourceAccessHelp').addEventListener('click',e=>{if(e.target.closest('.source-guide-config')){visible('settings');$('settings-sources').open=true;$('publisherPanel').scrollIntoView({behavior:'smooth',block:'start'});}});
  hook('openSourceSettings',()=>{visible('settings');$('settings-sources').open=true;$('settings-sources').scrollIntoView({behavior:'smooth',block:'start'})});
  hook('refreshProviderUsage',refreshProviderUsage);
  hook('modelOfflineCheck',async()=>{try{await saveAiOnly({silent:true});await refreshModelPreflight()}catch(err){notice(err.message,true)}});
  $('modelEstimateAction').addEventListener('change',refreshModelPreflight);
  $('llmConsent').addEventListener('change',()=>{const text=$('llmConsent').checked?'授权尚未保存，请点击保存设置。':'撤销授权尚未保存，请点击保存设置。';$('modelStatusText').textContent=text});
  async function savePublisher(name,fields){try{for(const [field,id] of fields){const val=$(id).value.trim();if(val){await api('/api/source-credential',{source:name,field,value:val});$(id).value='';}}const res=await api('/api/state');sourceAccess=res.source_access||{};updateSourceHelp();$('openalexState').textContent=sourceAccess.openalex?.key?'已保存':'未配置';$('springerState').textContent=sourceAccess.springer?.key?'已保存':'未配置';$('elsevierState').textContent=sourceAccess.elsevier?.key?'已保存':'未配置';notice('授权信息已保存到系统凭据管理器')}catch(e){notice(e.message,true)}}
  hook('saveOpenAlex',()=>savePublisher('openalex',[['key','openalexKey']]));hook('saveSpringer',()=>savePublisher('springer',[['key','springerKey']]));hook('saveElsevier',()=>savePublisher('elsevier',[['key','elsevierKey'],['insttoken','elsevierToken']]));
  hook('checkOpenAlexQuota',async()=>{
    const button=$('checkOpenAlexQuota'),result=$('openalexQuotaStatus');
    button.disabled=true;result.textContent='正在读取 OpenAlex 官方额度…';
    try{
      const info=await api('/api/openalex/quota');
      const parts=[info.message||'状态未知'];
      if(info.rate_limit_remaining!==undefined)parts.push(`剩余额度 ${info.rate_limit_remaining}`);
      if(info.rate_limit_reset_seconds!==undefined)parts.push(`约 ${Math.ceil(info.rate_limit_reset_seconds/60)} 分钟后重置`);
      if(info.retry_after_seconds!==undefined)parts.push(`服务商建议 ${info.retry_after_seconds} 秒后再试`);
      result.textContent=parts.join(' · ');
    }catch(err){result.textContent='额度检查失败：'+err.message}
    finally{button.disabled=false}
  });
  $('librarySearch').addEventListener('input',()=>{clearTimeout(window.searchTimer);window.searchTimer=setTimeout(()=>{syncLocalLink();loadLibrary()},260)});
  $('libraryFilter').addEventListener('change',()=>{if(['inbox','skipped','actionable'].includes($('libraryFilter').value))$('libraryScope').value='topic';syncLocalLink({push:true});loadLibrary()});
  function keepLocalNoteDraft(input){
    const panel=input.closest('.local-reading-note');if(!panel)return;
    const button=panel.querySelector('.local-note-save');
    const key=($('activeTopic').value||'default')+':'+button.dataset.id;
    localReadingDrafts.set(key,{note:panel.querySelector('.local-note-input').value,
      action:panel.querySelector('.local-note-action').value,
      revision:localReadingDrafts.get(key)?.revision??Number(button.dataset.revision)});
    panel.dataset.dirty='true';
    panel.querySelector('.local-note-discard').disabled=false;
    panel.querySelector('.local-note-feedback').textContent='草稿尚未保存';
  }
  $('libraryList').addEventListener('input',e=>{if(e.target.matches('.local-note-input'))keepLocalNoteDraft(e.target)});
  window.addEventListener('beforeunload',event=>{if(localReadingDrafts.size){event.preventDefault();event.returnValue='';}});
  $('libraryList').addEventListener('click',async e=>{
    const discardButton=e.target.closest('button.local-note-discard');
    if(discardButton){
      const panel=discardButton.closest('.local-reading-note');
      const button=panel.querySelector('.local-note-save');
      const key=($('activeTopic').value||'default')+':'+button.dataset.id;
      localReadingDrafts.delete(key);
      const paper=libraryPapers.find(x=>x.paper_id===button.dataset.id);
      panel.querySelector('.local-note-input').value=paper?.note||'';
      panel.querySelector('.local-note-action').value=paper?.next_action||'none';
      button.dataset.revision=String(paper?.note_revision||0);
      discardButton.disabled=true;panel.dataset.dirty='false';
      panel.querySelector('.local-note-feedback').textContent='已丢弃草稿；其他窗口修改请刷新查看';
      return;
    }
    const noteButton=e.target.closest('button.local-note-save');
    if(noteButton){
      const panel=noteButton.closest('.local-reading-note');
      const note=panel.querySelector('.local-note-input').value;
      const action=panel.querySelector('.local-note-action').value;
      noteButton.disabled=true;
      try{
        const result=await api('/api/paper/note',{id:noteButton.dataset.id,note,next_action:action,expected_revision:Number(noteButton.dataset.revision)});
        noteButton.dataset.revision=String(result.note_revision);
        localReadingDrafts.delete(($('activeTopic').value||'default')+':'+result.paper_id);
        panel.querySelector('.local-note-discard').disabled=true;
        panel.dataset.dirty='false';
        panel.querySelector('.local-note-feedback').textContent='已保存至当前主题 · '+(result.note_updated_at||'');
        const paper=libraryPapers.find(x=>x.paper_id===result.paper_id);
        if(paper)Object.assign(paper,result);
        notice('阅读笔记已保存');markGuideStep('read');
      }catch(err){panel.querySelector('.local-note-feedback').textContent='保存失败：'+err.message+'；草稿仍在页面';notice(err.message,true)}
      finally{noteButton.disabled=false}
      return;
    }
    const abstractBtn=e.target.closest('button.abstract-refresh');
    if(abstractBtn){
      abstractBtn.disabled=true;abstractBtn.textContent='正在核对公开索引…';
      try{
        const r=await api('/api/papers/abstract/refresh',{paper_id:abstractBtn.dataset.id});
        if(r.status==='updated'||r.status==='already_present'){
          const item=libraryPapers.find(p=>p.paper_id===abstractBtn.dataset.id);
          if(item)item.abstract=r.abstract;
          notice('原始摘要已补全，来源：'+(r.source||'已收录元数据'));
          await loadLibrary();
        }else{abstractBtn.textContent='来源暂无摘要';notice(r.reason||'公开来源暂未提供摘要');}
      }catch(err){abstractBtn.disabled=false;abstractBtn.textContent='重新补取摘要';notice(err.message,true);}
      return;
    }
    const evidenceBtn=e.target.closest('button.fulltext-open');if(evidenceBtn){await openFulltext(evidenceBtn.dataset.id);return;}const feedbackBtn=e.target.closest('button[data-feedback]');if(feedbackBtn){try{await api('/api/paper',{id:feedbackBtn.dataset.id,feedback:Number(feedbackBtn.dataset.feedback)});await loadLibrary();notice('已保存当前主题的相关性反馈')}catch(err){notice(err.message,true)}return;}const pdf=e.target.closest('.pdf-button');if(pdf){await downloadPapers([pdf.dataset.id]);return;}const target=e.target.closest('button[data-action="star"]');if(!target)return;try{await api('/api/paper',{id:target.dataset.id,starred:target.dataset.next==='true'});markGuideStep('read');await loadLibrary()}catch(e){notice(e.message,true)}});
  $('libraryList').addEventListener('change',async e=>{if(e.target.matches('.local-note-action')){keepLocalNoteDraft(e.target);return;}if(e.target.matches('.paper-check')){if(e.target.checked)selectedPapers.add(e.target.dataset.id);else selectedPapers.delete(e.target.dataset.id);$('selectedCount').textContent=selectedPapers.size?`已选 ${selectedPapers.size} 篇`:'未选择论文';return;}const target=e.target.closest('.read-select');if(!target)return;try{await api('/api/paper',{id:target.dataset.id,status:target.value});notice('阅读状态已更新');markGuideStep('read')}catch(e){notice(e.message,true)}});
  async function localBulk(action){
    const ids=[...selectedPapers];
    if(!ids.length){notice('请先勾选要处理的论文',true);return;}
    if(ids.length>100){notice('每次最多批量处理 100 篇',true);return;}
    if($('libraryScope').value!=='topic'){notice('批量操作只支持当前研究主题，请先把论文范围切换为“当前研究主题”',true);return;}
    if(action==='skip'&&!confirm(`将 ${ids.length} 篇论文标为暂不关注？`))return;
    try{
      const result=await api('/api/papers/bulk',{paper_ids:ids,action});
      await loadLibrary();
      notice(`已处理 ${result.updated} 篇论文`);
    }catch(err){notice('批量处理失败：'+err.message,true)}
  }
  hook('localBulkStar',()=>localBulk('star'));
  hook('localBulkSkip',()=>localBulk('skip'));
  hook('localBulkDone',()=>localBulk('done'));
  hook('localExportStarred',()=>{window.location.href='/api/papers/reading-export?scope=starred&format=md'});
  hook('localExportActions',()=>{window.location.href='/api/papers/reading-export?scope=actionable&format=csv'});
  $('downloadList').addEventListener('click',e=>{const el=e.target.closest('button.retry-download');if(el)downloadPapers([el.dataset.id])});
  hook('downloadSelected',()=>downloadPapers([...selectedPapers]));
  hook('downloadStarred',async()=>{try{const data=await api('/api/papers?scope=topic&starred=1&limit=21');if(data.total>20){notice(`共有 ${data.total} 篇星标论文。每次最多下载 20 篇，请先筛选。`,true);return}await downloadPapers(data.papers.map(p=>p.paper_id))}catch(e){notice(e.message,true)}});
  document.querySelectorAll('[data-focus]').forEach(button=>button.addEventListener('click',()=>{$(button.dataset.focus).focus();$(button.dataset.focus).scrollIntoView({block:'center',behavior:'smooth'})}));
  hook('themeToggle' ,()=>{const dark=document.documentElement.dataset.theme==='dark';document.documentElement.dataset.theme=dark?'light':'dark';try{localStorage.setItem('lt-theme',dark?'light':'dark')}catch{}});
  try{if(localStorage.getItem('lt-theme')==='dark')document.documentElement.dataset.theme='dark'}catch{}
  $('clock').textContent=new Date().toLocaleDateString('zh-CN');
  loadBootstrap().then(async()=>{
    await updateState(true);
    await refreshStorageHealth();
    await restoreLocalRoute();
    clearLocalConnectionError();
    if(storageReady)await loadRecent();
  }).catch(e=>{
    bootstrapReady=false;
    $('runSearch').disabled=true;
    $('searchAvailability').textContent='设置加载失败，请刷新页面';
    showLocalConnectionError(e.message);
    notice('工作台设置加载失败：'+e.message,true);
  });
  // Idle workspaces should not re-read cloud-synced settings every 2.3 s.
  // Keep responsive progress while a task runs; manual actions refresh at once.
  async function pollStatus(){
    await updateState();
    window.setTimeout(pollStatus,jobWasBusy?2600:12000);
  }
  window.setTimeout(pollStatus,12000);
  async function refreshProcess(){try{const info=await api('/api/process');$('processState').textContent=`${info.current_mode==='foreground'?'前台':'后台'} · PID ${info.pid} · 端口 ${info.port}`+(info.maintenance?' · 准备升级':'')}catch{$('processState').textContent='服务状态暂不可读取'}}
  refreshProcess();setInterval(refreshProcess,9000);setInterval(pollHandoff,2200);
});

// Zotero profile actions. These controls are available on the dedicated Zotero page.
let zoteroNotePreview=null;
let zoteroPullPlan=null;
$('zoteroSyncPaper').addEventListener('input',()=>{zoteroNotePreview=null;$('zoteroSyncConfirm').disabled=true;zoteroPullPlan=null;$('zoteroPullConfirm').disabled=true});
$('zoteroSyncPreview').addEventListener('click',async()=>{
  const id=$('zoteroSyncPaper').value.trim();if(!id){notice('请输入本地论文 ID',true);return}
  try{
    const result=await api('/api/zotero/web-sync',{paper_id:id,stage:'preview'});
    zoteroNotePreview=result;$('zoteroSyncConfirm').disabled=false;
    $('zoteroSyncState').textContent=`找到 Zotero 条目「${result.zotero_title}」，将新建 ${result.note_length} 字的阅读笔记。`;
  }catch(e){zoteroNotePreview=null;$('zoteroSyncConfirm').disabled=true;$('zoteroSyncState').textContent=e.message;notice(e.message,true)}
});
$('zoteroSyncConfirm').addEventListener('click',async()=>{
  const plan=zoteroNotePreview;if(!plan)return;
  if(!confirm(`在 Zotero「${plan.zotero_title}」下新建一条阅读笔记？不会覆盖已有条目。`))return;
  $('zoteroSyncConfirm').disabled=true;
  try{
    const result=await api('/api/zotero/web-sync',{paper_id:plan.paper_id,stage:'confirm',
      parent:plan.zotero_parent,library_version:plan.library_version,fingerprint:plan.fingerprint});
    $('zoteroSyncState').textContent=`已写入 Zotero 子笔记：${result.zotero_note_key||'完成'}`;
    notice('阅读笔记已写入 Zotero');
  }catch(e){$('zoteroSyncState').textContent='未写入：'+e.message;notice(e.message,true)}
  finally{zoteroNotePreview=null}
});
$('zoteroPullPreview').addEventListener('click',async()=>{
  const id=$('zoteroSyncPaper').value.trim();if(!id)return notice('请输入论文 ID',true);
  zoteroPullPlan=null;$('zoteroPullConfirm').disabled=true;
  try{
    const result=await api('/api/zotero/web-sync',{paper_id:id,stage:'import_preview'});
    zoteroPullPlan=result;$('zoteroPullConfirm').disabled=false;
    $('zoteroPullState').textContent=`Zotero 笔记：${result.length} 字。片段：${result.preview}。本地已有 ${result.existing_note_length} 字，将在确认后合并。`;
  }catch(e){$('zoteroPullState').textContent='读取失败：'+e.message;notice(e.message,true)}
});
$('zoteroPullConfirm').addEventListener('click',async()=>{
  const plan=zoteroPullPlan;if(!plan)return;
  if(!confirm('确认将预览的 Zotero 笔记添加到本地论文笔记？已有内容会保留。'))return;
  $('zoteroPullConfirm').disabled=true;
  try{
    const result=await api('/api/zotero/web-sync',{
      paper_id:plan.paper_id,stage:'import_confirm',parent:plan.zotero_parent,
      note_key:plan.zotero_note_key,remote_fingerprint:plan.remote_fingerprint,
      local_revision:plan.local_revision});
    $('zoteroPullState').textContent=result.updated?'已合并到本地阅读笔记。':'笔记已存在，无需重复合并。';
    notice('Zotero 笔记同步完成');
  }catch(e){$('zoteroPullState').textContent='导入未完成：'+e.message;notice(e.message,true)}
  finally{zoteroPullPlan=null;}
});
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

// An explicit connectivity check. Never probe public APIs during page refresh.
$('sourceConnectivityCheck').addEventListener('click', checkSourceConnectivity);
