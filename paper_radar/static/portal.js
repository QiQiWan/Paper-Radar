'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let csrf = '', currentUser = null, currentTopic = 'default', lastReport = '';
  let openedReportId = '', displayedJobId = 0;
  let register = false;
  let jobTimer = null, lastFinishedJob = null;
  let paperOffset=0, paperTopic='', paperQueryNonce=0, paperSearchTimer=null;
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
    if (!result.ok) throw new Error(data.error || `请求失败 HTTP ${result.status}`);
    return data;
  }
  const stageLabels={starting:'准备执行',discovery:'发现论文',download:'准备 PDF',
    fulltext:'解析全文',evidence:'核验实验',synthesis:'生成报告',task:'整个任务'};
  const stageStatus={started:'正在执行',completed:'已完成',failed:'失败',cancelled:'已取消'};
  const taskKinds={search:'公开检索',research:'完整科研分析',analyze:'单篇 PDF 分析',synthesize:'跨论文比较'};
  const views={workspace:['工作台','WORKSPACE'],library:['论文库','LIBRARY'],
    tasks:['任务中心','TASKS'],reports:['研究报告','REPORTS'],account:['我的账号','ACCOUNT']};
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
  $('quickStart').onclick=()=>navigate('tasks');
  $('homeOpenTasks').onclick=()=>navigate('tasks');
  $('homeOpenLibrary').onclick=()=>navigate('library');
  $('libraryRunSearch').onclick=()=>navigate('tasks');
  async function refreshDashboard(){
    const topic=currentTopic;
    const data=await api('/api/public/dashboard?topic_id='+encodeURIComponent(topic));
    if(topic!==currentTopic)return;
    $('metricPapers').textContent=data.papers;
    $('metricStarred').textContent=data.starred;
    $('metricRead').textContent=data.read;
    $('metricReports').textContent=data.reports;
    const detail=currentTopics.find(t=>t.id===topic);
    $('topicQuickSummary').textContent=detail?
      `${detail.name} · ${(detail.keywords||[]).join('、')}`:'请选择研究主题';
    const recent=$('homeRecentPapers');recent.replaceChildren();
    if(!data.recent.length){
      node('p','尚未收录论文。进入任务中心，点击“仅检索 arXiv”开始第一轮跟踪。',recent,'hint');
      const start=node('button','开始检索',recent,'primary');start.onclick=()=>navigate('tasks');
    }else for(const paper of data.recent){
      const entry=node('div','',recent,'recent-paper');
      const title=node('button',paper.title,entry,'quiet-link recent-title');
      title.onclick=()=>{navigate('library');$('paperSearch').value='';
        $('paperFilter').value='all';refreshPapers().catch(e=>alertUser(e.message,true));};
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
    if(job.state==='failed')details.push(`失败原因：${job.error||'请联系管理员查看服务器日志'}`);
    if(job.state==='cancelled')details.push('任务已取消。已完成的入库结果仍保留。');
    if(job.state==='running')details.push('正在执行；刷新页面不会终止后台任务。');
    $('jobOutcome').textContent=details.join('；')||'尚无已保存的阶段统计。';
    const btn=$('openTaskReport');const reportId=result.report_id;
    btn.hidden=!reportId;
    btn.onclick=reportId?()=>openReport(reportId,{scroll:true}).catch(e=>alertUser(e.message,true)):null;
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
      const flag=node('p','',section,'matrix-warning');
      flag.textContent=(group.flags||[]).join('；')||'指标定义、数据划分、单位和计算预算仍需人工核对。';
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
        const verified=rec.provenance==='pdf_sha256_verified';
        node('td',verified?'本地 PDF 已核验':'导入数据，待复核',row,verified?'matrix-verified':'matrix-unverified');
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
      if(file.size<256 || file.size>12*1024*1024) return alertUser('PDF 大小应在 256B—12MiB 之间',true);
      try{const res=await fetch('/api/public/pdf',{method:'PUT',credentials:'same-origin',
          headers:{'Content-Type':'application/pdf','X-Portal-Action':'1','X-CSRF-Token':csrf,
            'X-Paper-ID':paper.id,'X-Topic-ID':currentTopic},body:file});
        const data=await res.json();if(!res.ok)throw Error(data.error||'上传失败');
        alertUser('PDF 已保存到当前账号。请点击“分析 PDF”。');
      }catch(e){alertUser(e.message,true);}
    };field.click();
  }
  async function previewPaper(paper,page=1) {
    try{
      const requestedTopic=currentTopic;
      const requestedPage=Math.max(1,Math.min(40,Number(page)||1));
      const q=new URLSearchParams({topic_id:requestedTopic,paper_id:paper.id,page:String(requestedPage)});
      const res=await api('/api/public/fulltext?'+q);
      if(requestedTopic!==currentTopic)return;
      navigate('library');
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
      input.min='1';input.max=String(Math.min(40,res.document.pages||40));input.value=String(requestedPage);
      const go=node('button','跳转',jump);go.type='button';
      go.onclick=()=>previewPaper(paper,Number(input.value)||1);
      const matches=Array.isArray(res.evidence)?res.evidence:[];
      node('p',`第 ${requestedPage} 页 · ${matches.length} 条已核对结构化证据`,side,'hint');
      for(const e of matches){
        const card=node('article','',side,'pdf-evidence-card');
        node('strong',`${e.metric||'实验指标'}：${e.value||'—'}`,card);
        node('p',[e.dataset,e.baseline?'对照 '+e.baseline:'',e.setting].filter(Boolean).join(' · '),card,'hint');
        node('blockquote',e.quote||'未提供原文片段',card);
      }
      if(!matches.length)node('p','当前页暂未提取到可核验的实验数值。可在下面阅读已解析文本，必要时手动核查 PDF 中的表格和公式。',side,'hint');
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
    register = isRegister;
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
      opt.textContent=t.name+' · '+t.keywords.join(', '); select.appendChild(opt);
    }
    if (data.topics.some(t=>t.id===currentTopic)) select.value=currentTopic;
    else currentTopic=select.value;
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
    const data=await api('/api/public/papers?'+params.toString());
    if(requestedTopic!==currentTopic||nonce!==paperQueryNonce||
      search!==$('paperSearch').value.trim()||filter!==$('paperFilter').value||sort!==$('paperSort').value)return;
    const target=$('paperList');
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
      node('small',[paper.published,paper.doi].filter(Boolean).join(' · ')||'未提供日期或 DOI',el);
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
    if(paperSearchTimer)clearTimeout(paperSearchTimer);
    paperSearchTimer=setTimeout(()=>refreshPapers().catch(e=>alertUser(e.message,true)),250);
  }
  $('paperSearch').addEventListener('input',requestPaperRefresh);
  $('paperFilter').addEventListener('change',()=>refreshPapers().catch(e=>alertUser(e.message,true)));
  $('paperSort').addEventListener('change',()=>refreshPapers().catch(e=>alertUser(e.message,true)));
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
    $('reportReader').hidden=true;$('downloadSynthesis').disabled=true;
    $('summary').textContent='选择历史报告或排队生成新的比较报告。';
    try{navigate(currentView,{push:true,scroll:false});
      await Promise.all([refreshDashboard(),refreshPapers(),refreshReports()]);}
    catch(e){alertUser(e.message,true);}
  };
  $('topicAdd').onclick=async()=>{try{
    const words=$('newKeywords').value.split(/[,，;；]+/).map(v=>v.trim()).filter(Boolean);
    const info=await api('/api/public/topics','POST',{name:$('newTopic').value,keywords:words});
    currentTopic=info.id;navigate(currentView,{scroll:false});await refreshTopics();
    $('newTopic').value='';$('newKeywords').value='';$('topicSetup').open=false;alertUser('研究主题已建立');
  }catch(err){alertUser(err.message,true);}};
  const jobLabels = {queued:'等待执行',running:'正在执行',success:'已完成',
    failed:'执行失败',cancelled:'已取消',idle:'没有检索任务'};
  const jobActive = state => state === 'running' || state === 'queued';
  function cancelPolling() { if (jobTimer) clearTimeout(jobTimer); jobTimer = null; }
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
    }
  }
  async function refreshJobState(){
    cancelPolling();if(!currentUser)return;
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
      if(busy)jobTimer=setTimeout(refreshJobState,2300);
      else await refreshJobHistory();
    }catch(err){
      $('jobState').textContent='任务状态暂时不可用：'+err.message;
      alertUser('任务状态查询失败，请检查网络后点击“刷新任务状态”。',true);
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
  restore();
})();
