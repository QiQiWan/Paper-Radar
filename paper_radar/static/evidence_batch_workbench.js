// Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
'use strict';
// Batch PDF evidence preparation: every untrusted field is inserted via textContent.
window.PREvidenceBatch = (() => {
  const make=(tag,parent,label='',cls='')=>{
    const el=document.createElement(tag);el.textContent=String(label??'');
    if(cls)el.className=cls;parent.append(el);return el;
  };
  const btn=(parent,label,handler,cls='')=>{
    const item=make('button',parent,label,cls);item.type='button';item.addEventListener('click',handler);return item;
  };
  function mount({root,request,getQuestion,getModelState,onModelSetup}){
    const panel=make('section',root,'','batch-workbench');
    const header=make('div',panel,'','batch-header');
    const title=make('div',header,'');make('h3',title,'批量准备原文证据');
    make('p',title,'自动整理高相关论文的 PDF、实验片段和页码，再集中人工确认。','batch-muted');
    const previewBtn=btn(header,'选择相关论文',preview,'batch-button batch-button-primary');
    const state=make('div',panel,'','batch-state');state.setAttribute('role','status');
    const options=make('div',panel,'','batch-options');
    const summary=make('div',panel,'','batch-summary');
    const queue=make('div',panel,'','batch-queue');
    const compare=make('div',panel,'','batch-compare');
    const audit=make('details',panel,'','batch-pilot-audit');
    make('summary',audit,'试点评估：下载率、抽取准确率与人工审核耗时');
    const auditBody=make('div',audit,'','batch-pilot-body');
    async function pilotOverview(){
      auditBody.replaceChildren();
      try{
        const stat=await request('/api/pilot/summary');
        const fmt=value=>value===null||value===undefined?'尚无实测':String(value);
        const line=(label,value)=>{
          const row=make('div',auditBody,'','batch-pilot-stat');
          make('span',row,label);make('strong',row,fmt(value));
        };
        line('文献库总量',stat.library_total+' 篇');
        line('已纳入真实试点',stat.pilot_selected_unique+' 篇');
        line('已取得 PDF',stat.downloaded_or_existing_pdf+' 篇');
        line('PDF 获取率',stat.pdf_acquisition_rate===null?null:(stat.pdf_acquisition_rate*100).toFixed(1)+'%');
        line('已索引全文',stat.fulltext_indexed+' 篇');
        line('有人工标注的字段正确率',stat.field_accuracy_on_reviewed_fields===null?null:
             (stat.field_accuracy_on_reviewed_fields*100).toFixed(1)+'%（'+stat.field_accuracy_denominator+' 个已判定字段）');
        line('平均实际核对耗时',stat.mean_active_review_seconds===null?null:
             stat.mean_active_review_seconds+' 秒（'+stat.timed_reviews+' 条记录）');
        line('可能形成的对照组',stat.potential_comparison_groups+' 组');
        line('已人工确认可比的对照组',stat.method_confirmed_comparison_groups+' 组');
        make('p',auditBody,'尚未人工计时或标注的数据不会被计为 0；准确率不等于召回率。','batch-muted');
        const links=make('div',auditBody,'','batch-inline-actions');
        const a=make('a',links,'下载本次试点统计 ZIP','batch-button');
        a.href='/api/pilot/export';
        btn(links,'刷新统计',pilotOverview,'batch-button');
      }catch(e){make('p',auditBody,'试点统计读取失败：'+e.message,'batch-error');}
    }
    audit.addEventListener('toggle',()=>{if(audit.open)pilotOverview();});
    let runId='',busy=false,latest=null,poll=null,chosen=new Set();
    const message=(text,error=false)=>{state.textContent=text;state.classList.toggle('batch-error',error);};
    function stopPoll(){if(poll){clearTimeout(poll);poll=null;}}
    function schedule(){stopPoll();poll=setTimeout(()=>{if(panel.isConnected)refresh();},3000);}
    const input=(parent,label,value='',maxLength=220)=>{
      const wrap=make('label',parent,'','batch-input-field');make('span',wrap,label);
      const field=make('input',wrap);field.type='text';field.value=String(value??'');field.maxLength=maxLength;return field;
    };
    async function preview(){
      if(busy)return;
      const question=String(getQuestion()||'').trim();
      if(question.length<8){message('请先填写上方研究问题（至少 8 个字符）。',true);return;}
      busy=true;previewBtn.disabled=true;message('正在按研究问题筛选已收录论文…');
      try{
        const result=await request('/api/evidence-batch/preview','POST',{question,limit:12});
        options.replaceChildren();chosen.clear();
        if(!result.papers?.length){message('当前主题没有直接匹配的论文。请先扩展检索，再尝试批量准备。');return;}
        make('p',options,`匹配 ${result.matches} 篇；以下最多显示 12 篇，请选择需要准备的论文。`,'batch-muted');
        const list=make('div',options,'','batch-select-list');
        for(const paper of result.papers){
          const label=make('label',list,'','batch-select-paper');
          const check=make('input',label);check.type='checkbox';check.checked=true;chosen.add(paper.paper_id);
          check.addEventListener('change',()=>check.checked?chosen.add(paper.paper_id):chosen.delete(paper.paper_id));
          const details=make('div',label,'');make('strong',details,paper.title);
          make('small',details,`${paper.reason} · ${paper.has_pdf?'本地已有 PDF':'需要获取 PDF'}`,'batch-muted');
        }
        const permissions=make('div',options,'','batch-permissions');
        const readiness=typeof getModelState==='function'?getModelState():{ready:false,enabled:false};
        const modelLabel=make('label',permissions,'','batch-checkline');
        const model=make('input',modelLabel);model.type='checkbox';model.checked=!!readiness.ready&&!!readiness.enabled;
        make('span',modelLabel,readiness.ready?'使用已连接模型提取结构化实验数据':'当前无可用模型，将只整理本地原文候选');
        if(!readiness.ready){model.disabled=true;if(typeof onModelSetup==='function')btn(permissions,'连接模型',onModelSetup,'batch-button');}
        const confirmLabel=make('label',permissions,'','batch-checkline');
        const consent=make('input',confirmLabel);consent.type='checkbox';
        make('span',confirmLabel,'同意本批所选 PDF 的有限原文片段发送给模型服务（可能收费）。');
        confirmLabel.hidden=!model.checked;
        model.addEventListener('change',()=>{confirmLabel.hidden=!model.checked;});
        const runBtn=btn(permissions,'开始批量准备',async()=>{
          if(!chosen.size){message('至少选择一篇论文。',true);return;}
          if(model.checked&&!consent.checked){message('使用模型前需确认本批数据发送范围；也可关闭模型。',true);return;}
          runBtn.disabled=true;
          try{
            const started=await request('/api/evidence-batch/start','POST',{
              question,paper_ids:[...chosen],use_model:!!model.checked,
              confirm_transfer:!!model.checked&&!!consent.checked});
            runId=started.run_id;options.replaceChildren();message('任务已提交。PDF 下载和解析将逐篇进行，刷新页面后仍可恢复查看。');
            await refresh();
          }catch(e){message('任务提交失败：'+e.message,true);runBtn.disabled=false;}
        },'batch-button batch-button-primary');
        message('请选择论文，再开始准备证据。');
      }catch(e){message('筛选失败：'+e.message,true);}finally{busy=false;previewBtn.disabled=false;}
    }
    async function refresh(){
      try{
        const suffix=runId?'?run_id='+encodeURIComponent(runId):'';
        const data=await request('/api/evidence-batch/status'+suffix);
        if(!data.run){summary.replaceChildren();queue.replaceChildren();return;}
        latest=data;runId=data.run.id;
        render(data);
        if(['running','queued'].includes(data.run.state))schedule();else stopPoll();
      }catch(e){message('批量任务暂时无法读取：'+e.message,true);stopPoll();}
    }
    function render(data){
      summary.replaceChildren();queue.replaceChildren();
      const items=data.items||[],count=data.counts||{};
      const stages={queued:'等待处理',running:'处理中',ready_for_review:'可审核',no_pdf:'无开放 PDF',no_text:'全文不可读',no_experiment:'未检出实验数值',failed:'处理失败'};
      const top=make('div',summary,'','batch-status-head');
      make('strong',top,`最近批次 · ${items.length} 篇 · ${data.run.state==='running'?'处理中':data.run.state==='complete'?'准备完成':'部分未完成'}`);
      make('span',top,`待确认 ${count.pending||0} 条 · 已确认 ${count.approved||0} 条`,'batch-muted');
      const modelStatus={not_requested:'本批未使用模型',not_ready:'模型未连接或授权不足，本地片段模式',
        structured_results:'已有结构化模型候选',partial:'部分模型提取失败，已保留本地片段',
        failed_fallback:'模型提取失败，改用本地原文片段',
        no_structured_results:'模型未产出可核对的结构化实验字段'};
      if(data.run.model_status&&data.run.model_status!=='not_requested')
        make('p',summary,modelStatus[data.run.model_status]||'模型状态未知','batch-muted');
      const actions=make('div',summary,'','batch-inline-actions');
      btn(actions,'刷新状态',refresh,'batch-button');
      btn(actions,'继续未完成任务',async()=>{
        try{await request('/api/evidence-batch/resume','POST',{run_id:runId,retry_unavailable:false});message('已继续处理失败或中断的论文。');await refresh();}
        catch(e){message('续跑失败：'+e.message,true);}
      },'batch-button');
      btn(actions,'重试未完成或失效的论文',async()=>{
        try{await request('/api/evidence-batch/resume','POST',{run_id:runId,retry_unavailable:true});message('已重新检查无法获取的 PDF。');await refresh();}
        catch(e){message('重试失败：'+e.message,true);}
      },'batch-button');
      btn(actions,'查看跨论文对照',loadComparison,'batch-button batch-button-primary');
      const history=make('details',summary,'','batch-stage-detail');make('summary',history,'查看每篇论文的处理进度');
      for(const row of items){const line=make('div',history,'','batch-item');
        make('span',line,row.title,'batch-item-title');
        make('span',line,`${stages[row.state]||row.state} · ${row.candidate_count||0} 条候选`,'batch-muted');}
      const candidates=(data.candidates||[]).filter(c=>c.effective_status==='pending');
      if(candidates.length){make('h4',queue,'待人工确认的实验片段 · '+candidates.length+' 条');
        make('p',queue,'请打开 PDF 核对引用，填写原文中明确出现的指标与数值，再手动确认。','batch-muted');}
      for(const item of candidates.slice(0,30))candidateCard(queue,item);
      if(!candidates.length&&items.length)make('p',queue,'当前没有待审核片段。可以查看处理进度或跨论文对照。','batch-muted');
      if(count.stale)make('p',queue,`${count.stale} 条历史引文已因 PDF 变动失效，需要重新解析。`,'batch-warning');
    }
    function candidateCard(parent,item){
      const card=make('article',parent,'','batch-candidate');
      const head=make('div',card,'','batch-candidate-head');
      make('strong',head,item.title);make('small',head,`PDF 第 ${item.page} 页 · ${item.source==='model_verified_quote'?'模型候选':'本地片段'}`,'batch-muted');
      const ref=make('blockquote',card,item.quote,'batch-quote');
      const link=make('a',card,`打开 PDF 第 ${item.page} 页`,'batch-source-link');
      link.href='/api/pdf/'+encodeURIComponent(item.paper_id)+'#page='+item.page;
      link.target='_blank';link.rel='noopener noreferrer';
      const fields=make('div',card,'','batch-fields');
      const dataset=input(fields,'数据集',item.dataset,120),metric=input(fields,'指标',item.metric,120),
        value=input(fields,'原文数值',item.value,80),baseline=input(fields,'对照方法',item.baseline,120),
        setting=input(fields,'实验条件',item.setting,220);
      const note=input(card,'人工核对说明（必填）','',900);
      const inspection=make('details',card,'','batch-quality-panel');
      make('summary',inspection,'试点标注：判断自动提取的原始字段是否正确，并记录人工核对时间');
      const choices={dataset:'数据集',metric:'指标名称',value:'指标数值',baseline:'对照方法',setting:'实验条件'};
      const selectors={};
      for(const [key,label] of Object.entries(choices)){
        const field=make('label',inspection,'','batch-quality-row');
        make('span',field,label);
        const select=make('select',field);selectors[key]=select;
        for(const [val,txt] of [['unchecked','尚未判定'],['correct','正确'],['incorrect','错误'],['uncertain','无法判断']]){
          const option=make('option',select,txt);option.value=val;
        }
      }
      const clock=make('div',inspection,'','batch-timer');
      const clockText=make('span',clock,'尚未计时');
      let elapsed=0,started=null,timerHandle=null;
      const total=()=>elapsed+(started===null?0:(performance.now()-started)/1000);
      const updateClock=()=>{clockText.textContent='有效审核时间 '+Math.round(total())+' 秒';};
      btn(clock,'开始／继续计时',()=>{
        if(started===null){started=performance.now();timerHandle=setInterval(updateClock,1000);updateClock();}
      },'batch-button');
      btn(clock,'暂停计时',()=>{
        if(started!==null){elapsed=total();started=null;clearInterval(timerHandle);timerHandle=null;updateClock();}
      },'batch-button');
      const actions=make('div',card,'','batch-inline-actions');
      const submit=async decision=>{
        try{
          const outcome=await request('/api/evidence-batch/review','POST',{
            candidate_id:item.id,revision:item.revision,decision,expected_sha256:item.pdf_sha256,
            metric:metric.value,value:value.value,dataset:dataset.value,baseline:baseline.value,
            setting:setting.value,review_note:note.value});
          const activeSeconds=total();
          if(timerHandle){clearInterval(timerHandle);timerHandle=null;}
          started=null;
          try{
            await request('/api/pilot/record-review','POST',{
              candidate_id:item.id,revision:outcome.revision,
              active_seconds:Math.round(activeSeconds*100)/100,
              labels:Object.fromEntries(Object.entries(selectors).map(([name,el])=>[name,el.value])),
              original_fields:{dataset:String(item.dataset||''),metric:String(item.metric||''),
                value:String(item.value||''),baseline:String(item.baseline||''),setting:String(item.setting||'')},
              corrected_fields:{dataset:dataset.value,metric:metric.value,value:value.value,
                baseline:baseline.value,setting:setting.value}});
            message(decision==='approved'?'已确认原文并记录试点审核。':'已排除候选并记录试点审核。');
          }catch(e){
            message('人工核对已经保存，但试点评估记录未保存：'+e.message,true);
          }
          await refresh();
        }catch(e){message('审核未保存：'+e.message,true);}
      };
      btn(actions,'核对后确认',()=>submit('approved'),'batch-button batch-button-primary');
      btn(actions,'排除这条',()=>submit('rejected'),'batch-button');
    }
    async function loadComparison(){
      try{
        const matrix=await request('/api/pilot/groups');compare.replaceChildren();
        const allGroups=matrix.groups||[];
        const top=make('div',compare,'','batch-status-head');make('h4',top,'跨论文实验数据对照');
        const link=make('a',top,'下载 CSV','batch-source-link');link.href='/api/evidence-batch/export';
        make('p',compare,`共 ${allGroups.length} 组相同数据集与指标，只有核对实验设置后才能确认可比。`,'batch-muted');
        for(const group of allGroups){
          const block=make('div',compare,'','batch-compare-group');
          make('strong',block,`${group.metric} · ${group.dataset||'数据集尚未标明'}`);
          make('p',block,group.note,'batch-muted');
          const status={confirmed:'已人工确认可比',rejected:'已判定不可比',stale_review:'原核对已过期',unreviewed:'尚未审查可比性'};
          make('p',block,status[group.method_review_status]||'待审查','batch-muted');
          for(const row of group.entries){
            const line=make('div',block,'','batch-compare-entry');
            const details=make('div',line,'','batch-compare-content');
            make('strong',details,row.title);
            make('p',details,`原文数值：${row.value} · PDF 第 ${row.page} 页`,'batch-muted');
            make('p',details,`对照方法：${row.baseline||'未标明'} · 实验条件：${row.setting||'未标明'}`,'batch-muted');
            const ref=make('a',line,'查看原文');ref.href='/api/pdf/'+encodeURIComponent(row.paper_id)+'#page='+row.page;
            ref.target='_blank';ref.rel='noopener noreferrer';
          }
          const review=make('details',block,'','batch-quality-panel');
          make('summary',review,'核对这一组实验条件是否可以直接比较');
          const names={metric_definition:'指标定义与计算公式一致',dataset_version:'数据集版本和测试样本一致',
            split_protocol:'训练／测试划分与评价协议一致',unit_scale:'单位、误差倍率及归一化一致',
            task_geometry:'研究任务、几何和网格条件可比',budget_and_seeds:'训练预算和随机重复已核对'};
          const checks={};
          for(const [key,label] of Object.entries(names)){
            const row=make('label',review,'','batch-checkline');
            const check=make('input',row);check.type='checkbox';checks[key]=check;
            make('span',row,label);
          }
          const reason=input(review,'可比性判断依据（至少 15 字）','',1200);
          const actionBar=make('div',review,'','batch-inline-actions');
          const verdict=async approved=>{
            try{
              await request('/api/pilot/review-group','POST',{
                group_key:group.group_key,fingerprint:group.fingerprint,
                checks:Object.fromEntries(Object.entries(checks).map(([k,v])=>[k,v.checked])),
                approved,note:reason.value});
              message(approved?'本组已确认为条件可比。':'本组已记录为不满足可比条件。');
              await loadComparison();
            }catch(e){message('可比性核对未保存：'+e.message,true);}
          };
          btn(actionBar,'全部核对通过，确认可比',()=>verdict(true),'batch-button batch-button-primary');
          btn(actionBar,'记录为不可比',()=>verdict(false),'batch-button');
        }
        if(!allGroups.length)make('p',compare,'尚没有两篇不同论文在同一数据集和指标下形成已人工确认的记录。','batch-muted');
      }catch(e){message('读取对照矩阵失败：'+e.message,true);}
    }
    refresh();
    return {refresh,preview};
  }
  return {mount};
})();
