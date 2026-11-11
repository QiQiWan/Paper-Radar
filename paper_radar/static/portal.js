'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let csrf = '', currentUser = null, currentTopic = 'default', lastReport = '';
  let openedReportId = '', displayedJobId = 0;
  let register = false, registrationEnabled = true;
  let jobTimer = null, lastFinishedJob = null, jobFailures = 0, jobRefreshInFlight = false;
  let paperOffset=0, paperTopic='', paperQueryNonce=0, paperSearchTimer=null;
  const triageSelected=new Set(); let loadedPaperIds=[];
  const readingDrafts=new Map(); // page memory only; never written to a URL or browser storage
  $('logout').addEventListener('click',()=>readingDrafts.clear(),{capture:true});
  window.addEventListener('beforeunload',event=>{if(readingDrafts.size){event.preventDefault();event.returnValue='';}});
  let focusedPaperId='', readerPaperId='', readerPage=1;
  let paperTrace=null,topicTrace=null;
  let portalRestoring=false, portalLinkTimer=null, readerNonce=0, routeNonce=0, reportNonce=0;
  const R=window.PRReading;
  const UI=window.PRUI;
  let currentView='workspace', currentTopics=[], lastJob=null,publicInquiry=null;
  const alertUser = (message, error = false) => {
    $('message').textContent = message;
    $('message').className = 'message' + (error ? ' error' : '');
    $('message').hidden = !message;
  };
  const node = (tag, text, parent, cls = '') => {
    const e = document.createElement(tag);
    e.textContent = String(text || ''); if (cls) e.className = cls;
    parent.appendChild(e); return e;
  };
  async function api(path, method = 'GET', body = null) {
    const options = {method, credentials:'same-origin', headers:{}};
    if (method !== 'GET') {
      options.headers['Content-Type'] = 'application/json';
      options.headers['X-Portal-Action'] = '1';
      if (csrf) options.headers['X-CSRF-Token'] = csrf;
      options.body = JSON.stringify(body || {});
    }
    let result;
    try { result = await fetch(path, options); }
    catch (_) { throw new Error('网络连接失败。请检查网络后刷新任务状态；如刚提交任务，请先确认任务是否已开始。'); }
    let data; try { data = await result.json(); } catch (_) { data = {}; }
    if (!result.ok) {
      const advice=UI.requestMessage(result.status,data.error,'public');
      const safeRequestId=typeof data.request_id==='string'&&/^[a-f0-9]{8,32}$/i.test(data.request_id)?data.request_id:'';
      const error = new Error(result.status>=500&&safeRequestId?`${advice} 错误编号：${safeRequestId}`:advice);
      error.status = result.status;
      throw error;
    }
    return data;
  }
  const stageLabels={starting:'准备执行',discovery:'发现论文',download:'准备 PDF',
    fulltext:'解析全文',evidence:'核验实验',synthesis:'生成报告',task:'整个任务'};
  const stageStatus={started:'正在执行',completed:'已完成',failed:'失败',cancelled:'已取消'};
  const taskKinds={search:'公开检索',research:'完整科研分析',analyze:'单篇 PDF 分析',synthesize:'跨论文比较'};
  const views={workspace:['首页','WORKSPACE'],library:['我的文献','LIBRARY'],
    tasks:['找论文','SEARCH'],following:['论文更新','FOLLOWING'],inquiry:['研究分析','INQUIRY'],reports:['我的报告','REPORTS'],account:['设置','SETTINGS']};
  function routeFromHash() {
    const parts=window.location.hash.replace(/^#/,'').split('/');
    const legacy=Object.hasOwn(views,parts[0]);
    const path=window.location.pathname.replace(/^\//,'').replace(/\/$/,'');
    const selected=path==='settings'?'account':path;
    const view=Object.hasOwn(views,selected)?selected:(legacy?parts[0]:'workspace');
    const query=R.get('topic')||'';
    const topic=query||(legacy?parts[1]:'');
    return {view,topic:/^[a-z0-9_-]{1,48}$/.test(topic)?topic:''};
  }
  let publicFollowData=null;
  async function loadPublicFollowing(){
    const result=await api('/api/public/following?topic_id='+encodeURIComponent(currentTopic));
    publicFollowData=result;
    const sources=$('publicFollowingSources'), events=$('publicFollowingEvents');
    sources.replaceChildren();events.replaceChildren();
    for(const row of result.checkpoints||[]){
      node('div',row.source_name+' · '+(row.status==='complete'?'已完成':'待补检')+
           ' · 上次成功：'+(row.last_success||'暂无')+
           (row.error_code?' · '+row.error_code:''),sources,'follow-row');
    }
    if(!result.checkpoints?.length)node('p','暂无来源检查记录，完成一次检索后会显示。',sources,'hint');
    $('publicFollowingUnread').textContent='未读：'+(result.unread||0)+' 条';
    for(const row of result.events||[]){
      node('div',row.title+' · '+row.source_name+' · '+row.kind+
           ' · '+row.observed_at,events,'follow-row');
    }
    if(!result.events?.length)node('p','暂无论文变化。',events,'hint');
    $('publicFollowNew').checked=!!result.preferences.notify_new;
    $('publicFollowUpdated').checked=!!result.preferences.notify_updated;
  }
  $('publicFollowingRefresh').addEventListener('click',()=>loadPublicFollowing().catch(e=>alertUser(e.message,true)));
  $('publicFollowingSave').addEventListener('click',async()=>{
    try{
      await api('/api/public/following/preferences','POST',{
        topic_id:currentTopic,preferences:{notify_new:$('publicFollowNew').checked,
                                         notify_updated:$('publicFollowUpdated').checked}});
      await loadPublicFollowing();alertUser('提醒设置已保存');
    }catch(err){alertUser(err.message,true);}
  });
  $('publicFollowingSeen').addEventListener('click',async()=>{
    try{
      if(!publicFollowData)return;
      const ids=(publicFollowData.events||[]).filter(r=>r.notification_id && !r.seen_at).map(r=>r.id);
      if(ids.length)await api('/api/public/following/seen','POST',{topic_id:currentTopic,event_ids:ids});
      await loadPublicFollowing();
    }catch(err){alertUser(err.message,true);}
  });
  function reportProgress(){
    if(!$('reportReader') || $('reportReader').hidden || !$('reportContent'))return 0;
    const area=$('reportContent');
    const top=area.getBoundingClientRect().top+window.scrollY;
    const span=Math.max(1,area.offsetHeight-window.innerHeight+120);
    return Math.min(10000,Math.max(0,Math.round((window.scrollY-top)/span*10000)));
  }
  function publicLinkState(view=currentView){
    const state={topic:currentTopic};
    if(view==='library'){
      Object.assign(state,{q:$('paperSearch').value,filter:$('paperFilter').value,sort:$('paperSort').value});
      if(focusedPaperId)state.focus=focusedPaperId;
      if(readerPaperId && $('fulltextArea').open){state.paper=readerPaperId;state.page=readerPage;}
    }else if(view==='reports' && openedReportId && !$('reportReader').hidden){
      state.report=openedReportId;state.pos=reportProgress();
    }
    return state;
  }
  function syncPublicLink({push=false}={}){
    if(portalRestoring)return;
    R.write(R.link('public',currentView,publicLinkState()),{push});
  }
  function updatePortalHelp(view=currentView){
    $('contextHelpPortal').href=R.help('public',view);
  }
  function navigate(view,{push=true,scroll=true}={}){
    if(!Object.hasOwn(views,view))view='workspace';
    currentView=view;
    // Settings already contains full model guidance; avoid repeating the banner there.
    const aiBanner=$('portalAiBanner');
    if(aiBanner)aiBanner.hidden=view==='account'||publicAiReady===true;
    if($('primaryNav').querySelector('.portal-side-more[open]'))$('primaryNav').querySelector('.portal-side-more[open]').open=false;
    for(const page of document.querySelectorAll('.portal-page'))page.hidden=page.dataset.page!==view;
    for(const button of document.querySelectorAll('#primaryNav [data-view]')){
      const active=button.dataset.view===view;
      button.classList.toggle('active',active);
      if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
    }
    $('currentPageName').textContent=views[view][0];
    $('currentPageEn').textContent=views[view][1];
    document.title=views[view][0]+' · Paper-Radar';
    const route=R.link('public',view,publicLinkState(view));
    if(!portalRestoring)R.write(route,{push});
    updatePortalHelp(view);
    if(view==='following')loadPublicFollowing().catch(e=>alertUser(e.message,true));
    if(view==='inquiry')loadPublicInquiry();
    if(scroll)window.scrollTo({top:0,behavior:'instant'});
  }
  async function applyRoute(){
    if(!currentUser)return;
    portalRestoring=true;
    const currentRoute=++routeNonce;
    const {view,topic}=routeFromHash();
    const paper=R.get('paper'),page=Number(R.get('page')||1),report=R.get('report');
    const position=Number(R.get('pos')||0);
    try{
      if(topic && !currentTopics.some(t=>t.id===topic))alertUser('当前账号无权访问该研究主题，请选择自己的主题。',true);
      if(topic && currentTopics.some(t=>t.id===topic) && currentTopic!==topic){
        currentTopic=topic;$('topicSelect').value=topic;
        lastReport='';openedReportId='';$('reportReader').hidden=true;
        readerPaperId='';$('fulltextArea').open=false;
        await Promise.all([refreshDashboard(),refreshPapers(),refreshReports()]);
        if(currentRoute!==routeNonce)return;
      }
      if(view==='library'){
        $('paperSearch').value=R.get('q');
        for(const [key,id,def] of [['filter','paperFilter','all'],['sort','paperSort','recent']]){
          const value=R.get(key)||def;
          $(id).value=[...$(id).options].some(o=>o.value===value)?value:def;
        }
        focusedPaperId=R.get('focus');
        if(!paper){readerPaperId='';$('fulltextArea').open=false;}
        await refreshPapers();
        if(currentRoute!==routeNonce)return;
      }else{
        readerPaperId='';$('fulltextArea').open=false;
      }
      if(view==='reports'){
        if(!report){openedReportId='';$('reportReader').hidden=true;}
        else await openReport(report,{scroll:false,push:false,position});
        if(currentRoute!==routeNonce)return;
      }
      navigate(view,{push:false,scroll:false});
      if(view==='library' && paper){
        await previewPaper({id:paper,title:'论文 PDF 与证据'},page,{push:false,scroll:false});
        if(currentRoute!==routeNonce)return;
      }
      if(view==='reports' && report){
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        restoreReportPosition(position);
      }
    }catch(err){alertUser('恢复页面失败：'+err.message,true);}
    finally{if(currentRoute===routeNonce){portalRestoring=false;syncPublicLink();}}
  }
  function restoreReportPosition(value){
    const area=$('reportContent');if(!area||$('reportReader').hidden)return;
    const top=area.getBoundingClientRect().top+window.scrollY;
    const span=Math.max(1,area.offsetHeight-window.innerHeight+120);
    window.scrollTo({top:Math.max(0,top+span*(Number(value||0)/10000)),behavior:'instant'});
  }
  window.addEventListener('popstate',applyRoute);
  window.addEventListener('hashchange',applyRoute);
  for(const btn of document.querySelectorAll('#primaryNav [data-view]'))
    btn.addEventListener('click',()=>navigate(btn.dataset.view));
  // Separate editing from creation so normal settings contain one short form.
  function selectTopicMode(mode='edit') {
    for(const btn of document.querySelectorAll('[data-topic-tab]')){
      const active=btn.dataset.topicTab===mode;
      btn.classList.toggle('active',active);
      btn.setAttribute('aria-selected',String(active));
    }
    for(const form of document.querySelectorAll('[data-topic-mode]'))
      form.hidden=form.dataset.topicMode!==mode;
  }
  for(const btn of document.querySelectorAll('[data-topic-tab]'))
    btn.addEventListener('click',()=>selectTopicMode(btn.dataset.topicTab));
  $('openTopicSettings').onclick=()=>{
    selectTopicMode('edit');
    navigate('account'); $('topicSetup').open=true;
    $('topicSetup').scrollIntoView({block:'start',behavior:'smooth'});
  };
  // Public search can be completed on one page. Changes here update only the active research topic.
  function fillPortalQuickSearch(){
    const item=currentTopics.find(t=>t.id===currentTopic);
    if(!item){$('portalQuickKeywords').value='';$('portalSearchSummary').textContent='请先在设置中建立研究主题。';return;}
    if(document.activeElement!==$('portalQuickKeywords'))$('portalQuickKeywords').value=(item.keywords||[]).join(', ');
    for(const input of document.querySelectorAll('[data-quick-source]')){
      input.checked=(item.sources||['arxiv']).includes(input.value);
    }
    $('portalSearchSummary').textContent=`当前主题：${item.name}。找到的论文自动保存到“我的文献”。`;
  }
  async function preparePortalSearch(){
    const item=currentTopics.find(t=>t.id===currentTopic);
    if(!item)throw Error('请先在“设置”中建立研究主题。');
    const words=$('portalQuickKeywords').value.split(/[,，;；]+/).map(s=>s.trim()).filter(Boolean);
    if(words.length<1||words.length>6)throw Error('请填写 1—6 组搜索关键词。');
    if(words.some(s=>s.length>80))throw Error('每组关键词最多 80 个字符。');
    const sources=[...document.querySelectorAll('[data-quick-source]:checked')].map(el=>el.value);
    if(!sources.length)throw Error('请至少选择一个论文网站。');
    const beforeWords=item.keywords||[],beforeSources=item.sources||['arxiv'];
    if(JSON.stringify(words)===JSON.stringify(beforeWords) && JSON.stringify(sources)===JSON.stringify(beforeSources))return;
    await api('/api/public/topics/update','POST',{topic_id:currentTopic,name:item.name,keywords:words,sources});
    item.keywords=words;item.sources=sources;fillTopicEditor();
    $('topicQuickSummary').textContent=`${item.name} · ${words.join('、')}`;
    $('portalSearchSummary').textContent='关键词和论文网站已保存，本次检索将采用新设置。';
  }
  function showModelGuide(){
    const administrator=currentUser?.role==='admin';
    $('portalAdminModelInstructions').hidden=!administrator;
    $('portalUserModelInstructions').hidden=administrator;
    $('portalModelGuideStatus').textContent=publicAiReady===true?'AI 已连接。使用模型分析前还会要求确认发送范围。':
      publicAiReady===false?'AI 尚未连接，普通检索和阅读可以继续。':'正在确认 AI 是否可用。';
    if(!$('portalModelGuide').open)$('portalModelGuide').showModal();
  }
  $('portalAiGuide').onclick=showModelGuide;
  $('portalOpenModelGuide').onclick=showModelGuide;
  $('portalModelGuideClose').onclick=()=> $('portalModelGuide').close();
  $('portalModelGuideDone').onclick=()=> $('portalModelGuide').close();
  $('portalModelGuideRefresh').onclick=async()=>{
    $('portalModelGuideStatus').textContent='正在检查连接…';
    await refreshPortalAiStatus();
    $('portalModelGuideStatus').textContent=publicAiReady===true?'AI 已连接，可以尝试研究分析。':
      '仍未发现有效的 AI 服务配置。可以继续使用普通检索和阅读。';
  };
  $('cloudAnalysis').addEventListener('change',savePublicAiPreference);
  $('quickStart').onclick=()=>navigate('tasks');
  $('homeOpenTasks').onclick=()=>navigate('tasks');
  $('homeOpenLibrary').onclick=()=>navigate('library');
  $('libraryRunSearch').onclick=()=>navigate('tasks');
  const selectedSources = mode => Array.from(document.querySelectorAll(`[data-sources="${mode}"]:checked`))
    .map(input=>input.value);
  function fillTopicEditor() {
    fillPortalQuickSearch();
    const item=currentTopics.find(t=>t.id===currentTopic);
    if(!item)return;
    $('editTopicName').value=item.name;
    $('editKeywords').value=(item.keywords||[]).join(', ');
    for(const input of document.querySelectorAll('[data-sources="edit"]'))
      input.checked=(item.sources||['arxiv']).includes(input.value);
  }
  function openPaperFromHome(paperId) {
    focusedPaperId=paperId;
    $('paperSearch').value='';$('paperFilter').value='all';
    navigate('library');
    refreshPapers().catch(e=>alertUser('打开论文失败：'+e.message,true));
  }
  const openTriageFilter = filter => {
    focusedPaperId='';readerPaperId='';$('fulltextArea').open=false;
    $('paperSearch').value='';$('paperFilter').value=filter;
    triageSelected.clear();navigate('library');
    refreshPapers().catch(e=>alertUser('加载阅读清单失败：'+e.message,true));
  };
  $('openInboxQueue').onclick=()=>openTriageFilter('inbox');
  $('openActionQueue').onclick=()=>openTriageFilter('actionable');
  $('openReadingQueue').onclick=()=>{
    focusedPaperId='';$('paperSearch').value='';$('paperFilter').value='to_read';
    navigate('library');refreshPapers().catch(e=>alertUser(e.message,true));
  };
  let pendingEvidencePapers=[];
  async function openPendingEvidence(item) {
    const topic=currentTopic;
    focusedPaperId=item.id;$('paperSearch').value='';$('paperFilter').value='all';
    navigate('library');
    try {
      await refreshPapers();
      if (topic===currentTopic) await previewPaper({id:item.id,title:item.title},item.first_page||1);
    } catch (err) { alertUser('打开待复核论文失败：'+err.message,true); }
  }
  $('openEvidenceQueue').onclick=()=>{
    if(pendingEvidencePapers.length)openPendingEvidence(pendingEvidencePapers[0]);
    else {navigate('library');alertUser('当前主题没有待人工复核的 PDF 实验证据。');}
  };
  $('showAllPapers').onclick=()=>{focusedPaperId='';syncPublicLink({push:true});refreshPapers().catch(e=>alertUser(e.message,true));};
  async function refreshDashboard(){
    const topic=currentTopic;
    const data=await api('/api/public/dashboard?topic_id='+encodeURIComponent(topic));
    if(topic!==currentTopic)return;
    $('metricPapers').textContent=data.papers;
    $('metricStarred').textContent=data.starred;
    $('metricRead').textContent=data.read;
    $('metricReports').textContent=data.reports;
    $('metricInbox').textContent=data.inbox||0;
    $('metricNextActions').textContent=data.next_actions||0;
    $('metricToRead').textContent=data.to_read||0;
    $('metricPendingEvidence').textContent=data.pending_review||0;
    pendingEvidencePapers=Array.isArray(data.pending_papers)?data.pending_papers:[];
    const pendingList=$('homePendingPapers');pendingList.replaceChildren();
    pendingList.hidden=!pendingEvidencePapers.length;
    for(const item of pendingEvidencePapers){
      const row=node('div','',pendingList,'pending-paper');
      const open=node('button',item.title,row,'quiet-link');
      open.onclick=()=>openPendingEvidence(item);
      node('span',`${item.pending_count} 条待核对 · PDF 第 ${item.first_page} 页`,row,'hint');
    }
    const detail=currentTopics.find(t=>t.id===topic);
    $('topicQuickSummary').textContent=detail?
      `${detail.name} · ${(detail.keywords||[]).join('、')}`:'请选择研究主题';
    const sourceNames={arxiv:'arXiv',openalex:'OpenAlex',crossref:'Crossref',openreview:'OpenReview'};
    $('homeSourceStatus').textContent=detail?
      '文献来源：'+(detail.sources||['arxiv']).map(x=>sourceNames[x]||x).join('、'):'尚未选择文献来源。';
    const recent=$('homeRecentPapers');recent.replaceChildren();
    if(!data.recent.length){
      node('p','暂无论文。先检索当前主题。',recent,'hint');
      const start=node('button','开始检索',recent,'primary');start.onclick=()=>navigate('tasks');
    }else for(const paper of data.recent){
      const entry=node('div','',recent,'recent-paper');
      const title=node('button',paper.title,entry,'quiet-link recent-title');
      title.onclick=()=>openPaperFromHome(paper.id);
      node('span',paper.published||'日期未知',entry,'recent-date');
    }
  }
  function syncHomeJob(job){
    lastJob=job;
    const label=job?.id?`#${job.id} · ${taskKinds[job.kind]||job.kind} · `+(
      job.result?.state==='partial'?'部分完成':({queued:'排队中',running:'执行中',success:'任务已结束',failed:'执行失败',cancelled:'已取消'})[job.state]||job.state):
      '暂无任务';
    $('homeJobSummary').textContent=job?.id&&UI.taskSummary(job).sources_total?
      `${taskKinds[job.kind]||job.kind} · ${UI.taskSummary(job).label} · ${UI.taskSummary(job).detail}`:label;
    $('homeJobBadge').textContent=job?.state==='running'?`正在处理：${stageLabels[job.stage]||'任务初始化'}`:
      job?.state==='queued'?`任务正在排队（第 ${job.queue_position||1} 位）`:
      job?.state==='failed'?'最近任务失败，请查看原因':
      job?.state==='success'&&['partial','failed','unknown'].includes(UI.taskSummary(job).kind)?UI.taskSummary(job).label+' · '+UI.taskSummary(job).detail:
      job?.state==='success'?'最近一次任务已结束，可查看结果':'选择任务开始检索';
    $('homeJobBadge').dataset.state=job?.state||'idle';
  }

  function renderJobDetail(job) {
    $('jobDetailPanel').hidden=!job || !job.id;
    if (!job || !job.id) return;
    const newDetail=displayedJobId!==job.id;
    displayedJobId=job.id;
    if(newDetail)$('jobStepsDetail').open=['queued','running','failed'].includes(job.state);
    $('jobDetailTitle').textContent=`任务 #${job.id} · ${taskKinds[job.kind]||job.kind}`;
    $('jobDetailMeta').textContent=job.started_at ?
      `开始于 ${new Date(job.started_at*1000).toLocaleString()}` : '排队中';
    const phases=job.kind==='search'?['discovery']:
      job.kind==='synthesize'?['synthesis']:
      job.kind==='analyze'?['fulltext','evidence']:
      ['discovery','download','fulltext','evidence','synthesis'];
    const statuses=new Map();
    for (const event of job.events||[]) if(phases.includes(event.stage)) statuses.set(event.stage,event.status);
    const list=$('jobStages');list.replaceChildren();
    for (const stage of phases) {
      const state=statuses.get(stage)||'not_run';
      const li=node('li','',list);li.dataset.state=state;
      node('span',({started:'…',completed:'✓',failed:'!',cancelled:'×',not_run:'–'})[state]||'–',li,'job-stage-marker');
      node('span',stageLabels[stage],li,'job-stage-name');
      node('span',state==='not_run'?(job.state==='queued'?'尚未开始':'未执行或已复用'):
        (stageStatus[state]||state),li,'job-stage-detail');
    }
    const result=job.result||{};
    const summary=UI.taskSummary(job);
    const counter=result.discovery||result;
    const details=[];
    if(summary.sources_total)details.push(summary.label+' · '+summary.detail);
    if(Number.isInteger(counter.retrieved))details.push(`发现 ${counter.retrieved} 篇论文`);
    if(Number.isInteger(counter.new))details.push(`新增 ${counter.new} 篇`);
    if(Number.isInteger(result.analyzed))details.push(`已解析 ${result.analyzed} 篇 PDF`);
    if(result.synthesis && Number.isInteger(result.synthesis.finding_count))
      details.push(`报告包含 ${result.synthesis.finding_count} 条实验证据`);
    if(result.state==='partial')details.push('部分成果已保存；未取得的 PDF 或未完成的分析需人工检查');
    function retrievalSummary(part,label) {
      if(!part || !part.retrieval || !Number.isInteger(part.retrieval.scanned_pages))return;
      const r=part.retrieval;
      const count=Array.isArray(r.candidate_pages)?r.candidate_pages.length:0;
      details.push(`${label}：共解析 ${r.scanned_pages} 页，选取 ${count} 页（第 ${(r.candidate_pages||[]).join('、')||'无'} 页）`);
      if(r.model_cache_hit)details.push('已命中当前 PDF 的完整分析缓存，无需重复调用模型');
      else if(Number.isInteger(r.model_batches_total))
        details.push(`本次新调用 ${r.model_batches_attempted} 批，完成 ${r.model_batches_succeeded}/${r.model_batches_total} 批${r.model_batches_resumed?`（续用 ${r.model_batches_resumed} 批检查点）`:''}${r.model_batches_failed?`，失败 ${r.model_batches_failed} 批`:''}${r.model_quota_blocked?'；模型额度不足':''}`);
      if(Array.isArray(r.failed_candidate_pages)&&r.failed_candidate_pages.length)
        details.push(`未完成核验的实验候选页：${r.failed_candidate_pages.join('、')}`);
      if(Array.isArray(r.unprocessed_candidate_pages)&&r.unprocessed_candidate_pages.length)
        details.push(`尚未调用模型的候选页：${r.unprocessed_candidate_pages.join('、')}`);
      if(r.model_findings_rejected)details.push(`因原文证据不足，拒绝 ${r.model_findings_rejected} 条候选结果`);
      if(Array.isArray(r.model_diagnostics))for(const diagnostic of r.model_diagnostics.slice(0,3)){details.push(`模型调用：${diagnostic.title}；${diagnostic.action}`);}
    }
    retrievalSummary(result.analysis,'当前论文');
    if(result.analysis?.analysis_error)details.push(result.analysis.analysis_error);
    if(Array.isArray(result.papers))for(const p of result.papers){
      retrievalSummary(p,p.paper_id);
      if(p.analysis_error)details.push(`${p.paper_id}：${p.analysis_error}`);
    }
    if(job.state==='failed')details.push(`失败原因：${job.error||'请联系管理员查看服务器日志'}`);
    if(job.state==='cancelled')details.push('任务已取消。已完成的入库结果仍保留。');
    if(job.state==='running')details.push('正在执行；刷新页面不会终止后台任务。');
    $('jobOutcome').textContent=details.join('；')||'尚无已保存的阶段统计。';
    const sources=result.discovery?.source_results||result.source_results||[];
    const src=$('jobSourceResults');src.replaceChildren();
    if(Array.isArray(sources)&&sources.length){
      node('strong','本次公开检索来源',src);
      for(const item of sources){
        const row=node('div','',src,'source-result');row.dataset.state=item.ok?'ok':'failed';
        node('strong',({arxiv:'arXiv',openalex:'OpenAlex',crossref:'Crossref',openreview:'OpenReview'})[item.name]||String(item.name||'来源'),row);
        const cacheStatus=item.cache_state==='hit'?' · 缓存命中':
          item.cache_state==='stale'?' · 来源异常，已展示旧缓存（待刷新）':'';
        node('span',`${UI.sourceState(item)} · ${item.count||0} 篇 · ${item.seconds||0} 秒${cacheStatus}`,row);
        if(!item.ok||item.partial||item.cache_state==='stale')node('small',UI.sourceAdvice(item),row,'source-next-action');
      }
    }
    const btn=$('openTaskReport');const reportId=result.report_id;
    btn.hidden=!reportId;
    btn.onclick=reportId?()=>openReport(reportId,{scroll:true}).catch(e=>alertUser(e.message,true)):null;
    const retry=$('retrySafeJob');retry.hidden=!job.retryable;
    retry.onclick=job.retryable?async()=>{
      try{
        const res=await api('/api/public/jobs/retry','POST',{job_id:job.id});
        alertUser(`已提交安全重试任务 #${res.job_id}；所有任务配额照常计算。`);
        displayedJobId=0;await refreshJobState();
      }catch(err){alertUser('无法重试：'+err.message,true);}
    }:null;
  }

  function renderMarkdownSafely(markdown,container) {
    // Never put paper/model text into innerHTML. Markdown subset is DOM-created
    // with textContent, including escaped/untrusted CSV data.
    container.replaceChildren();
    const lines=String(markdown||'').split(/\r?\n/).slice(0,2400);
    // Render only a small, safe inline subset. Paper titles and model output
    // always enter through text nodes, never innerHTML.
    const inline=(element,value)=>{
      const parts=String(value||'').split(/(\*\*[^*\r\n]+\*\*|`[^`\r\n]+`|\\[\\`*_{}\[\]<>])/g);
      for(const part of parts){
        if(!part)continue;
        if(part.startsWith('**')&&part.endsWith('**'))node('strong',part.slice(2,-2),element);
        else if(part.startsWith('`')&&part.endsWith('`'))node('code',part.slice(1,-1),element);
        else element.appendChild(document.createTextNode(part[0]==='\\'&&part.length===2?part.slice(1):part));
      }
    };
    let i=0;
    while(i<lines.length){
      const line=lines[i];
      const heading=/^(#{1,4})\s+(.+)$/.exec(line);
      if(heading){const level=Math.min(4,heading[1].length+2);inline(node('h'+level,'',container),heading[2]);i++;continue;}
      if(line.trim().startsWith('|') && i+1<lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i+1])){
        const parse=x=>x.trim().replace(/^\||\|$/g,'').split('|').map(z=>z.trim());
        const heads=parse(line);
        const wrap=node('div','',container,'report-table-scroll');
        const table=node('table','',wrap);
        const header=node('thead','',table),tr=node('tr','',header);
        heads.forEach(h=>inline(node('th','',tr),h));i+=2;
        const body=node('tbody','',table);
        while(i<lines.length && lines[i].trim().startsWith('|')){
          const tr=node('tr','',body);
          parse(lines[i]).slice(0,heads.length).forEach(x=>inline(node('td','',tr),x));i++;
        }
        continue;
      }
      if(/^\s*[-*]\s+/.test(line))inline(node('p','• ',container),line.replace(/^\s*[-*]\s+/,''));
      else if(line.trim() && !/^[-_]{3,}$/.test(line.trim()))inline(node('p','',container),line);
      i++;
    }
    if(!lines.some(line=>line.trim()))node('p','当前报告没有正文。',container,'hint');
  }

  function refreshTopicTrace(){
    const root=$('topicTraceWorkbench');if(!root||!currentUser)return;
    topicTrace=window.PRTrace.mount({root,request:api,mode:'public',topic:currentTopic,
      onMessage:(m,error)=>{if(error)alertUser(m,true)},
      onChanged:()=>{if(paperTrace&&readerPaperId)paperTrace.refresh()}});
  }
  function loadPublicInquiry(){
    if(!window.PRInquiry||!currentUser)return;
    if(!publicInquiry)publicInquiry=window.PRInquiry.mount({
      root:$('publicInquiryWorkbench'),mode:'public',getTopic:()=>currentTopic,request:api,
      onCompleted:()=>refreshReports().catch(()=>{}),onNavigate:view=>navigate(view)
    });
    publicInquiry.refresh();
    if(window.PRResearchProgram){
      if(!window._publicResearchProgram)window._publicResearchProgram=window.PRResearchProgram.mount({
        root:$('publicResearchProgram'),mode:'public',getTopic:()=>currentTopic,request:api,
        getSharedQuestion:()=>publicInquiry?.getQuestion()||'',
        onAnalysisComplete:()=>refreshReports().catch(()=>{})
      });
      window._publicResearchProgram.refresh();
    }
  }
  async function refreshReports() {
    const topic=currentTopic;
    refreshTopicTrace();
    $('reportHistory').textContent='正在加载已保存报告…';
    const data=await api('/api/public/reports?topic_id='+encodeURIComponent(topic));
    if(topic!==currentTopic)return;
    const target=$('reportHistory');target.replaceChildren();
    if(!data.reports.length){node('p','还没有报告。先在“研究分析”中提出问题，或者点击“生成跨论文比较”。',target,'hint');return;}
    for(const report of data.reports){
      const entry=node('div','',target,'report-entry');
      const text=node('div','',entry);
      node('strong',`研究进展 · ${new Date(report.generated_at*1000).toLocaleString()}`,text);
      node('div',`${report.paper_count} 篇有证据的论文 · ${report.finding_count} 条可核对证据`,text,'report-entry-meta');
      const btn=node('button','在线阅读',entry);btn.type='button';
      btn.onclick=()=>openReport(report.id,{scroll:true}).catch(err=>alertUser(err.message,true));
    }
  }

  function renderComparisonMatrix(summary) {
    const root=$('comparisonMatrix');root.replaceChildren();
    const area=$('comparisonArea');
    const groups=Array.isArray(summary?.comparisons)?summary.comparisons:[];
    area.hidden=groups.length===0;
    const filter=String($('comparisonFilter').value||'').trim().toLowerCase();
    const filtered=groups.filter(group=>[group.dataset,group.metric,group.kind,group.setting]
      .some(v=>String(v||'').toLowerCase().includes(filter)));
    if(!filtered.length){node('p',groups.length?'没有符合筛选条件的实验组。':'当前报告尚无字段条件一致的跨论文实验组。',root,'hint');return;}
    for(const group of filtered.slice(0,30)){
      const section=node('section','',root,'matrix-group');
      node('h3',`${group.dataset||'未注明数据集'} · ${group.metric||'未注明指标'}`,section);
      node('p',`实验类别：${group.kind||'未知'}；条件：${group.setting||'缺失'}；论文 ${group.papers||0} 篇。`,section,'hint');
      const state={user_confirmed_conditions:'全部配对已登记人工条件复核',
        partially_reviewed:'部分配对已复核',manual_review_required:'仍需人工对照原文'};
      node('p',`${state[group.review_status]||'待复核'} · 人工确认配对 ${(group.user_confirmed_pairs||[]).length} 组 · 条件不满足 ${(group.blocked_pairs||[]).length} 组`,section,'hint');
      const accepted=(group.records||[]).filter(r=>r.review_decision==='approved' && r.evidence_id);
      if(accepted.length>=2){
        const details=node('details','',section,'review-details');
        node('summary','登记两篇论文的实验可比性判断',details);
        const wrap=node('form','',details,'review-grid');
        const choices=[];
        for(let k=0;k<2;k++){
          const lbl=node('label',k===0?'第一条已审核证据':'第二条已审核证据',wrap);
          const sel=node('select','',lbl);choices.push(sel);
          for(const rec of accepted){const o=node('option',`${rec.title||rec.paper_id} · ${rec.metric} ${rec.value}`,sel);o.value=rec.evidence_id;}
        }
        choices[1].selectedIndex=Math.min(1,accepted.length-1);
        const choiceLabel=node('label','配对结论',wrap);const choice=node('select','',choiceLabel);
        for(const [value,label] of [['needs_review','尚未确定'],['comparable','人工确认可比'],['incompatible','条件不一致']]){
          const opt=node('option',label,choice);opt.value=value;
        }
        const noteLabel=node('label','判断依据',wrap);
        const note=node('textarea','',noteLabel);note.rows=2;note.maxLength=360;
        note.placeholder='记录数据版本、数据划分、单位、基线与实验协议如何对齐';
        const save=node('button','保存两项证据的比较判断',wrap,'primary');save.type='submit';
        wrap.onsubmit=async event=>{
          event.preventDefault();save.disabled=true;
          try{
            await api('/api/public/comparisons/review','POST',{
              topic_id:currentTopic,evidence_a_id:choices[0].value,evidence_b_id:choices[1].value,
              decision:choice.value,note:note.value});
            alertUser('配对复核已保存。历史报告保持不变；重新生成报告可保存新结论。');
            // Historical reports are immutable; a new synthesis is a queued,
            // quota-controlled research job initiated by the user.
            save.textContent='已保存。请生成新报告查看更新后的矩阵';
          }catch(err){alertUser('配对复核未保存：'+err.message,true);}
          finally{save.disabled=false;}
        };
      }
      const flag=node('p','',section,'matrix-warning');
      flag.textContent=(group.flags||[]).join('；')||'指标定义、数据划分、单位和计算预算仍需人工核对。';
      if((group.blocked_pairs||[]).length){
        const problems=node('details','',section,'review-details');
        node('summary',`查看 ${group.blocked_pairs.length} 项条件冲突或人工否决`,problems);
        const list=node('ul','',problems);
        for(const conflict of group.blocked_pairs.slice(0,20))
          node('li',(conflict.reasons||[]).join('；'),list);
      }
      const scroll=node('div','',section,'matrix-scroll');
      const table=node('table','',scroll,'comparison-table');
      const head=node('thead','',table);const hrow=node('tr','',head);
      for(const col of ['论文','原文数值','对照基线','证据等级','定位'])node('th',col,hrow);
      const tbody=node('tbody','',table);
      for(const rec of (group.records||[]).slice(0,40)){
        const row=node('tr','',tbody);
        node('td',rec.title||rec.paper_id,row);
        node('td',rec.value||'—',row);
        node('td',rec.baseline||'—',row);
        const verified=rec.quote_verified_against_pdf===true ||
          ['pdf_sha256_verified','user_pdf_quote_verified'].includes(rec.provenance);
        const evidenceLabel=rec.provenance==='user_pdf_quote_verified'?
          '上传 PDF 引文匹配；实验行列及文献身份待复核':
          verified?'PDF 引文匹配；实验行列待复核':'导入数据，待复核';
        node('td',evidenceLabel,row,verified?'matrix-verified':'matrix-unverified');
        const page=node('td','',row);
        if(verified&&rec.paper_id){
          const btn=node('button',`核查第 ${Number(rec.page)||1} 页`,page,'matrix-open');btn.type='button';
          btn.onclick=()=>previewPaper({id:rec.paper_id,title:rec.title},Number(rec.page)||1);
        }else node('span',`第 ${Number(rec.page)||1} 页（待人工核查）`,page,'hint');
      }
    }
    if(filtered.length>30)node('p',`还有 ${filtered.length-30} 组未在此处展开，请缩小筛选范围。`,root,'hint');
  }
  $('comparisonFilter').addEventListener('input',()=>renderComparisonMatrix(window.__prCurrentSummary||{}));

  async function openReport(id,{scroll=false,push=true,position=0}={}) {
    const requestedTopic=currentTopic, requestedReport=++reportNonce;
    const record=await api('/api/public/reports/view?id='+encodeURIComponent(id));
    if(currentTopic!==requestedTopic || requestedReport!==reportNonce)return;
    if(record.topic_id!==currentTopic){alertUser('报告属于另一研究主题，请先切换主题。',true);return;}
    openedReportId=id;lastReport=record.markdown||'';
    $('downloadSynthesis').disabled=!lastReport;
    $('reportReader').hidden=false;
    $('reportReaderTitle').textContent='研究报告 · '+new Date(record.generated_at*1000).toLocaleString();
    $('reportReaderInfo').textContent=`已收录 ${Number(record.summary?.library_paper_count??record.paper_count)} 篇题录 · ${record.paper_count} 篇有证据的论文 · ${record.finding_count} 条可核对证据。历史报告可能未包含最新论文版本。`;
    $('reportAuditMeta').textContent=`报告格式版本：${record.schema_version}；证据摘要指纹：${record.source_digest}。用于识别报告采用的证据摘要版本，仍需核对原文。`;
    renderMarkdownSafely(record.markdown,$('reportContent'));
    const summary=record.summary||{};
    window.__prCurrentSummary=summary;
    renderComparisonMatrix(summary);
    const target=$('summary');target.replaceChildren();
    node('p',`本主题收录 ${Number(summary.library_paper_count??record.paper_count)} 篇论文题录，其中 ${summary.paper_count||0} 篇有可核对的实验依据，共 ${summary.finding_count||0} 条证据。原文来源和比较口径请在报告正文核对。`,target);
    if(summary.diagnostics){const d=summary.diagnostics;
      node('p',`数据诊断：未核验 ${d.unverified||0} 条；条件缺失 ${d.incomplete||0} 条；疑似重复 ${d.duplicates||0} 条。`,target,'hint');}
    navigate('reports',{push,scroll:false});
    if(scroll){$('reportReader').scrollIntoView({behavior:'smooth',block:'start'});$('reportContent').focus({preventScroll:true});}
    else if(position)requestAnimationFrame(()=>restoreReportPosition(position));
  }
  async function uploadPaper(paper) {
    const field=document.createElement('input');field.type='file';field.accept='.pdf,application/pdf';
    field.onchange=async()=>{const file=field.files[0];if(!file)return;
      if(file.size<256 || file.size>32*1024*1024) return alertUser('PDF 大小应在 256B—32MiB 之间',true);
      try{const res=await fetch('/api/public/pdf',{method:'PUT',credentials:'same-origin',
          headers:{'Content-Type':'application/pdf','X-Portal-Action':'1','X-CSRF-Token':csrf,
            'X-Paper-ID':paper.id,'X-Topic-ID':currentTopic},body:file});
        let data={};try{data=await res.json();}catch(_){/* nginx may send an HTML 413 */}
        if(res.status===413)throw Error('PDF 超出服务器上传限制（最大 32 MiB）。请压缩 PDF 后重试。');
        if(!res.ok)throw Error(data.error||`PDF 上传失败（状态 ${res.status}），请检查网络或联系管理员。`);
        alertUser('PDF 已保存到当前账号。请点击“分析 PDF”。');
      }catch(e){alertUser(e.message,true);}
    };field.click();
  }
  async function previewPaper(paper,page=1,{push=true,scroll=true}={}) {
    try{
      const requestedTopic=currentTopic;
      const request=++readerNonce;
      const requestedPage=Math.max(1,Math.min(240,Number(page)||1));
      const q=new URLSearchParams({topic_id:requestedTopic,paper_id:paper.id,page:String(requestedPage)});
      const res=await api('/api/public/fulltext?'+q);
      if(requestedTopic!==currentTopic || request!==readerNonce)return;
      readerPaperId=paper.id;readerPage=requestedPage;
      $('fulltextArea').open=true;
      navigate('library',{push,scroll:false});
      const target=$('fulltextPanel');target.replaceChildren();
      const heading=node('div','',target,'evidence-heading');
      node('strong',paper.title||'论文精读',heading);
      const pdfStateName={indexed:'已完成全文读取',saved:'PDF 已保存，尚未读取全文',stale:'文件已更换，请重新上传',missing:'尚无 PDF'};
      const pdfSourceName={user_upload:'用户上传',arxiv_public:'公开来源'};
      node('p',`${pdfStateName[res.document.state]||'PDF 状态待确认'} · ${res.document.pages?res.document.pages+' 页':'页数待读取'} · ${pdfSourceName[res.document.source]||'来源待确认'}`,heading,'hint');
      const ready=['saved','indexed'].includes(res.document.state);
      if(!ready){
        node('p','尚无有效 PDF。请在论文卡片中上传 PDF 或使用已授权的公开下载，再点击“分析 PDF”。',target,'empty-state');
        if(scroll)$('fulltextArea').scrollIntoView({behavior:'smooth',block:'start'});return;
      }
      const assistant=node('section','',target,'paper-reading-assistant');
      node('h3','阅读这篇论文',assistant);
      node('p','读摘要、查看实验依据、按问题找原文。每条结果都有页码，具体数值需要核对 PDF。',assistant,'hint');
      const taskButtons=node('div','',assistant,'reader-action-buttons');
      const summaryTab=node('button','摘要与研究内容',taskButtons,'reader-tab');summaryTab.type='button';
      const experimentTab=node('button','实验结果与比较',taskButtons,'reader-tab');experimentTab.type='button';
      const questionTab=node('button','向论文提问',taskButtons,'reader-tab');questionTab.type='button';
      const taskOutput=node('section','',assistant,'reader-task-output');
      const summaryContent=()=>{
        taskOutput.replaceChildren();
        node('h4','这篇论文的主要内容',taskOutput);
        node('p',paper.abstract||'当前收录记录没有摘要。可在下方 PDF 原文中阅读，并使用全文分析提取依据。',taskOutput);
        node('p','此处来自论文的原始题录或摘要，尚未作为经过核实的实验结论。',taskOutput,'hint');
      };
      summaryTab.onclick=summaryContent;
      experimentTab.onclick=()=>{
        taskOutput.replaceChildren();
        node('h4','当前页的实验依据',taskOutput);
        if(matches.length){for(const item of matches.slice(0,6)){
          const card=node('article','',taskOutput,'pdf-evidence-card');
          node('strong',`${item.metric||'实验结果'} · PDF 第 ${item.page||requestedPage} 页`,card);
          node('blockquote',item.quote||'请打开原文核对',card);
          const btn=node('button','跳转至引用页',card);btn.type='button';
          btn.onclick=()=>previewPaper(paper,item.page||requestedPage);
        }}else node('p','本页尚无已定位的实验结果。可以先执行 PDF 分析，或在下方核对表格原文。',taskOutput,'hint');
        const go=node('button','生成当前主题的实验比较报告',taskOutput,'primary');go.type='button';
        go.onclick=async()=>{
          try{
            await submitResearch('/api/public/research/synthesize',{fetch_pdf:false,cloud_analysis:false});
            navigate('tasks');alertUser('实验比较任务已提交，完成后在“我的报告”查看。');
          }catch(err){alertUser('无法提交实验比较：'+err.message,true)}
        };
      };
      questionTab.onclick=()=>{
        taskOutput.replaceChildren();
        node('h4','按研究问题定位 PDF 原文',taskOutput);
        node('p','先从已解析文字中查找相关段落；返回内容是原文线索，需要核对后再引用。',taskOutput,'hint');
        const field=node('textarea','',taskOutput);field.rows=2;field.maxLength=400;
        field.setAttribute('aria-label','关于当前论文的问题');field.placeholder='例如：这篇论文使用了哪些基线模型？';
        const ask=node('button','查找原文依据',taskOutput,'primary');ask.type='button';
        const result=node('section','',taskOutput,'reader-evidence-answers');
        ask.onclick=async()=>{
          const q=field.value.trim();if(q.length<5){result.textContent='请填写至少 5 个字符的问题。';return}
          ask.disabled=true;result.textContent='正在当前论文中查找…';
          try{
            const data=await api('/api/public/pdf/question','POST',{topic_id:requestedTopic,paper_id:paper.id,question:q});
            if(request!==readerNonce||requestedTopic!==currentTopic)return;
            result.replaceChildren();node('p',data.answer||'未找到相关原文',result);
            for(const ref of data.references||[]){
              const card=node('article','',result,'pdf-evidence-card');
              node('strong',`[${ref.id}] PDF 第 ${ref.page} 页`,card);
              node('blockquote',ref.quote,card);
              const jump=node('button','打开此页 PDF',card);jump.type='button';
              jump.onclick=()=>previewPaper(paper,ref.page);
            }
            if(!(data.references||[]).length)node('p','可尝试论文英文关键词，或先点击“分析 PDF”建立文本索引。',result,'hint');
          }catch(err){result.textContent='这次检索失败：'+err.message}
          finally{ask.disabled=false}
        };
        field.focus({preventScroll:true});
      };
      summaryContent();
      const layout=node('div','',target,'pdf-evidence-layout');
      const pdfPane=node('section','',layout,'pdf-pane');
      node('h3','PDF 原文',pdfPane);
      const source=new URLSearchParams({topic_id:requestedTopic,paper_id:paper.id});
      const viewer=document.createElement('iframe');
      viewer.title='当前论文 PDF 阅读器';viewer.className='pdf-reader-frame';
      viewer.loading='lazy';viewer.referrerPolicy='no-referrer';
      viewer.src='/api/public/pdf/view?'+source.toString()+'#page='+requestedPage;
      pdfPane.appendChild(viewer);
      const external=node('a','在单独标签打开 PDF',pdfPane,'paper-source-link');
      external.href=viewer.src;external.target='_blank';external.rel='noopener noreferrer';
      const side=node('section','',layout,'evidence-side');
      node('h3','原文与页码',side);
      const jump=node('div','',side,'evidence-jump');
      const jumpLabel=node('label','页码',jump);jumpLabel.htmlFor='evidenceJumpPage';
      const input=node('input','',jump);input.id='evidenceJumpPage';input.type='number';
      input.min='1';input.max=String(Math.min(240,res.document.pages||240));input.value=String(requestedPage);
      const go=node('button','跳转',jump);go.type='button';
      go.onclick=()=>previewPaper(paper,Number(input.value)||1);
      const prev=node('button','上一页',jump);prev.type='button';
      prev.disabled=requestedPage===1;prev.onclick=()=>previewPaper(paper,Math.max(1,requestedPage-1));
      const next=node('button','下一页',jump);next.type='button';
      next.disabled=requestedPage>=Math.min(240,res.document.pages||240);
      next.onclick=()=>previewPaper(paper,requestedPage+1);
      const share=node('button','复制论文链接',jump);share.type='button';
      share.onclick=()=>sharePublicLink();
      // Public PDF evidence: server resolves the current account's PDF and
      // validates its stored SHA-256 before returning text geometry or PNG.
      const geometry=node('details','',side,'review-details');
      node('summary','原文高亮与表格',geometry);
      const geometryBody=node('div','',geometry,'review-history-content');
      node('p','输入当前页中的逐字引文，定位后可查看原文高亮。表格为待人工核对的候选内容。',geometryBody,'hint');
      const quoteField=document.createElement('textarea');quoteField.rows=2;
      quoteField.maxLength=700;quoteField.placeholder='粘贴第 '+requestedPage+' 页的逐字引文（至少 6 字）';
      geometryBody.appendChild(quoteField);
      const findButton=node('button','定位原文',geometryBody);findButton.type='button';
      const tableButton=node('button','查看本页表格',geometryBody);tableButton.type='button';
      const geometryOutput=node('div','',geometryBody,'review-history-content');
      const geometryParams=()=>new URLSearchParams({topic_id:requestedTopic,
        paper_id:paper.id,page:String(requestedPage),quote:quoteField.value.trim()});
      findButton.onclick=async()=>{
        if(quoteField.value.trim().length<6)return alertUser('请粘贴至少 6 字的逐字引文。',true);
        geometryOutput.replaceChildren();
        try{
          const args=geometryParams();
          const found=await api('/api/public/pdf/locate?'+args.toString());
          if(requestedTopic!==currentTopic||request!==readerNonce)return;
          if(!found.matched){node('p','该页文字层未找到逐字引文，请打开 PDF 手工核对。',geometryOutput);return;}
          node('p',`已在第 ${requestedPage} 页定位 ${found.boxes.length} 处。`,geometryOutput);
          const image=document.createElement('img');image.alt='原文证据高亮预览';
          image.loading='lazy';image.style.maxWidth='100%';
          image.src='/api/public/pdf/highlight?'+args.toString();
          geometryOutput.appendChild(image);
        }catch(e){node('p','定位失败：'+e.message,geometryOutput);}
      };
      tableButton.onclick=async()=>{
        geometryOutput.replaceChildren();
        try{
          const args=geometryParams();
          const data=await api('/api/public/pdf/tables?'+args.toString());
          if(requestedTopic!==currentTopic||request!==readerNonce)return;
          node('p',data.note||'表格结果需要人工复核。',geometryOutput,'hint');
          if(!(data.tables||[]).length)node('p','这一页未找到可识别的表格。',geometryOutput);
          for(const table of (data.tables||[])){
            const block=node('div','',geometryOutput,'pdf-evidence-card');
            node('strong','表格候选 '+table.id,block);
            for(const row of table.rows||[])node('p',row.join(' | '),block);
          }
        }catch(e){node('p','表格提取失败：'+e.message,geometryOutput);}
      };
      const matches=Array.isArray(res.evidence)?res.evidence:[];
      node('p',`第 ${requestedPage} 页 · ${matches.length} 条已匹配 PDF 引文（实验行列待复核）`,side,'hint');
      const history=node('details','',side,'review-details');
      node('summary','查看本篇论文的人工复核历史',history);
      const historyContent=node('div','',history,'review-history-content');
      let historyLoaded=false;
      history.ontoggle=async()=>{
        if(!history.open||historyLoaded)return;
        historyContent.textContent='正在读取复核历史…';
        try{
          const q=new URLSearchParams({topic_id:requestedTopic,paper_id:paper.id,limit:'30'});
          const data=await api('/api/public/reviews/history?'+q.toString());
          if(requestedTopic!==currentTopic)return;
          historyContent.replaceChildren();
          for(const entry of data.history||[]){
            const item=node('article','',historyContent,'review-history-item');
            node('strong',`${entry.reviewed_at} · ${entry.decision}`,item);
            node('p',entry.note||'未提供备注',item,'hint');
            node('small','PDF '+String(entry.sha256||'').slice(0,12)+'…',item);
          }
          if(!(data.history||[]).length)node('p','暂无人工复核记录。',historyContent,'hint');
          historyLoaded=true;
        }catch(err){historyContent.textContent='加载失败：'+err.message;}
      };
      for(const e of matches){
        const card=node('article','',side,'pdf-evidence-card');
        node('strong',`${e.metric||'实验指标'}：${e.value||'—'}`,card);
        node('p',[e.dataset,e.baseline?'对照 '+e.baseline:'',e.setting].filter(Boolean).join(' · '),card,'hint');
        node('blockquote',e.quote||'未提供原文片段',card);
        if(e.evidence_id){
          const link=node('button','将实验原文加入证据链',card,'trace-mini');link.type='button';
          link.onclick=()=>{
            if(paperTrace)paperTrace.setAnchor({quote:e.quote,page:e.page||requestedPage,
              experimentId:e.evidence_id});
            else alertUser('请在本页下方打开证据工作台后重试。',true);
          };
        }
        if(e.evidence_id){
          const review=e.review_decision||'needs_review';
          const label={approved:'人工已核对',rejected:'用户已排除',needs_review:'待核对'}[review]||'待核对';
          node('p','复核状态：'+label+(e.reviewed_at?' · '+e.reviewed_at:''),card,'hint');
          const details=node('details','',card,'review-details');
          node('summary','核对实验字段与原文',details);
          const form=node('form','',details,'review-grid');
          const decisionLabel=node('label','判断',form);
          const decision=node('select','',decisionLabel);
          for(const [value,text] of [['needs_review','尚未确认'],['approved','人工核对通过'],['rejected','原文关联不正确']]){
            const option=node('option',text,decision);option.value=value;
          }
          decision.value=review;
          const specs=[['dataset_version','数据版本','例如 Darcy v1'],
            ['split','数据划分','例如 test'],['unit','指标单位','例如 fraction 或 percent'],
            ['metric_direction','指标方向','higher 或 lower'],
            ['aggregation','汇总方式','例如 mean of 3 seeds'],
            ['training_budget','训练预算','例如 100 epochs'],
            ['evaluation_protocol','评估协议','例如 original official test']];
          const fields={};
          for(const [key,text,placeholder] of specs){
            const label=node('label',text,form);
            const field=node('input','',label);
            field.type='text';field.maxLength=120;field.placeholder=placeholder;
            field.value=(e.review_conditions||{})[key]||'';fields[key]=field;
          }
          const noteLabel=node('label','复核依据（必须说明）',form);
          const note=node('textarea','',noteLabel);
          note.maxLength=360;note.rows=2;
          note.placeholder='例如：已核查 PDF 第 13 页表 2 的完整行列及实验设置';
          note.value=e.review_note||'';
          const submit=node('button','保存复核结果',form,'primary');submit.type='submit';
          form.onsubmit=async event=>{
            event.preventDefault();submit.disabled=true;
            try{
              await api('/api/public/evidence/review','POST',{
                topic_id:requestedTopic,paper_id:paper.id,evidence_id:e.evidence_id,
                decision:decision.value,conditions:Object.fromEntries(
                  specs.map(([key])=>[key,fields[key].value.trim()])),note:note.value});
              alertUser('已保存人工复核记录，跨论文结论需另行核对。');
              if(requestedTopic===currentTopic){
                await previewPaper(paper,requestedPage,{push:false,scroll:false});
                await refreshDashboard();
              }
            }catch(err){alertUser('复核保存失败：'+err.message,true);}
            finally{submit.disabled=false;}
          };
        }
      }
      if(!matches.length)node('p','当前页暂未提取到可核验的实验数值。可在下面阅读已解析文本，必要时手动核查 PDF 中的表格和公式。',side,'hint');
      const rows=Array.isArray(res.table_candidates)?res.table_candidates:[];
      if(rows.length){
        const tableDetails=node('details','',side,'pdf-text-details');
        node('summary',`查看本页表格候选（${rows.length} 行，需人工核对）`,tableDetails);
        node('p','由本地布局解析器生成。行列位置和数值归属不能仅凭自动提取结果确认。',tableDetails,'hint');
        const wrap=node('div','',tableDetails,'report-table-scroll');
        const grid=node('table','',wrap);
        for(const row of rows.slice(0,60)){
          const tr=node('tr','',grid);
          node('td',`T${row.table_id+1}/R${row.row_index+1}`,tr);
          for(const item of (row.cells||[]).slice(0,12))node('td',item,tr);
        }
      }

      const details=node('details','',side,'pdf-text-details');
      node('summary','查看本页已提取文字',details);
      node('pre',res.text||'当前页尚未建立全文文本索引。请点击论文卡片上的“分析 PDF”。',details,'pdf-preview');
      const expertTools=node('details','',target,'trace-panel portal-pdf-expert');
      node('summary','深入分析：核对原文并管理研究证据',expertTools);
      const traceRoot=node('section','',expertTools,'trace-workspace');
      paperTrace=window.PRTrace.mount({root:traceRoot,request:api,mode:'public',topic:requestedTopic,
        paperId:paper.id,page:requestedPage,sourceText:res.text||'',sha:res.document.sha256||'',
        onMessage:(m,error)=>{if(error)alertUser(m,true)},
        onChanged:()=>{topicTrace?.refresh()}});
      if(scroll)$('fulltextArea').scrollIntoView({behavior:'smooth',block:'start'});
    }catch(err){alertUser(err.message,true);}
  }
  async function submitResearch(path,extra={}) {
    if(extra.cloud_analysis===true){
      const pre=await api('/api/public/model/preflight?action=experiments');
      if(!pre.available){
        await refreshPortalAiStatus();
        const offline=window.confirm('服务器尚未配置 AI 模型密钥。\n\n点击“确定”：本次继续使用普通 PDF 解析，不会发送论文给云模型。\n点击“取消”：前往设置查看模型连接方法。');
        if(!offline){navigate('account');return false;}
        extra={...extra,cloud_analysis:false};
        alertUser('这次已切换为普通 PDF 解析，AI 连接好后会自动提示可用。');
      }else{
      const cost=pre.estimate?.estimated_cost;
      const price=cost?`根据管理员填写的单价，估计约 $${cost.min}–$${cost.max}，以实际账单为准。`:'管理员未提供有效模型单价，暂时无法预估费用。';
      const ok=window.confirm(`此次任务可能向 ${pre.destination_host||'管理员配置的模型服务'} 发送论文实验段落或表格文本。${price}\n是否授权此次传输并提交任务？`);
      if(!ok)return;
      extra={...extra,confirm_cloud_data_transfer:true};
      }
    }
    const data=await api(path,'POST',{topic_id:currentTopic,...extra});
    $('jobState').textContent=`任务 #${data.job_id} 已提交`;await refreshJobState();
    return true;
  }
  let publicAiReady=null;
  function publicAiPreferenceKey(){return 'paper-radar-v148-ai:'+String(currentUser?.id||currentUser?.username||'visitor');}
  function readPublicAiPreference(){try{return localStorage.getItem(publicAiPreferenceKey())!=='off';}catch{return true}}
  function savePublicAiPreference(){try{localStorage.setItem(publicAiPreferenceKey(),$('cloudAnalysis').checked?'on':'off')}catch{}}
  async function refreshPortalAiStatus(){
    const panel=$('portalAiBanner'),summary=$('portalAiSettingsState');
    try{
      const check=await api('/api/public/model/preflight?action=experiments');
      publicAiReady=!!check.available;
      panel.hidden=publicAiReady||currentView==='account';
      summary.textContent=publicAiReady?'服务器模型已连接；正式发送论文片段前会再次征求授权。':
        '服务器尚未配置模型密钥。请联系管理员完成连接；普通检索、阅读和不依赖云模型的分析可继续使用。';
      if(!publicAiReady){
        $('portalAiTitle').textContent='AI 功能已默认开启，等待服务器配置';
        $('portalAiDescription').textContent=currentUser?.role==='admin'?
          '请在服务器环境中添加模型 API Key、接口地址和模型名称。密钥不应填写在公开页面或上传到仓库。':
          '请联系系统管理员配置模型密钥。普通检索、阅读和基础研究分析仍可使用。';
      }
    }catch(err){
      publicAiReady=null;panel.hidden=currentView==='account';
      $('portalAiTitle').textContent='暂时无法确认 AI 连接状态';
      $('portalAiDescription').textContent='请检查连接后重试。公开文献检索不受该状态检查限制。';
      summary.textContent='模型状态检查失败，请稍后刷新。';
    }
  }
  function changeTab(isRegister) {
    register = registrationEnabled && isRegister;
    $('loginTab').classList.toggle('active', !register);
    $('registerTab').classList.toggle('active', register);
    $('loginTab').setAttribute('aria-selected',String(!register));
    $('registerTab').setAttribute('aria-selected',String(register));
    $('loginTab').tabIndex=register?-1:0;
    $('registerTab').tabIndex=register?0:-1;
    $('authForm').setAttribute('aria-labelledby',register?'registerTab':'loginTab');
    $('authSubmit').textContent = register ? '注册并进入工作台' : '登录账号';
    $('password').autocomplete = register ? 'new-password' : 'current-password';
  }
  async function restore() {
    try { const data = await api('/api/public/me');
      csrf = data.csrf; currentUser = data.user; await showApp();
    } catch (_) {
      csrf='';currentUser=null;cancelPolling();
      $('appView').hidden=true;$('authView').hidden=false;
      $('logout').hidden=true;$('accountLabel').textContent='';
    }
  }
  async function showApp() {
    $('authView').hidden=true; $('appView').hidden=false; $('logout').hidden=false;
    $('accountLabel').textContent = currentUser.username;
    $('adminView').hidden = currentUser.role !== 'admin';
    $('portalAdminAiSteps').hidden=currentUser.role!=='admin';
    $('cloudAnalysis').checked=readPublicAiPreference();
    refreshPortalAiStatus().catch(()=>{});
    try{await refreshTopics();}catch(err){alertUser('主题加载失败：'+err.message,true);}
    await applyRoute();
    await refreshJobState();
    if (currentUser.role === 'admin') {
      try{await refreshAdmin();}catch(err){alertUser('管理员数据加载失败：'+err.message,true);}
    }
  }
  async function refreshTopics() {
    const data=await api('/api/public/topics');
    const firstTopicLoad=currentTopics.length===0;
    currentTopics=data.topics;
    const hashTopic=routeFromHash().topic;
    if(firstTopicLoad && hashTopic && data.topics.some(t=>t.id===hashTopic))currentTopic=hashTopic;
    const select=$('topicSelect'); select.replaceChildren();
    for (const t of data.topics) {
      const opt=document.createElement('option');opt.value=t.id;
      opt.textContent=t.name; select.appendChild(opt);
    }
    if (data.topics.some(t=>t.id===currentTopic)) select.value=currentTopic;
    else currentTopic=select.value;
    fillTopicEditor();
    await Promise.all([refreshDashboard(),refreshPapers()]);
    try{await refreshReports();}catch(err){
      $('reportHistory').textContent='报告加载失败，请检查网络后点击“刷新报告”。';
      alertUser(err.message,true);
    }
  }
  async function updatePaperFlags(paper,changes){
    const topic=currentTopic;
    try{
      const result=await api('/api/public/papers/flags','POST',{
        topic_id:topic,paper_id:paper.id,...changes});
      if(topic!==currentTopic)return;
      Object.assign(paper,result);
      await Promise.all([refreshPapers(),refreshDashboard()]);
    }catch(err){alertUser('阅读状态保存失败：'+err.message,true);}
  }
  // Batch selection deliberately covers only papers the user has loaded and seen.
  function updateTriageToolbar(){
    const count=triageSelected.size;
    $('triageSelectedCount').textContent=count?`已选择 ${count} 篇（最多 100 篇）`:'未选择论文';
    for(const id of ['triageStar','triageSkip','triageDone','triageClear']) $(id).disabled=!count;
    const eligible=loadedPaperIds.slice(0,100);
    $('paperSelectPage').disabled=!eligible.length;
    $('paperSelectPage').checked=eligible.length>0&&eligible.every(x=>triageSelected.has(x));
    $('paperSelectPage').indeterminate=eligible.some(x=>triageSelected.has(x))&&!$('paperSelectPage').checked;
    for(const input of document.querySelectorAll('.paper-selection input'))
      input.checked=triageSelected.has(input.value);
  }
  function clearTriageSelection(){triageSelected.clear();updateTriageToolbar();}
  $('paperSelectPage').onchange=()=>{
    if($('paperSelectPage').checked){
      for(const id of loadedPaperIds){if(triageSelected.size>=100)break;triageSelected.add(id);}
      if(loadedPaperIds.length>100)alertUser('单次最多处理 100 篇，请先完成本批次。');
    }else triageSelected.clear();
    updateTriageToolbar();
  };
  $('triageClear').onclick=clearTriageSelection;
  async function doBulkTriage(action){
    const ids=[...triageSelected];if(!ids.length)return;
    if(ids.length>100){alertUser('每次最多处理 100 篇。',true);return;}
    if(['skip','done'].includes(action) && !window.confirm(
      `确定将 ${ids.length} 篇论文${action==='skip'?'设为“暂不关注”':'标记为“已读完”'}？`))return;
    for(const id of ['triageStar','triageSkip','triageDone','triageClear'])$(id).disabled=true;
    try{
      const result=await api('/api/public/papers/bulk','POST',{
        topic_id:currentTopic,paper_ids:ids,action});
      triageSelected.clear();
      await Promise.all([refreshPapers(),refreshDashboard()]);
      alertUser(`已处理 ${result.updated} 篇论文。`);
    }catch(err){alertUser('批量处理未完成：'+err.message,true);updateTriageToolbar();}
  }
  $('triageStar').onclick=()=>doBulkTriage('star');
  $('triageSkip').onclick=()=>doBulkTriage('skip');
  $('triageDone').onclick=()=>doBulkTriage('done');
  function downloadReading(scope,format){
    const args=new URLSearchParams({topic_id:currentTopic,scope,format});
    const link=document.createElement('a');
    link.href='/api/public/reading/export?'+args.toString();
    link.download='paper-radar-reading.'+format;
    document.body.appendChild(link);link.click();link.remove();
  }
  $('portalBibExport').onclick=()=>{window.location.href='/api/public/papers/citations?topic_id='+encodeURIComponent(currentTopic)+'&scope=starred&format=bib'};
  $('portalRisExport').onclick=()=>{window.location.href='/api/public/papers/citations?topic_id='+encodeURIComponent(currentTopic)+'&scope=starred&format=ris'};
  $('exportStarredReading').onclick=()=>downloadReading('starred','md');
  $('exportActionReading').onclick=()=>downloadReading('actionable','csv');
  const actionOptions=[['none','暂不安排'],['read','精读原文'],
    ['replicate','尝试复现'],['cite','准备引用'],['discuss','与同事讨论']];
  const actionLabel=action=>actionOptions.find(x=>x[0]===action)?.[1]||'暂不安排';
  function appendPaperNote(paper,el){
    const draftKey=currentTopic+':'+paper.id;
    const draft=readingDrafts.get(draftKey);
    const details=node('details','',el,'paper-note-panel');
    const summary=node('summary',paper.note?'编辑阅读笔记 · 已保存':'添加阅读笔记 / 后续行动',details);
    const fields=node('div','',details,'note-fields');
    const noteLabel=node('label','我的判断与疑问（最多 1600 字）',fields);
    const note=node('textarea','',noteLabel);
    note.value=draft?.note??paper.note??'';note.maxLength=1600;note.rows=4;
    note.placeholder='例如：解决了什么问题、主要实验条件、与我的研究有什么关系、还需核对的页码。';
    const actionLabelEl=node('label','下一步行动',fields);
    const action=node('select','',actionLabelEl);
    for(const [value,label] of actionOptions){const opt=node('option',label,action);opt.value=value;}
    action.value=draft?.action??paper.next_action??'none';
    const footer=node('div','',details,'paper-note-actions');
    const save=node('button','保存笔记',footer,'primary');save.type='button';
    const status=node('span',draft?'有未保存草稿；请点击“保存笔记”':'本主题笔记',footer,'hint');
    const discard=node('button','丢弃本页草稿',footer);discard.type='button';discard.disabled=!draft;
    discard.onclick=()=>{readingDrafts.delete(draftKey);note.value=paper.note||'';
      action.value=paper.next_action||'none';discard.disabled=true;
      status.textContent='已丢弃本页草稿。其他窗口的修改可以刷新后查看。';};
    const dirty=()=>{readingDrafts.set(draftKey,{note:note.value,action:action.value,
      revision:readingDrafts.get(draftKey)?.revision??paper.note_revision??0});
      discard.disabled=false;status.textContent='尚未保存，离开前请点击“保存笔记”';};
    note.oninput=dirty;action.onchange=dirty;
    save.onclick=async()=>{
      save.disabled=true;status.textContent='正在保存…';
      try{
        const result=await api('/api/public/papers/note','POST',{
          topic_id:currentTopic,paper_id:paper.id,note:note.value,
          next_action:action.value,expected_revision:readingDrafts.get(draftKey)?.revision??paper.note_revision??0});
        Object.assign(paper,result);readingDrafts.delete(draftKey);discard.disabled=true;
        summary.textContent=paper.note?'编辑阅读笔记 · 已保存':'添加阅读笔记 / 后续行动';
        status.textContent='已保存 · 再次编辑时以最新版本为准';
        refreshDashboard().catch(()=>{});
      }catch(err){
        status.textContent=err.status===409?'其他窗口已更新此笔记；当前草稿保留，请先复制再刷新':
          '保存失败，草稿仍保留在此页面';
        alertUser(status.textContent+'：'+err.message,true);
      }finally{save.disabled=false;}
    };
  }
  async function refreshPapers(append=false) {
    const requestedTopic=currentTopic;
    const offset=(append && paperTopic===requestedTopic)?paperOffset:0;
    const search=$('paperSearch').value.trim();
    const filter=$('paperFilter').value,sort=$('paperSort').value;
    const nonce=++paperQueryNonce;
    if(!append){triageSelected.clear();loadedPaperIds=[];updateTriageToolbar();}
    $('paperCount').textContent='正在加载论文…';
    const params=new URLSearchParams({topic_id:requestedTopic,limit:'12',offset:String(offset),
      q:search,filter,sort});
    if(focusedPaperId)params.set('paper_id',focusedPaperId);
    const data=await api('/api/public/papers?'+params.toString());
    if(requestedTopic!==currentTopic||nonce!==paperQueryNonce||
      search!==$('paperSearch').value.trim()||filter!==$('paperFilter').value||sort!==$('paperSort').value)return;
    const target=$('paperList');
    $('focusedPaper').hidden=!focusedPaperId;
    if(offset===0)target.replaceChildren();
    if(!data.total){
      const message=search||filter!=='all'?'未找到符合当前筛选条件的论文。可调整搜索或筛选。':
        '当前研究主题尚未收录论文。开始一次公开检索即可建立文献库。';
      node('p',message,target,'empty-state');
      if(!search&&filter==='all'){const start=node('button','去检索论文',target,'primary');
        start.onclick=()=>navigate('tasks');}
    }
    for(const paper of data.papers){
      const el=node('article','',target,'paper paper-card-v1493');
      const head=node('div','',el,'paper-head');
      const titleGroup=node('div','',head,'paper-title-group');
      const checkLabel=node('label','',head,'paper-selection');
      const check=node('input','',checkLabel);check.type='checkbox';check.value=paper.id;
      check.setAttribute('aria-label','选择论文：'+paper.title);
      check.checked=triageSelected.has(paper.id);
      check.onchange=()=>{
        if(check.checked){
          if(triageSelected.size>=100){check.checked=false;alertUser('每次最多选择 100 篇。',true);return;}
          triageSelected.add(paper.id);
        }else triageSelected.delete(paper.id);
        updateTriageToolbar();
      };
      node('strong',paper.title,titleGroup);
      const star=node('button',paper.is_starred?'★ 已星标':'☆ 星标',head,'star-toggle');
      star.type='button';star.setAttribute('aria-pressed',String(!!paper.is_starred));
      star.setAttribute('aria-label',(paper.is_starred?'取消星标：':'星标论文：')+paper.title);
      star.onclick=()=>updatePaperFlags(paper,{is_starred:!paper.is_starred});
      const source=paper.id.startsWith('openalex:')?'OpenAlex':
        paper.id.startsWith('crossref:')?'Crossref':
        paper.id.startsWith('openreview:')?'OpenReview':'arXiv / 手工入库';
      node('small',[source,paper.published,paper.doi].filter(Boolean).join(' · '),el);
      if(paper.abstract){
        node('p',paper.abstract.length>260?paper.abstract.slice(0,260)+'…':paper.abstract,el,'paper-preview');
        if(paper.abstract.length>260){
          const abstractMore=node('details','',el,'paper-abstract-more');
          node('summary','展开完整摘要',abstractMore);
          node('p',paper.abstract,abstractMore,'paper-original-abstract');
        }
      }
      if(!String(paper.abstract||'').trim()){
        const recover=node('button','从公开来源补取摘要',el,'abstract-refresh');recover.type='button';
        recover.onclick=async()=>{
          recover.disabled=true;recover.textContent='正在核对公开索引…';
          try{
            const res=await api('/api/public/papers/abstract/refresh','POST',{topic_id:currentTopic,paper_id:paper.id});
            if(res.status==='updated'||res.status==='already_present'){
              paper.abstract=res.abstract;alertUser('摘要已补全，来源：'+(res.source||'已收录元数据'));await refreshPapers();
            }else{recover.textContent='来源暂无摘要';alertUser(res.reason||'公开来源没有提供摘要');}
          }catch(err){recover.disabled=false;recover.textContent='重新补取摘要';alertUser(err.message,true);}
        };
      }
      const actions=node('div','',el,'paper-tools');
      const reader=node('button','阅读论文',actions,'primary paper-read-main');
      reader.type='button';reader.onclick=()=>previewPaper(paper);
      const statusLabel=node('label','阅读进度',actions,'reading-label');
      const progress=node('select','',statusLabel,'reading-select');
      progress.setAttribute('aria-label','阅读进度：'+paper.title);
      for(const [value,label] of [['unread','未阅读'],['reading','阅读中'],['done','已读完']]){
        const opt=node('option',label,progress);opt.value=value;
      }
      progress.value=paper.reading_status||'unread';
      progress.onchange=()=>updatePaperFlags(paper,{reading_status:progress.value});
      const more=node('details','',actions,'paper-expert-actions');
      node('summary','更多操作',more);
      const extra=node('div','',more,'paper-expert-actions-body');
      const vote=node('button',paper.feedback===1?'✓ 已标记有价值':'有价值',extra,'feedback-btn');
      vote.type='button';vote.setAttribute('aria-pressed',String(paper.feedback===1));
      vote.onclick=()=>updatePaperFlags(paper,{feedback:paper.feedback===1?0:1});
      const skip=node('button',paper.feedback===-1?'撤销暂不关注':'暂不关注',extra);
      skip.type='button';skip.setAttribute('aria-pressed',String(paper.feedback===-1));
      skip.onclick=()=>updatePaperFlags(paper,paper.feedback===-1?{feedback:0}:
        {feedback:-1,is_starred:false});
      const upload=node('button','上传 PDF',extra);upload.type='button';upload.onclick=()=>uploadPaper(paper);
      const view=node('button','查看证据',extra);view.type='button';view.onclick=()=>previewPaper(paper);
      const analyze=node('button','分析 PDF',extra);analyze.type='button';analyze.onclick=async()=>{
        try{const submitted=await submitResearch('/api/public/research/analyze',
          {paper_id:paper.id,cloud_analysis:$('cloudAnalysis').checked});if(submitted)navigate('tasks');}
        catch(e){alertUser(e.message,true);}};
      const tags=node('div','',el,'paper-actions-secondary');
      if(paper.feedback===-1){const tag=node('span','暂不关注',tags,'paper-decision-tag');tag.dataset.kind='skipped';}
      if(paper.next_action && paper.next_action!=='none'){
        const tag=node('span','下一步：'+actionLabel(paper.next_action),tags,'paper-decision-tag');
        tag.dataset.kind='action';
      }
      appendPaperNote(paper,el);
      if(/^https:\/\//.test(paper.url)){
        const link=node('a','打开原文 ↗',actions,'paper-source-link');link.href=paper.url;
        link.target='_blank';link.rel='noopener noreferrer';
      }
    }
    for(const paper of data.papers)if(!loadedPaperIds.includes(paper.id))loadedPaperIds.push(paper.id);
    updateTriageToolbar();
    paperTopic=requestedTopic;paperOffset=offset+data.papers.length;
    $('paperCount').textContent=`显示 ${paperOffset} / ${data.total} 篇`;
    $('loadPapers').hidden=paperOffset>=data.total;
  }
  function requestPaperRefresh(){
    focusedPaperId='';readerPaperId='';$('fulltextArea').open=false;
    if(paperSearchTimer)clearTimeout(paperSearchTimer);
    paperSearchTimer=setTimeout(()=>{syncPublicLink();refreshPapers().catch(e=>alertUser(e.message,true))},260);
  }
  $('paperSearch').addEventListener('input',requestPaperRefresh);
  $('paperFilter').addEventListener('change',()=>{focusedPaperId='';readerPaperId='';$('fulltextArea').open=false;syncPublicLink({push:true});refreshPapers().catch(e=>alertUser(e.message,true));});
  $('paperSort').addEventListener('change',()=>{focusedPaperId='';readerPaperId='';$('fulltextArea').open=false;syncPublicLink({push:true});refreshPapers().catch(e=>alertUser(e.message,true));});
  $('fulltextArea').addEventListener('toggle',()=>{
    if(!$('fulltextArea').open && readerPaperId && R.get('paper')===readerPaperId && !portalRestoring){
      readerPaperId='';syncPublicLink({push:true});
    }
  });
  $('loadPapers').onclick=async()=>{
    try{await refreshPapers(true);}catch(err){alertUser(err.message,true);}
  };
  async function refreshAdmin() {
    const stats=await api('/api/public/admin/jobs');
    $('adminJobStats').textContent=`执行中 ${stats.states.running}；等待 ${stats.states.queued}；在线科研 Worker ${stats.live_workers ?? 0}；过期租约 ${stats.expired_leases ?? 0}；最长等待 ${stats.oldest_queued_seconds ?? 0} 秒；`
      +`近24小时完成 ${stats.last_24h.successful} 次、失败 ${stats.last_24h.failed} 次。`;
    const data=await api('/api/public/admin/accounts');
    const target=$('accountList');target.replaceChildren();
    for(const acc of data.accounts) {
      const row=node('div','',target,'admin-row');
      const label=node('span',acc.username+' · '+acc.role+' · '+(acc.is_active?'正常':'已停用'),row);
      if(acc.id===currentUser.id) continue;
      const button=node('button',acc.is_active?'停用账号':'启用账号',row);
      button.addEventListener('click',async()=>{
        try{await api('/api/public/admin/account','POST',{id:acc.id,active:!acc.is_active});await refreshAdmin();}
        catch(e){alertUser(e.message,true);}
      });
    }
  }
  $('loginTab').onclick=()=>changeTab(false);
  $('registerTab').onclick=()=>changeTab(true);
  for(const tab of [$('loginTab'),$('registerTab')]){
    tab.addEventListener('keydown',e=>{
      if(e.key==='ArrowLeft'||e.key==='ArrowRight'){
        e.preventDefault();const next=tab.id==='loginTab'?'registerTab':'loginTab';
        if($(next).hidden)return;
        $(next).focus();changeTab(next==='registerTab');
      }
    });
  }
  $('authForm').onsubmit=async e=>{
    e.preventDefault();try{
      const data=await api('/api/public/'+(register?'register':'login'),'POST',{
        username:$('username').value.trim(),password:$('password').value});
      csrf=data.csrf;currentUser=data.user;$('password').value='';alertUser('');await showApp();
    }catch(err){alertUser(err.message,true);}
  };
  $('logout').onclick=async()=>{cancelPolling();try{await api('/api/public/logout','POST');}catch(_){}
    csrf='';currentUser=null;lastFinishedJob=null;lastReport='';openedReportId='';
    $('reportReader').hidden=true;$('downloadSynthesis').disabled=true;
    $('logout').hidden=true;$('accountLabel').textContent='';await restore();};
  $('topicSelect').onchange=async()=>{
    currentTopic=$('topicSelect').value;lastReport='';openedReportId='';
    triageSelected.clear();loadedPaperIds=[];updateTriageToolbar();
    focusedPaperId='';readerPaperId='';$('fulltextArea').open=false;fillTopicEditor();
    $('reportReader').hidden=true;$('downloadSynthesis').disabled=true;
    $('summary').textContent='选择历史报告或排队生成新的比较报告。';
    try{navigate(currentView,{push:true,scroll:false});
      await Promise.all([refreshDashboard(),refreshPapers(),refreshReports()]);}
    catch(e){alertUser(e.message,true);}
  };
  $('topicAdd').onclick=async()=>{try{
    const words=$('newKeywords').value.split(/[,，;；]+/).map(v=>v.trim()).filter(Boolean);
    const info=await api('/api/public/topics','POST',{name:$('newTopic').value,
      keywords:words,sources:selectedSources('new')});
    currentTopic=info.id;navigate(currentView,{scroll:false});await refreshTopics();
    $('newTopic').value='';$('newKeywords').value='';selectTopicMode('edit');$('topicSetup').open=false;alertUser('研究主题已建立');
  }catch(err){alertUser(err.message,true);}};
  $('topicSave').onclick=async()=>{try{
    const words=$('editKeywords').value.split(/[,，;；]+/).map(v=>v.trim()).filter(Boolean);
    await api('/api/public/topics/update','POST',{topic_id:currentTopic,
      name:$('editTopicName').value,keywords:words,sources:selectedSources('edit')});
    await refreshTopics();alertUser('研究主题配置已保存。下一次检索会使用新的来源和关键词。');
  }catch(err){alertUser('保存主题失败：'+err.message,true);}};
  const jobLabels = {queued:'等待执行',running:'正在执行',success:'已完成',
    failed:'执行失败',cancelled:'已取消',idle:'没有检索任务'};
  const jobActive = state => state === 'running' || state === 'queued';
  function cancelPolling() { if (jobTimer) clearTimeout(jobTimer); jobTimer = null; }
  function scheduleJobPoll(delay) {
    cancelPolling();
    if (currentUser) jobTimer = setTimeout(refreshJobState, delay);
  }
  function retryJobPoll() {
    // Bounded exponential backoff with jitter; never resubmit the research job.
    jobFailures = Math.min(jobFailures + 1, 6);
    const base = Math.min(30000, 2000 * (2 ** (jobFailures - 1)));
    scheduleJobPoll(base + Math.floor(Math.random() * Math.min(600, base / 4)));
  }
  window.addEventListener('online', () => {
    if (currentUser) refreshJobState();
  });
  document.addEventListener('visibilitychange', () => {
    if (currentUser && !document.hidden) refreshJobState();
  });
  async function refreshJobHistory() {
    const data=await api('/api/public/jobs/history?limit=8');
    const target=$('jobHistory');target.replaceChildren();
    if(!data.jobs.length){node('p','暂无任务记录。',target,'hint');return;}
    for(const job of data.jobs){
      const row=node('div','',target,'job-row');
      const time=new Date(job.created_at*1000).toLocaleString();
      const state=job.result?.state==='partial'?'部分完成':(jobLabels[job.state]||job.state);
      node('span',`#${job.id} · ${taskKinds[job.kind]||job.kind} · ${state} · ${time}`,row);
      if(job.error)node('small',`原因：${job.error}`,row);
      const btn=node('button','查看详情',row);btn.type='button';
      btn.onclick=async()=>{
        try{const detail=await api('/api/public/jobs/detail?id='+job.id);
          navigate('tasks');renderJobDetail(detail);$('jobDetailPanel').scrollIntoView({behavior:'smooth',block:'start'});
        }catch(err){alertUser(err.message,true);}
      };
      if(job.retryable){
        const retry=node('button','安全重试',row);retry.type='button';
        retry.onclick=async()=>{try{
          const result=await api('/api/public/jobs/retry','POST',{job_id:job.id});
          alertUser('已提交新任务 #'+result.job_id);await refreshJobState();
        }catch(err){alertUser('重试失败：'+err.message,true);}};
      }
    }
  }
  async function refreshQuota(){
    try{
      const data=await api('/api/public/quotas');const q=data.quotas||{};
      const balance=key=>Number.isInteger(q[key]?.remaining)?q[key].remaining:'—';
      $('taskQuotas').textContent=`过去 24 小时剩余额度：公开检索 ${balance('search')} 次 · PDF 分析 ${balance('analyze')} 次 · 报告生成 ${balance('synthesize')} 次 · 云模型调用 ${balance('model_calls')} 次。`;
    }catch(_){$('taskQuotas').textContent='暂时无法读取任务配额，可稍后刷新。';}
  }
  async function refreshJobState(){
    if(!currentUser){cancelPolling();return;}
    if(jobRefreshInFlight)return;
    jobRefreshInFlight=true;
    cancelPolling();
    try{
      const job=await api('/api/public/jobs');
      syncHomeJob(job);
      const busy=jobActive(job.state);
      $('searchNow').disabled=busy;$('runResearch').disabled=busy;$('runSynthesis').disabled=busy;
      $('cancelJob').hidden=!busy;
      $('researchStage').textContent=busy?`当前：${stageLabels[job.stage]||'排队中'}`:'';
      $('jobState').textContent=job.state==='queued'?`任务 #${job.id} 已排队（位置 ${job.queue_position||1}）`:
        job.state==='running'?`任务 #${job.id} 正在执行（第 ${job.attempts} 次）`:
        job.state==='success'?`任务 #${job.id} · ${UI.taskSummary(job).label} · ${UI.taskSummary(job).detail}`:
        job.state==='failed'?`任务 #${job.id} 失败：${job.error||'请检查详情'}`:
        job.state==='cancelled'?`任务 #${job.id} 已取消`:'暂无运行任务';
      if(!displayedJobId||busy||displayedJobId===job.id)renderJobDetail(job);
      if(!busy && job.id && lastFinishedJob!==job.id){
        lastFinishedJob=job.id;
        if(job.state==='success')await refreshPapers();
        await Promise.all([refreshReports(),refreshDashboard()]);
      }
      if(!busy)await Promise.all([refreshJobHistory(),refreshQuota()]);
      if(jobFailures>0)alertUser('任务状态连接已恢复。');
      jobFailures=0;
      // Also poll while idle: jobs submitted in another tab become visible.
      scheduleJobPoll(busy?2300:30000);
    }catch(err){
      if(err.status===401){
        csrf='';currentUser=null;cancelPolling();
        $('appView').hidden=true;$('authView').hidden=false;
        $('logout').hidden=true;$('accountLabel').textContent='';
        alertUser('登录已过期，请重新登录。',true);
        return;
      }
      $('jobState').textContent='任务状态暂时不可用，系统正在自动重试：'+err.message;
      if(jobFailures===0)alertUser('任务状态查询失败，正在自动重连；后台任务不会重复提交。',true);
      retryJobPoll();
    }finally{
      jobRefreshInFlight=false;
    }
  }
  $('searchNow').onclick=async()=>{const button=$('searchNow');button.disabled=true;try{
    await preparePortalSearch();
    const job=await api('/api/public/search','POST',{topic_id:currentTopic});
    alertUser(''); // A new successful task clears any stale validation error.
    $('jobState').textContent='已开始查找论文，检索进度如下。';
    await refreshJobState();
  }catch(err){alertUser('检索未开始：'+err.message,true);}finally{button.disabled=false;}};
  $('runResearch').onclick=async()=>{const button=$('runResearch');button.disabled=true;try{
    await preparePortalSearch();
    await submitResearch('/api/public/research/start',{fetch_pdf:$('fetchPdf').checked,cloud_analysis:$('cloudAnalysis').checked});
  }catch(e){alertUser('分析任务未开始：'+e.message,true);}finally{button.disabled=false;}};
  $('cancelJob').onclick=async()=>{try{
    await api('/api/public/jobs/cancel','POST',{});
    $('jobState').textContent='已请求取消，正在等待安全停止…';
    await refreshJobState();
  }catch(err){alertUser(err.message,true);}};
  $('importEvidence').onclick=async()=>{try{
    const file=$('evidenceCsv').files[0];if(!file)throw Error('请先选择 CSV 文件');
    if(file.size>240000)throw Error('CSV 文件请控制在 240KB 内');
    const csv=await file.text();const res=await api('/api/public/import','POST',{topic_id:currentTopic,csv});
    $('importResult').textContent=`新增 ${res.imported} 条实验记录。${res.warning}`;
    await refreshPapers();
  }catch(err){alertUser(err.message,true);}};
  $('runSynthesis').onclick=async()=>{
    try{
      await submitResearch('/api/public/research/synthesize',{fetch_pdf:false,cloud_analysis:false});
      $('summary').textContent='比较报告已进入任务队列。完成后请在“研究报告与历史”中在线阅读。';
    }catch(err){alertUser(err.message,true);}
  };
  $('refreshReports').onclick=()=>refreshReports().catch(err=>alertUser(err.message,true));
  $('reportClose').onclick=()=>{$('reportReader').hidden=true;openedReportId='';syncPublicLink({push:true});};
  async function sharePublicLink(){
    syncPublicLink();
    try{await R.share();alertUser('已复制当前页面阅读链接。需要登录且具备相应主题权限才能访问。');}
    catch{alertUser('请从地址栏复制当前链接',true);}
  }
  $('sharePagePortal').addEventListener('click',sharePublicLink);
  window.addEventListener('scroll',()=>{
    if(currentView!=='reports'||!openedReportId||portalRestoring||$('reportReader').hidden)return;
    clearTimeout(portalLinkTimer);
    portalLinkTimer=setTimeout(()=>syncPublicLink(),170);
  },{passive:true});
  $('exportEvidence').onclick=()=>{
    // Same-origin cookie authentication; the server sends safe CSV attachment.
    window.location.assign('/api/public/evidence/export?topic_id='+encodeURIComponent(currentTopic));
  };
  $('refreshJob').onclick=()=>refreshJobState();
  function downloadCurrentReport(){
    if(!lastReport){alertUser('请先从历史记录打开一份报告。',true);return;}
    const blob=new Blob([lastReport],{type:'text/markdown;charset=utf-8'});
    const url=URL.createObjectURL(blob);const a=document.createElement('a');
    a.href=url;a.download='PaperRadar_Report_'+(openedReportId||'current').slice(0,12)+'.md';a.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  $('downloadSynthesis').onclick=downloadCurrentReport;
  $('reportDownload').onclick=downloadCurrentReport;
  $('changePassword').onclick=async()=>{try{
    await api('/api/public/password','POST',{old_password:$('oldPassword').value,new_password:$('newPassword').value});
    $('oldPassword').value='';$('newPassword').value='';csrf='';currentUser=null;
    alertUser('密码已修改，所有会话已退出，请重新登录');await restore();
  }catch(err){alertUser(err.message,true);}};
  async function boot(){
    try{
      const health=await api('/api/public/health');
      if(health.version)$('portalVersion').textContent='v'+health.version;
      registrationEnabled=health.registration_enabled!==false;
      $('registerTab').hidden=!registrationEnabled;
      if(!registrationEnabled)changeTab(false);
    }catch(_){/* Login still works if the optional health probe fails. */}
    await restore();
  }
  boot();
})();
