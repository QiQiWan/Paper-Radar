'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let csrf = '', currentUser = null, currentTopic = 'default', lastReport = '';
  let openedReportId = '', displayedJobId = 0;
  let register = false, registrationEnabled = true;
  let jobTimer = null, lastFinishedJob = null, jobFailures = 0, jobRefreshInFlight = false;
  let paperOffset=0, paperTopic='', paperQueryNonce=0, paperSearchTimer=null;
  let focusedPaperId='';
  let currentView='workspace', currentTopics=[], lastJob=null;
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
    catch (_) { throw new Error('网络连接失败。请检查连接后重新刷新任务或报告，已保存的成果不会丢失。'); }
    let data; try { data = await result.json(); } catch (_) { data = {}; }
    if (!result.ok) {
      const error = new Error(data.error || `请求失败 HTTP ${result.status}`);
      error.status = result.status;
      throw error;
    }
    return data;
  }
  const stageLabels={starting:'准备执行',discovery:'发现论文',download:'准备 PDF',
    fulltext:'解析全文',evidence:'核验实验',synthesis:'生成报告',task:'整个任务'};
  const stageStatus={started:'正在执行',completed:'已完成',failed:'失败',cancelled:'已取消'};
  const taskKinds={search:'公开检索',research:'完整科研分析',analyze:'单篇 PDF 分析',synthesize:'跨论文比较'};
  const views={workspace:['工作台','WORKSPACE'],library:['论文库','LIBRARY'],
    tasks:['任务中心','TASKS'],reports:['研究报告','REPORTS'],account:['设置','SETTINGS']};
  function routeFromHash() {
    const parts=window.location.hash.replace(/^#/,'').split('/');
    return {view:Object.hasOwn(views,parts[0])?parts[0]:'workspace',
      topic:/^[a-z0-9_-]{1,48}$/.test(parts[1]||'')?parts[1]:''};
  }
  function navigate(view,{push=true,scroll=true}={}){
    if(!Object.hasOwn(views,view))view='workspace';
    currentView=view;
    for(const page of document.querySelectorAll('.portal-page'))page.hidden=page.dataset.page!==view;
    for(const button of document.querySelectorAll('#primaryNav [data-view]')){
      const active=button.dataset.view===view;
      button.classList.toggle('active',active);
      if(active)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
    }
    $('currentPageName').textContent=views[view][0];
    $('currentPageEn').textContent=views[view][1];
    document.title=views[view][0]+' · Paper-Radar';
    const hash='#'+view+'/'+encodeURIComponent(currentTopic);
    if(push && window.location.hash!==hash)history.pushState(null,'',hash);
    if(scroll)window.scrollTo({top:0,behavior:'instant'});
  }
  async function applyRoute(){
    if(!currentUser)return;
    const {view,topic}=routeFromHash();
    if(topic && currentTopics.some(t=>t.id===topic) && currentTopic!==topic){
      currentTopic=topic;$('topicSelect').value=topic;
      lastReport='';openedReportId='';$('reportReader').hidden=true;
      try{await Promise.all([refreshDashboard(),refreshPapers(),refreshReports()]);}
      catch(err){alertUser('主题切换失败：'+err.message,true);}
    }
    navigate(view,{push:false,scroll:true});
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
  $('quickStart').onclick=()=>navigate('tasks');
  $('homeOpenTasks').onclick=()=>navigate('tasks');
  $('homeOpenLibrary').onclick=()=>navigate('library');
  $('libraryRunSearch').onclick=()=>navigate('tasks');
  const selectedSources = mode => Array.from(document.querySelectorAll(`[data-sources="${mode}"]:checked`))
    .map(input=>input.value);
  function fillTopicEditor() {
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
  $('showAllPapers').onclick=()=>{focusedPaperId='';refreshPapers().catch(e=>alertUser(e.message,true));};
  async function refreshDashboard(){
    const topic=currentTopic;
    const data=await api('/api/public/dashboard?topic_id='+encodeURIComponent(topic));
    if(topic!==currentTopic)return;
    $('metricPapers').textContent=data.papers;
    $('metricStarred').textContent=data.starred;
    $('metricRead').textContent=data.read;
    $('metricReports').textContent=data.reports;
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
    const sourceNames={arxiv:'arXiv',openalex:'OpenAlex',crossref:'Crossref'};
    $('homeSourceStatus').textContent=detail?
      '当前检索来源：'+(detail.sources||['arxiv']).map(x=>sourceNames[x]||x).join('、'):'尚未配置检索来源。';
    const recent=$('homeRecentPapers');recent.replaceChildren();
    if(!data.recent.length){
      node('p','尚未收录论文。进入任务中心，点击“检索当前主题”开始第一轮跟踪。',recent,'hint');
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
      job.result?.state==='partial'?'部分完成':({queued:'等待 Worker',running:'执行中',success:'已完成',failed:'执行失败',cancelled:'已取消'})[job.state]||job.state):
      '当前还没有研究任务';
    $('homeJobSummary').textContent=label;
    $('homeJobBadge').textContent=job?.state==='running'?`正在处理：${stageLabels[job.stage]||'任务初始化'}`:
      job?.state==='queued'?`任务正在排队（第 ${job.queue_position||1} 位）`:
      job?.state==='failed'?'最近任务失败，请进入任务中心查看原因':
      job?.state==='success'&&job?.result?.state==='partial'?'最近任务部分完成，请查看未完成环节':
      job?.state==='success'?'最近一次任务已结束，报告可在研究报告中查看':'可随时启动新任务';
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
      `开始于 ${new Date(job.started_at*1000).toLocaleString()}` : '等待 Worker 领取';
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
    const counter=result.discovery||result;
    const details=[];
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
        node('strong',({arxiv:'arXiv',openalex:'OpenAlex',crossref:'Crossref'})[item.name]||String(item.name||'来源'),row);
        const cacheStatus=item.cache_state==='hit'?' · 缓存命中':
          item.cache_state==='stale'?' · 来源异常，已展示旧缓存（待刷新）':'';
        node('span',item.ok?`成功 · ${item.count||0} 篇 · ${item.seconds||0} 秒${cacheStatus}`:
          `${item.timed_out?'请求超时':'连接失败'} · ${item.seconds||0} 秒`,row);
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
    let i=0;
    while(i<lines.length){
      const line=lines[i];
      const heading=/^(#{1,4})\s+(.+)$/.exec(line);
      if(heading){const level=Math.min(4,heading[1].length+2);node('h'+level,heading[2],container);i++;continue;}
      if(line.trim().startsWith('|') && i+1<lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i+1])){
        const parse=x=>x.trim().replace(/^\||\|$/g,'').split('|').map(z=>z.trim());
        const heads=parse(line);
        const wrap=node('div','',container,'report-table-scroll');
        const table=node('table','',wrap);
        const header=node('thead','',table),tr=node('tr','',header);
        heads.forEach(h=>node('th',h,tr));i+=2;
        const body=node('tbody','',table);
        while(i<lines.length && lines[i].trim().startsWith('|')){
          const tr=node('tr','',body);
          parse(lines[i]).slice(0,heads.length).forEach(x=>node('td',x,tr));i++;
        }
        continue;
      }
      if(/^\s*[-*]\s+/.test(line))node('p','• '+line.replace(/^\s*[-*]\s+/,''),container);
      else if(line.trim() && !/^[-_]{3,}$/.test(line.trim()))node('p',line,container);
      i++;
    }
    if(!lines.some(line=>line.trim()))node('p','当前报告没有正文。',container,'hint');
  }

  async function refreshReports() {
    const topic=currentTopic;
    $('reportHistory').textContent='正在加载已保存报告…';
    const data=await api('/api/public/reports?topic_id='+encodeURIComponent(topic));
    if(topic!==currentTopic)return;
    const target=$('reportHistory');target.replaceChildren();
    if(!data.reports.length){node('p','尚无报告。运行“完整科研分析”或“排队生成比较报告”后会在这里保存。',target,'hint');return;}
    for(const report of data.reports){
      const entry=node('div','',target,'report-entry');
      const text=node('div','',entry);
      node('strong',`研究进展 · ${new Date(report.generated_at*1000).toLocaleString()}`,text);
      node('div',`${report.paper_count} 篇论文 · ${report.finding_count} 条证据 · 快照 ${report.source_digest.slice(0,10)}`,text,'report-entry-meta');
      const btn=node('button','在线阅读',entry);btn.type='button';
      btn.onclick=()=>openReport(report.id,{scroll:true}).catch(err=>alertUser(err.message,true));
    }
  }

  function renderComparisonMatrix(summary) {
    const root=$('comparisonMatrix');root.replaceChildren();
    const area=$('comparisonArea');
    const groups=Array.isArray(summary?.comparisons)?summary.comparisons:[];
    area.hidden=false;
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

  async function openReport(id,{scroll=false}={}) {
    const record=await api('/api/public/reports/view?id='+encodeURIComponent(id));
    if(record.topic_id!==currentTopic){alertUser('报告属于另一研究主题，请先切换主题。',true);return;}
    openedReportId=id;lastReport=record.markdown||'';
    $('downloadSynthesis').disabled=!lastReport;
    $('reportReader').hidden=false;
    $('reportReaderTitle').textContent='研究报告 · '+new Date(record.generated_at*1000).toLocaleString();
    $('reportReaderInfo').textContent=`版本 ${record.schema_version} · ${record.paper_count} 篇论文 · ${record.finding_count} 条证据 · 生成时来源摘要 ${record.source_digest.slice(0,16)}。历史结果需核查最新论文版本。`;
    renderMarkdownSafely(record.markdown,$('reportContent'));
    const summary=record.summary||{};
    window.__prCurrentSummary=summary;
    renderComparisonMatrix(summary);
    const target=$('summary');target.replaceChildren();
    node('p',`该报告包含 ${summary.paper_count||0} 篇论文、${summary.finding_count||0} 条证据。原文来源和比较口径请在报告正文核对。`,target);
    if(summary.diagnostics){const d=summary.diagnostics;
      node('p',`数据诊断：未核验 ${d.unverified||0} 条；条件缺失 ${d.incomplete||0} 条；疑似重复 ${d.duplicates||0} 条。`,target,'hint');}
    if(scroll){navigate('reports');$('reportReader').scrollIntoView({behavior:'smooth',block:'start'});$('reportContent').focus({preventScroll:true});}
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
        if(!res.ok)throw Error(data.error||`PDF 上传失败（HTTP ${res.status}），请检查反向代理配置。`);
        alertUser('PDF 已保存到当前账号。请点击“分析 PDF”。');
      }catch(e){alertUser(e.message,true);}
    };field.click();
  }
  async function previewPaper(paper,page=1) {
    try{
      const requestedTopic=currentTopic;
      const requestedPage=Math.max(1,Math.min(120,Number(page)||1));
      const q=new URLSearchParams({topic_id:requestedTopic,paper_id:paper.id,page:String(requestedPage)});
      const res=await api('/api/public/fulltext?'+q);
      if(requestedTopic!==currentTopic)return;
      navigate('library');
      $('fulltextArea').open=true;
      const target=$('fulltextPanel');target.replaceChildren();
      const heading=node('div','',target,'evidence-heading');
      node('strong',paper.title||'论文精读',heading);
      node('p',`PDF 状态：${res.document.state} · ${res.document.pages||0} 页 · ${res.document.source||'待上传'}`,heading,'hint');
      const ready=['saved','indexed'].includes(res.document.state);
      if(!ready){
        node('p','尚无有效 PDF。请在论文卡片中上传 PDF 或使用已授权的公开下载，再点击“分析 PDF”。',target,'empty-state');
        $('fulltextArea').scrollIntoView({behavior:'smooth',block:'start'});return;
      }
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
      node('h3','证据与页码',side);
      const jump=node('div','',side,'evidence-jump');
      const jumpLabel=node('label','页码',jump);jumpLabel.htmlFor='evidenceJumpPage';
      const input=node('input','',jump);input.id='evidenceJumpPage';input.type='number';
      input.min='1';input.max=String(Math.min(120,res.document.pages||120));input.value=String(requestedPage);
      const go=node('button','跳转',jump);go.type='button';
      go.onclick=()=>previewPaper(paper,Number(input.value)||1);
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
                await previewPaper(paper,requestedPage);
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
      $('fulltextArea').scrollIntoView({behavior:'smooth',block:'start'});
    }catch(err){alertUser(err.message,true);}
  }
  async function submitResearch(path,extra={}) {
    const data=await api(path,'POST',{topic_id:currentTopic,...extra});
    $('jobState').textContent=`任务 #${data.job_id} 已提交`;await refreshJobState();
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
  async function refreshPapers(append=false) {
    const requestedTopic=currentTopic;
    const offset=(append && paperTopic===requestedTopic)?paperOffset:0;
    const search=$('paperSearch').value.trim();
    const filter=$('paperFilter').value,sort=$('paperSort').value;
    const nonce=++paperQueryNonce;
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
      const el=node('article','',target,'paper');
      const head=node('div','',el,'paper-head');
      node('strong',paper.title,head);
      const star=node('button',paper.is_starred?'★ 已星标':'☆ 星标',head,'star-toggle');
      star.type='button';star.setAttribute('aria-pressed',String(!!paper.is_starred));
      star.setAttribute('aria-label',(paper.is_starred?'取消星标：':'星标论文：')+paper.title);
      star.onclick=()=>updatePaperFlags(paper,{is_starred:!paper.is_starred});
      const source=paper.id.startsWith('openalex:')?'OpenAlex':
        paper.id.startsWith('crossref:')?'Crossref':'arXiv / 手工入库';
      node('small',[source,paper.published,paper.doi].filter(Boolean).join(' · '),el);
      if(paper.abstract)node('p',paper.abstract.length>400?paper.abstract.slice(0,400)+'…':paper.abstract,el);
      const actions=node('div','',el,'paper-tools');
      const statusLabel=node('label','阅读进度',actions,'reading-label');
      const progress=node('select','',statusLabel,'reading-select');
      progress.setAttribute('aria-label','阅读进度：'+paper.title);
      for(const [value,label] of [['unread','未阅读'],['reading','阅读中'],['done','已读完']]){
        const opt=node('option',label,progress);opt.value=value;
      }
      progress.value=paper.reading_status||'unread';
      progress.onchange=()=>updatePaperFlags(paper,{reading_status:progress.value});
      const vote=node('button',paper.feedback===1?'✓ 已标记有价值':'有价值',actions,'feedback-btn');
      vote.type='button';vote.setAttribute('aria-pressed',String(paper.feedback===1));
      vote.onclick=()=>updatePaperFlags(paper,{feedback:paper.feedback===1?0:1});
      const upload=node('button','上传 PDF',actions);upload.type='button';upload.onclick=()=>uploadPaper(paper);
      const view=node('button','查看证据',actions);view.type='button';view.onclick=()=>previewPaper(paper);
      const analyze=node('button','分析 PDF',actions);analyze.type='button';analyze.onclick=async()=>{
        try{await submitResearch('/api/public/research/analyze',
          {paper_id:paper.id,cloud_analysis:$('cloudAnalysis').checked});navigate('tasks');}
        catch(e){alertUser(e.message,true);}};
      if(/^https:\/\//.test(paper.url)){
        const link=node('a','打开原文 ↗',actions,'paper-source-link');link.href=paper.url;
        link.target='_blank';link.rel='noopener noreferrer';
      }
    }
    paperTopic=requestedTopic;paperOffset=offset+data.papers.length;
    $('paperCount').textContent=`显示 ${paperOffset} / ${data.total} 篇`;
    $('loadPapers').hidden=paperOffset>=data.total;
  }
  function requestPaperRefresh(){
    focusedPaperId='';
    if(paperSearchTimer)clearTimeout(paperSearchTimer);
    paperSearchTimer=setTimeout(()=>refreshPapers().catch(e=>alertUser(e.message,true)),250);
  }
  $('paperSearch').addEventListener('input',requestPaperRefresh);
  $('paperFilter').addEventListener('change',()=>{focusedPaperId='';refreshPapers().catch(e=>alertUser(e.message,true));});
  $('paperSort').addEventListener('change',()=>{focusedPaperId='';refreshPapers().catch(e=>alertUser(e.message,true));});
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
    focusedPaperId='';fillTopicEditor();
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
      $('researchStage').textContent=busy?`当前：${stageLabels[job.stage]||'等待 Worker'}`:'';
      $('jobState').textContent=job.state==='queued'?`任务 #${job.id} 已排队（位置 ${job.queue_position||1}）`:
        job.state==='running'?`任务 #${job.id} 正在执行（第 ${job.attempts} 次）`:
        job.state==='success'?`任务 #${job.id} ${job.result?.state==='partial'?'部分完成，查看详情了解缺失成果':'完成'}`:
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
  $('searchNow').onclick=async()=>{try{
    const job=await api('/api/public/search','POST',{topic_id:currentTopic});
    $('jobState').textContent=`任务 #${job.job_id} 已提交`;
    await refreshJobState();
  }catch(err){alertUser(err.message,true);}};
  $('runResearch').onclick=async()=>{try{await submitResearch('/api/public/research/start',{fetch_pdf:$('fetchPdf').checked,cloud_analysis:$('cloudAnalysis').checked});}catch(e){alertUser(e.message,true);}};
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
  $('reportClose').onclick=()=>{$('reportReader').hidden=true;};
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
