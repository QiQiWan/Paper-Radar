// Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
'use strict';
// Human-readable research results. Every untrusted string is inserted as text.
window.PRInquiry = (() => {
  const make=(tag,parent,text='',cls='')=>{
    const el=document.createElement(tag);el.textContent=String(text??'');
    if(cls)el.className=cls;parent.appendChild(el);return el;
  };
  const button=(parent,label,act,cls='')=>{
    const el=make('button',parent,label,cls);el.type='button';el.addEventListener('click',act);return el;
  };
  const day=value=>{const date=new Date(value||'');return Number.isFinite(date.valueOf())?
    date.toLocaleString('zh-CN',{dateStyle:'short',timeStyle:'short'}):String(value||'');};
  function mount({root,request,mode='local',getTopic,onCompleted=null,onNavigate=null,getModelState=null,onModelSetup=null}){
    if(!root||typeof request!=='function'||typeof getTopic!=='function')throw Error('研究助手缺少配置');
    root.replaceChildren();root.classList.add('inquiry-workbench','inquiry-v1493','inquiry-v151','inquiry-v152');
    const header=make('div',root,'','inquiry-query-head');
    make('h2',header,'研究分析');
    make('p',header,'输入一个问题，先阅读论文摘要中的研究线索；需要比较实验数值时再核对 PDF。','inquiry-muted');
    const query=make('textarea',root,'','inquiry-question');query.rows=2;query.maxLength=500;
    query.placeholder='例如：神经算子在不同网格与几何条件下的泛化能力，现有实验有哪些一致结果和限制？';
    query.setAttribute('aria-label','研究问题');
    const selectRow=make('div',root,'','inquiry-select-row');
    const scopeLabel=make('label',selectRow,'分析哪些论文：','inquiry-scope-label');
    const scopeMode=make('select',scopeLabel,'','inquiry-scope-mode');
    [['recommended','自动推荐相关论文'],['all','当前主题全部论文'],['manual','手动选择论文']].forEach(([id,label])=>{
      const el=make('option',scopeMode,label);el.value=id;
    });
    const scopePreview=button(selectRow,'查看与调整选文',()=>loadPreview(true),'inquiry-scope-preview');
    const scopeInfo=make('p',root,mode==='local'?'默认先从当前主题筛选相关论文；可以查看或调整选文。':'分析当前研究主题的已有论文。','inquiry-scope-info');
    const picker=make('details',root,'','inquiry-picker');
    selectRow.hidden=mode!=='local';picker.hidden=mode!=='local';
    make('summary',picker,'论文选择 · 可搜索');
    const pickerSearch=make('input',picker);pickerSearch.type='search';pickerSearch.placeholder='按标题或论文编号查找…';
    pickerSearch.className='inquiry-picker-search';pickerSearch.setAttribute('aria-label','查找论文');
    const pickerCount=make('p',picker,'','inquiry-picker-count');
    const pickerList=make('div',picker,'','inquiry-picker-list');
    let preview=null,previewQuestion='',manualSet=new Set(),addedSet=new Set(),excludedSet=new Set();
    const normalized=()=>query.value.trim();
    const selectedForPreview=()=>{
      if(!preview)return [];
      if(scopeMode.value==='all')return preview.papers.map(p=>p.paper_id);
      if(scopeMode.value==='manual')return [...manualSet];
      return [...new Set([...preview.recommended_ids,...addedSet])].filter(x=>!excludedSet.has(x));
    };
    function drawPicker(){
      if(!preview)return;
      const chosen=new Set(selectedForPreview());
      const q=pickerSearch.value.trim().toLowerCase();
      const rows=[...preview.papers].sort((a,b)=>Number(b.recommended)-Number(a.recommended));
      pickerList.replaceChildren();
      for(const item of rows.filter(p=>!q||p.title.toLowerCase().includes(q)||p.paper_id.toLowerCase().includes(q)).slice(0,500)){
        const line=make('label',pickerList,'','inquiry-picker-paper');
        const cb=make('input',line);cb.type='checkbox';cb.checked=chosen.has(item.paper_id);
        cb.disabled=scopeMode.value==='all';
        cb.addEventListener('change',()=>{
          if(scopeMode.value==='manual'){
            if(cb.checked)manualSet.add(item.paper_id);else manualSet.delete(item.paper_id);
          }else if(scopeMode.value==='recommended'){
            if(cb.checked){excludedSet.delete(item.paper_id);if(!preview.recommended_ids.includes(item.paper_id))addedSet.add(item.paper_id);}
            else {addedSet.delete(item.paper_id);if(preview.recommended_ids.includes(item.paper_id))excludedSet.add(item.paper_id);}
          }
          const n=selectedForPreview().length;
          pickerCount.textContent='本次选择 '+n+' 篇论文';
          scopeInfo.textContent=`当前主题 ${preview.topic_total} 篇；本次分析 ${n} 篇。`;
        });
        const text=make('span',line,'','inquiry-picker-paper-text');
        make('strong',text,item.title||item.paper_id);
        make('small',text,(item.recommended?'系统推荐 · ':'')+
          (item.abstract_available?'有摘要':'缺摘要')+' · '+item.relevance_reason);
      }
      pickerCount.textContent='本次选择 '+chosen.size+' 篇论文；可搜索增删。';
      scopeInfo.textContent=`当前主题 ${preview.topic_total} 篇，匹配研究问题 ${preview.matched_count} 篇；本次将分析 ${chosen.size} 篇。`;
    }
    async function loadPreview(openPicker=false){
      if(mode!=='local'){scopeInfo.textContent='公网版默认使用当前主题的相关论文。';return null;}
      const question=normalized();
      if(question.length<8){message('请先输入至少 8 个字符的研究问题，以便选择论文。',true);return null;}
      try{
        scopePreview.disabled=true;scopeInfo.textContent='正在检查当前主题的论文相关性…';
        const payload=await request('/api/inquiry/preview?'+new URLSearchParams({question}));
        if(question!==normalized())return null;
        if(previewQuestion!==question){manualSet=new Set();addedSet=new Set();excludedSet=new Set();}
        preview=payload;previewQuestion=question;drawPicker();
        if(openPicker)picker.open=true;
        return payload;
      }catch(e){message('论文推荐暂时无法读取：'+e.message,true);return null;}
      finally{scopePreview.disabled=false;}
    }
    scopeMode.addEventListener('change',()=>{
      if(scopeMode.value==='all')scopePreview.textContent='查看分析范围';
      else scopePreview.textContent='查看与调整选文';
      if(preview)drawPicker();else scopeInfo.textContent='开始分析前可预览选文范围。';
    });
    pickerSearch.addEventListener('input',drawPicker);
    const bar=make('div',root,'','inquiry-actions inquiry-query-actions');
    const run=button(bar,'开始分析',()=>generate(),'inquiry-primary');
    const qualityWrap=make('div',root,'','inquiry-abstract-status');
    const qualityText=make('span',qualityWrap,'摘要质量检查中…','inquiry-muted');
    const qualityButton=button(qualityWrap,'补全摘要',()=>completeAbstracts(),'inquiry-abstract-repair');
    qualityButton.hidden=mode!=='local';
    const optionsWrap=make('details',root,'','inquiry-options-wrap');
    make('summary',optionsWrap,'分析设置与历史记录');
    const reload=button(optionsWrap,'刷新历史记录',()=>refresh());
    let model=null,confirm=null;
    if(mode==='local'){
      const settings=make('div',optionsWrap,'','inquiry-options');
      make('strong',settings,'AI 摘要辅助');
      const ml=make('label',settings,'','inquiry-option-line');
      model=make('input',ml);model.type='checkbox';model.checked=typeof getModelState==='function'?!!getModelState().enabled:true;
      make('span',ml,'模型已连接并获授权时，生成带来源的中文摘要解释');
      const cl=make('label',settings,'','inquiry-option-line');
      confirm=make('input',cl);confirm.type='checkbox';
      make('span',cl,'本次允许将研究问题与所选论文的题名、摘要片段分批发送给模型（可能收费）。');
      make('p',settings,'不勾选发送授权也能完成摘要研究分析；不会因为没有 PDF 或密钥而停止。','inquiry-muted');
      if(typeof onModelSetup==='function')button(settings,'配置模型连接',onModelSetup);
    }
    const evidenceSummary=make('p',optionsWrap,'正在检查当前主题的原文依据…','inquiry-evidence-hint');
    const state=make('p',root,'','inquiry-state');state.setAttribute('role','status');
    const history=make('details',optionsWrap,'','inquiry-history inquiry-history-disclosure');
    const historyTitle=make('summary',history,'历史研究记录');
    const historyItems=make('div',history,'','inquiry-history-items');
    const output=make('section',root,'','inquiry-output');
    // Keep the current result above historical notes and advanced settings.
    root.insertBefore(state,optionsWrap);
    root.insertBefore(output,optionsWrap);
    const progressBox=make('div',root,'','inquiry-progress');
    root.insertBefore(progressBox,state);
    progressBox.hidden=true;
    const progressTitle=make('strong',progressBox,'正在准备研究分析');
    const progressMeter=make('progress',progressBox);progressMeter.max=100;progressMeter.value=0;
    const progressDetail=make('p',progressBox,'','inquiry-muted');
    const continueButton=button(progressBox,'继续失败任务',()=>continueJob(),'inquiry-retry');
    continueButton.hidden=true;
    let currentJobId='';
    function setProgress(value,detail=''){
      progressBox.hidden=false;progressMeter.value=Math.max(0,Math.min(100,Number(value)||0));
      progressTitle.textContent=detail||'正在分析论文';
      progressDetail.textContent=progressMeter.value+'% · '+detail;
    }
    const expert=make('details',root,'','inquiry-expert-tools');
    make('summary',expert,'高级工具：PDF 实验数值核对与试点评估');
    let batchMounted=false;
    expert.addEventListener('toggle',()=>{
      if(expert.open&&!batchMounted&&mode==='local'&&window.PREvidenceBatch){
        batchMounted=true;window.PREvidenceBatch.mount({root:expert,request,getQuestion:()=>query.value,
          getModelState,onModelSetup});
      }
    });
    const prefix=mode==='local'?'/api/inquiry':'/api/public/inquiry';
    const params=(extra={})=>new URLSearchParams(mode==='local'?extra:{topic_id:getTopic(),...extra});
    let nonce=0,active='';
    async function updateQuality(){
      if(mode!=='local'){qualityText.textContent='摘要可以直接分析，PDF 用于进一步核对实验数值。';return;}
      try{
        const data=await request('/api/abstracts/quality');
        const q=data.by_quality||{};
        qualityText.textContent=`当前主题 ${data.total||0} 篇 · 较完整 ${q.adequate||0} 篇 · 较短 ${q.short||0} 篇 · 缺失/截断 ${(q.missing||0)+(q.possibly_truncated||0)} 篇 · 当前可补 ${data.needs_refresh||0} 篇。`;
        if(data.checked_without_improvement){
          qualityText.textContent+=` 已检查暂未改善 ${data.checked_without_improvement} 篇，暂不重复请求。`;
          const last=data.last_run||{};
          qualityText.title=`另有 ${data.checked_without_improvement} 篇已检查，暂无更完整的公开摘要。上次检查：${last.improved||0} 篇改善，${last.unavailable||0} 篇来源未提供。`;
        }
        qualityButton.disabled=!(data.needs_refresh>0);
        qualityButton.hidden=!(data.needs_refresh>0);
      }catch(e){qualityText.textContent='摘要状态暂不可用，仍可直接分析已有论文。';qualityButton.hidden=false;}
    }
    async function completeAbstracts(){
      qualityButton.disabled=true;
      try{
        await request('/api/abstracts/start','POST',{limit:60});
        message('摘要补全已加入后台任务，完成后将自动更新摘要数量。');
        qualityText.textContent='正在检查公开来源…';
        for(let i=0;i<600;i++){
          await new Promise(resolve=>setTimeout(resolve,1000));
          const status=await request('/api/state');
          if(!status.runtime?.busy){await updateQuality();break;}
        }
      }catch(e){message('摘要补全未启动：'+e.message,true);qualityButton.disabled=false;}
    }
    function message(text,err=false){state.textContent=text;state.classList.toggle('error',err);}
    function nextActions(parent,{pending=0}={}){
      const actions=make('div',parent,'','inquiry-next-actions');
      make('p',actions,'下一步：先精读关联论文，再核对需要引用的实验结果。');
      if(typeof onNavigate==='function'){
        button(actions,'去我的文献',()=>onNavigate('library'),'inquiry-primary');
        if(pending>0)button(actions,'查看未完成的来源',()=>onNavigate('following'));
      }
      return actions;
    }
    async function refresh(){
      const topic=getTopic(),current=++nonce;
      updateQuality();
      try{
        const overviewPath=mode==='local'?'/api/trace':'/api/public/trace?'+params();
        try{
          const overview=await request(overviewPath);
          if(current!==nonce||topic!==getTopic())return;
          const n=Number(overview.counts?.confirmed_current||0);
          evidenceSummary.textContent=n?`当前已有 ${n} 条经过人工核对的原文结论。`:
            '当前尚无已核对的 PDF 结论，可先进行题录和摘要层面的研究分析。';
        }catch(_){if(current===nonce)evidenceSummary.textContent='原文依据状态暂时无法读取，仍可使用题录分析。';}
        const results=await request(prefix+'?'+params());
        if(current!==nonce||topic!==getTopic())return;
        const records=results.reports||[];
        historyItems.replaceChildren();
        historyTitle.textContent='历史研究记录 · '+records.length+' 次';
        for(const entry of records){
          const row=make('div',historyItems,'','inquiry-history-row');
          button(row,entry.question,()=>open(entry.id),'inquiry-history-link');
          make('time',row,day(entry.created_at),'inquiry-muted');
        }
        if(!records.length)make('p',historyItems,'还没有研究分析记录。','inquiry-muted');
      }catch(e){message('历史记录加载失败：'+e.message,true);}
    }
    async function open(id){
      const current=++nonce,topic=getTopic();active=id;
      try{
        const data=await request(prefix+'/view?'+params({id}));
        if(current!==nonce||topic!==getTopic())return;
        render(data);
      }catch(e){message('研究分析结果无法读取：'+e.message,true);}
    }
    async function pollJob(id){
      currentJobId=id;continueButton.hidden=true;
      for(let i=0;i<2700;i++){
        if(!root.isConnected)return;
        const data=await request('/api/inquiry/job?'+new URLSearchParams({run_id:id}));
        setProgress(data.progress||0,data.detail||data.stage||'正在分析');
        if(['completed','partial'].includes(data.state)){
          run.disabled=false;progressBox.hidden=true;
          await refresh();await open(data.report_id||id);
          message(data.state==='partial'?'基础分析已生成；部分模型批次失败，可在结果中查看覆盖情况。':
                  '分析完成，已打开本次结果。');
          output.scrollIntoView({behavior:'smooth',block:'start'});
          if(typeof onCompleted==='function')onCompleted(data);
          return;
        }
        if(['failed','interrupted'].includes(data.state)){
          run.disabled=false;continueButton.hidden=false;
          message('分析未完成：'+(data.error_message||data.detail||'可从当前进度继续')+
             (data.error_code?'（'+data.error_code+'）':''),true);
          return;
        }
        await new Promise(resolve=>setTimeout(resolve,900));
      }
      run.disabled=false;continueButton.hidden=false;
      message('分析任务仍在后台执行，可以稍后返回或查看任务中心。',true);
    }
    async function continueJob(){
      if(!currentJobId)return;
      try{
        await request('/api/inquiry/resume','POST',{run_id:currentJobId});
        run.disabled=true;continueButton.hidden=true;await pollJob(currentJobId);
      }catch(e){message('无法续跑：'+e.message,true);}
    }
    async function generate(){
      const question=query.value.trim();
      if(question.length<8){message('请用至少 8 个字说明具体研究问题。',true);query.focus();return;}
      const readiness=typeof getModelState==='function'?getModelState():{ready:false};
      const modelRequested=!!model?.checked;
      run.disabled=true;picker.open=false;setProgress(1,'正在检查论文分析范围');
      const topic=getTopic();
      try{
        if(mode==='local'&&(!preview||previewQuestion!==question)){
          const available=await loadPreview(false);
          if(!available)throw Error('请先检查论文推荐范围');
        }
        const cloudAuthorized=!!model?.checked&&!!readiness.ready&&!!confirm?.checked;
        const range=scopeMode.value;
        const payload=mode==='local'?{question,use_model:cloudAuthorized,
            confirm_transfer:cloudAuthorized,selection_mode:range,
            ...(range==='manual'?{paper_ids:[...manualSet]}:{}),
            ...(range==='recommended'?{include_ids:[...addedSet],exclude_ids:[...excludedSet]}:{})}:
            {topic_id:topic,question};
        if(mode==='local'&&range==='manual'&&!manualSet.size){
          picker.open=true;throw Error('请至少勾选一篇论文');
        }
        setProgress(2,'任务已提交，正在开始分析');
        if(mode==='local'){
          const job=await request('/api/inquiry/jobs','POST',payload);
          if(topic!==getTopic())return;
          await pollJob(job.run_id);
        }else{
          const data=await request(prefix+'/run','POST',payload);
          if(topic!==getTopic())return;
          active=data.id;await refresh();await open(active);
          progressBox.hidden=true;message('分析完成，已打开结果。');
          output.scrollIntoView({behavior:'smooth',block:'start'});
          if(typeof onCompleted==='function')onCompleted(data);
        }
        if(modelRequested&&!readiness.ready)message('已完成本地摘要分析；模型未连接，本次没有发送数据。');
      }catch(e){message('分析未开始或执行中断：'+e.message,true);continueButton.hidden=!currentJobId;}
      finally{run.disabled=false;}
    }
    function render(record){
      const brief=record.brief||{};output.replaceChildren();
      const coverage=brief.coverage||{};
      const top=make('div',output,'','inquiry-result-top');
      const title=make('div',top,'','inquiry-result-title');
      make('h3',title,'研究分析结果');
      make('p',title,brief.question||'','inquiry-question-repeat');
      const modelUsed=String(brief.model?.status||'disabled');
      const modelLabel=modelUsed==='model_partially_completed'?'AI 部分批次失败 · 结果已保存':
        modelUsed.startsWith('model_failed')?'AI 调用失败 · 已保留本地分析':
        ['model_screened_metadata','reordered_existing_claims'].includes(modelUsed)?'AI 已参与 · 建议需核实':
        'AI 未参与 · 本地筛选';
      make('small',title,modelLabel,'inquiry-model-state');
      const statuses={current:'原文证据有效',needs_refresh:'部分依据需重新验证',
        no_confirmed_evidence:'题录分析完成 · 实验结论待核实'};
      const status=make('span',top,statuses[record.snapshot_status]||'研究分析已保存','inquiry-status');
      status.dataset.status=record.snapshot_status||'';
      const metaActions=make('details',output,'','inquiry-result-tools inquiry-download-tools');
      make('summary',metaActions,'导出与报告信息');
      button(metaActions,'下载 Markdown',()=>{
        const blob=new Blob([record.markdown],{type:'text/markdown;charset=utf-8'});
        const url=URL.createObjectURL(blob);const a=document.createElement('a');
        a.href=url;a.download='research-inquiry.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      });
      const stamp=make('span',metaActions,'生成时间 '+day(brief.generated_at),'inquiry-muted');
      stamp.setAttribute('title',brief.generated_at||'');
      const chosen=brief.selection||{};
      const summary=make('p',top,'','inquiry-selection-summary');
      summary.textContent=`分析范围：${{recommended:'智能推荐',all:'当前主题全部论文',manual:'手动选文'}[chosen.mode]||'当前主题'} · 已选 ${chosen.selected_count||0} 篇 · 可用摘要 ${chosen.abstract_available_count||0} 篇`;
      const modelBatches=brief.model||{};
      if(chosen.mode==='all'){
        const batches=make('p',top,'','inquiry-batch-summary');
        batches.textContent=modelBatches.status==='disabled'?
          `已分为 ${(brief.analysis_batches||[]).length} 批完成本地摘要整理，本次未调用模型。`:
          `本次分为 ${(brief.analysis_batches||[]).length} 批；模型提交 ${modelBatches.batch_attempted||0} 批、成功 ${modelBatches.batch_succeeded||0} 批、失败 ${modelBatches.batch_failed||0} 批。`;
      }
      const stats=make('div',output,'','inquiry-stats');
      for(const [value,label] of [
        [coverage.papers_scanned||0,'当前主题论文'],[chosen.selected_count??coverage.candidate_matches??0,'实际选中文献'],
        [chosen.abstract_available_count??0,'有摘要可分析']]){
        const item=make('div',stats,'','inquiry-stat');make('strong',item,String(value));make('span',item,label);
      }
      if(coverage.papers_truncated||coverage.evidence_truncated){
        make('p',output,'部分文献未纳入本次分析，可缩小研究主题或分批检索。','inquiry-warning');
      }
      const scope=make('div',output,'','inquiry-scope');
      make('strong',scope,brief.findings?.length?'本次包含可追溯原文的实验结论':'已完成摘要层面分析');
      make('p',scope,brief.findings?.length?'实验结论附 PDF 页码；下面的摘要研究线索仍需标识为未核对全文。':
        '下方已按研究方法、实验设置、作者报告的结果和局限整理摘要原文。需要比较误差数值或判断方法优劣时，再使用 PDF 审核工具；摘要线索无法单独支持可靠的实验比较结论。');
      if(coverage.source_tracking?.pending_sources){
        make('p',scope,`另有 ${coverage.source_tracking.pending_sources} 个文献来源尚未完成检索，当前覆盖可能不完整。`,'inquiry-muted');
      }
      if((brief.research_differences||[]).length){
        const differences=make('section',output,'','inquiry-difference-section');
        make('h4',differences,'研究差异与覆盖范围（摘要层面）');
        for(const item of brief.research_differences){
          const line=make('p',differences,'','inquiry-difference-line');
          line.textContent=`${{mesh:'网格',geometry:'几何',generalization:'泛化',physics:'物理约束',comparison:'实验比较'}[item.facet]||item.facet}：${item.paper_count} 篇涉及该维度，待结合原文核对实验条件。`;
        }
      }
      if((brief.abstract_findings||[]).length){
        const section=make('section',output,'','inquiry-abstract-findings');
        make('h4',section,'AI 摘要解读（需核对）');
        for(const item of brief.abstract_findings.slice(0,4)){
          const card=make('article',section,'','inquiry-abstract-note');
          make('p',card,item.observation);
          for(const ref of item.sources||[]){
            const source=make('details',card,'','inquiry-abstract-reference');
            make('summary',source,`查看摘要来源 · ${ref.paper_id}`);
            make('blockquote',source,ref.quote);
          }
        }
      }
      if((brief.abstract_insights||[]).length){
        const section=make('section',output,'','inquiry-abstract-insights');
        make('h4',section,'从摘要可以了解什么');
        make('p',section,'以下均为原始摘要中的文字，按研究方法、实验设置、作者报告结果和局限整理。可直接用于阅读筛选，尚未独立核查全文。','inquiry-muted');
        const labels=['研究方法','实验设置','摘要报告的结果','局限与适用范围'];
        for(const label of labels){
          const items=brief.abstract_insights.filter(x=>x.label===label);
          if(!items.length)continue;
          const block=make('div',section,'','inquiry-insight-group');
          make('strong',block,label);
          make('small',block,`来源线索 ${items.length} 条；优先显示 3 条，其余可展开。`,'inquiry-muted');
          const extras=items.length>3?make('details',block,'','inquiry-abstract-extras'):null;
          if(extras)make('summary',extras,`查看其余 ${items.length-3} 条`);
          for(const [index,item] of items.entries()){
            const card=make('article',index<3?block:extras,'','inquiry-insight-item');
            const a=make('a',card,item.title,'inquiry-insight-source');
            a.href=mode==='local'?'/app/library?paper='+encodeURIComponent(item.paper_id):
              '/library?topic='+encodeURIComponent(getTopic())+'&paper='+encodeURIComponent(item.paper_id);
            make('p',card,item.quote);
          }
        }
      }
      if(record.issues?.length){
        const issues=make('details',output,'','inquiry-secondary-detail');
        make('summary',issues,'已保存依据的有效性提醒');
        for(const issue of record.issues)make('p',issues,issue,'inquiry-muted');
      }
      if((brief.findings||[]).length){
        const findings=make('section',output,'','inquiry-confirmed');make('h4',findings,'已确认的研究结论');
        for(const entry of brief.findings){
          const card=make('article',findings,'','inquiry-finding');
          make('p',card,entry.body,'inquiry-claim');
          make('small',card,`人工已确认 · 修订 v${entry.revision}`,'inquiry-muted');
          for(const src of entry.sources){
            const ref=make('div',card,'','inquiry-source');
            make('strong',ref,src.title+' · PDF 第 '+src.page+' 页');
            make('blockquote',ref,src.quote);
            const link=make('a',ref,'查看原文页码');
            link.href=mode==='local'?'/api/pdf/'+encodeURIComponent(src.paper_id)+'#page='+src.page:
              '/api/public/pdf/view?'+new URLSearchParams({topic_id:getTopic(),paper_id:src.paper_id})+'#page='+src.page;
            link.target='_blank';link.rel='noopener noreferrer';
          }
        }
      }
      const manual=record.reviewed_experiments||{};
      if(Number(manual.approved_valid||0)>0){
        const verified=make('section',output,'','inquiry-confirmed');
        make('h4',verified,'已人工核对的实验数据 · '+manual.approved_valid+' 条');
        make('p',verified,'数据来自目前有效的 PDF 原文审核记录，可查看对应页码。跨论文的指标定义和实验条件尚未确认可比，不进行自动优劣排名。','inquiry-muted');
        for(const group of manual.groups||[]){
          const card=make('article',verified,'','inquiry-finding');
          make('strong',card,group.metric+' · '+(group.dataset||'数据集未注明'));
          make('p',card,group.note,'inquiry-muted');
          for(const rec of group.entries||[]){
            const line=make('div',card,'','inquiry-source');
            make('p',line,rec.title+' · '+rec.value+' · PDF 第 '+rec.page+' 页');
            make('blockquote',line,rec.quote);
            const link=make('a',line,'查看原文第 '+rec.page+' 页');
            link.href='/api/pdf/'+encodeURIComponent(rec.paper_id)+'#page='+rec.page;
            link.target='_blank';link.rel='noopener noreferrer';
          }
        }
        if(!manual.groups?.length)make('p',verified,'当前还没有两篇论文在同一数据集和指标下形成并列对照。','inquiry-muted');
      }
      if((brief.topic_map||[]).length){
        const map=make('section',output,'','inquiry-topic-section');
        make('h4',map,'论文涉及的研究方向');
        make('p',map,'根据题名和摘要中的明确表述分组，用来确定优先阅读的方向。','inquiry-muted');
        const cards=make('div',map,'','inquiry-topic-grid');
        for(const item of brief.topic_map){
          const card=make('article',cards,'','inquiry-topic-card');
          const cardHeader=make('div',card,'','inquiry-topic-heading');
          make('strong',cardHeader,item.title);make('span',cardHeader,String(item.candidate_count_in_shortlist)+' 篇');
          make('p',card,item.question_to_verify,'inquiry-muted');
        }
      }
      const papers=make('section',output,'','inquiry-paper-section');
      const all=brief.metadata_candidates||[];
      make('h4',papers,'相关论文 · '+all.length+' 篇');
      make('p',papers,'论文与摘要来自已收录题录；下列匹配等级代表检索相关性，不代表方法性能。','inquiry-muted');
      function paperCard(container,item){
        const card=make('article',container,'','inquiry-paper-card');
        const titleRow=make('div',card,'','inquiry-paper-head');
        make('strong',titleRow,item.title,'inquiry-paper-title');
        const levels={high:'重点相关',medium:'相关',related:'领域相关',unmatched:'待筛选'};
        make('span',titleRow,levels[item.relevance_level]||'题录匹配','inquiry-paper-level');
        make('p',card,[(item.published||'日期未知'),(item.matched_labels||[]).slice(0,3).join(' · '),
          item.abstract_available?'有摘要':'缺摘要'].filter(Boolean).join('  ·  '),'inquiry-paper-meta');
        if(item.abstract_excerpt){
          make('p',card,item.abstract_excerpt,'inquiry-paper-excerpt');
          const details=make('details',card,'','inquiry-paper-excerpt-more');
          make('summary',details,'查看摘要相关片段');
          make('p',details,item.abstract_excerpt,'inquiry-meta-paper');
        }
        const actions=make('div',card,'','inquiry-paper-actions');
        const link=make('a',actions,'去阅读论文','inquiry-paper-link');
        link.href=mode==='local'?'/app/library?paper='+encodeURIComponent(item.paper_id):
          '/library?topic='+encodeURIComponent(getTopic())+'&paper='+encodeURIComponent(item.paper_id);
        make('span',actions,'原文待核实','inquiry-muted');
      }
      for(const item of all.slice(0,3))paperCard(papers,item);
      if(all.length>3){
        const rest=make('details',papers,'','inquiry-rest');
        make('summary',rest,'查看更多相关论文（'+(all.length-3)+' 篇）');
        for(const item of all.slice(3))paperCard(rest,item);
      }
      if(!all.length)make('p',papers,'当前题录未找到直接相关的论文，可以调整主题范围或继续检索。','inquiry-muted');
      if((brief.model_suggestions||[]).length){
        const advice=make('section',output,'','inquiry-advice');make('h4',advice,'模型建议进一步核实的问题');
        for(const item of brief.model_suggestions){
          const card=make('div',advice,'','inquiry-question-card');
          make('p',card,item.question);make('small',card,`关联题录 ${item.paper_ids.length} 篇 · 待核实`,'inquiry-muted');
        }
      }
      const next=make('section',output,'','inquiry-next-section');
      make('h4',next,'下一步');nextActions(next,{pending:Number(coverage.source_tracking?.pending_sources||0)});
      const details=make('details',output,'','inquiry-secondary-detail');
      make('summary',details,'分析范围、证据缺口与运行说明');
      const diag=make('p',details,'','inquiry-muted');
      const modelStatus=brief.model?.status||'disabled';
      const statusTexts={disabled:'本次未调用模型',skipped_no_confirmed_claims:'没有已确认结论，模型未执行排序',
        skipped_no_metadata:'没有候选论文，模型未调用',model_screened_metadata:'模型生成了待核实阅读问题',
        reordered_existing_claims:'模型调整了已确认结论顺序',model_failed_local_screening_used:'模型调用失败，已保留本地题录筛选',
        model_failed_offline_order_used:'模型调用失败，已保留原有结论顺序',
        model_partially_completed:'模型部分批次失败，已保存有效部分'};
      diag.textContent=(statusTexts[modelStatus]||modelStatus)+' · 运行编号：'+(brief.diagnostic_run_id||'未记录');
      for(const gap of brief.gaps||[])make('p',details,gap.title+'：'+gap.detail,'inquiry-muted');
      for(const group of brief.reading_roadmap||[])make('p',details,group.title+'：'+group.next_check,'inquiry-muted');
      for(const item of brief.disagreement_candidates||[])make('p',details,item.dataset+' / '+item.metric+'：'+item.note,'inquiry-muted');
      if(window.PRMath?.renderAll)window.PRMath.renderAll();
    }
    if(mode==='local')request('/api/inquiry/latest').then(job=>{
      if(['queued','running'].includes(job.state)&&job.id){
        run.disabled=true;pollJob(job.id).catch(e=>message('恢复进度失败：'+e.message,true));
      }else if(['failed','interrupted'].includes(job.state)&&job.id){
        currentJobId=job.id;continueButton.hidden=false;
        message('上次研究任务中断，可以点击“继续失败任务”。');
      }
    }).catch(()=>{});
    return {refresh,open,getQuestion:()=>query.value,setQuestion:text=>{query.value=String(text||'').slice(0,500)}};
  }
  return {mount};
})();
