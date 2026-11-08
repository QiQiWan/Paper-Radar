// Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
'use strict';
/* v1.40 research inquiry: content is always rendered as text, never HTML. */
window.PRInquiry = (() => {
  const make=(tag,parent,text='',cls='')=>{
    const el=document.createElement(tag);el.textContent=String(text??'');
    if(cls)el.className=cls;parent.appendChild(el);return el;
  };
  const button=(parent,label,act,cls='')=>{
    const el=make('button',parent,label,cls);el.type='button';el.addEventListener('click',act);return el;
  };
  function mount({root,request,mode='local',getTopic}) {
    if(!root || typeof request!=='function' || typeof getTopic!=='function')throw Error('研究助手缺少配置');
    root.replaceChildren();root.classList.add('inquiry-workbench');
    make('h2',root,'研究问题');
    const query=make('textarea',root,'','inquiry-question');query.rows=3;query.maxLength=500;
    query.placeholder='例如：神经算子在不同网格和几何条件下的泛化能力，现有实验有哪些一致结果与限制？';
    query.setAttribute('aria-label','研究问题');
    const bar=make('div',root,'','inquiry-actions');
    const run=button(bar,'整理已有证据',()=>generate(),'inquiry-primary');
    const reload=button(bar,'刷新历史',()=>refresh());
    let model=null,confirm=null;
    if(mode==='local') {
      const settings=make('details',root,'','inquiry-options');
      make('summary',settings,'模型辅助排序');
      const ml=make('label',settings,'','inquiry-option-line');
      model=make('input',ml);model.type='checkbox';make('span',ml,'调整已有结论的显示顺序');
      const cl=make('label',settings,'','inquiry-option-line');
      confirm=make('input',cl);confirm.type='checkbox';
      make('span',cl,'我同意发送研究问题及已确认结论的部分文字至所配置的模型服务（可能产生费用）。');
      make('p',settings,'模型不接收原始 PDF、私人阅读笔记或未复核实验记录，也不能增加新的论文或科研结论。','inquiry-muted');
    }
    const evidenceSummary=make('p',root,'正在查看当前主题的原文依据…','inquiry-muted');
    const state=make('p',root,'','inquiry-state');state.setAttribute('role','status');
    const history=make('div',root,'','inquiry-history');
    const output=make('section',root,'','inquiry-output');
    let lastTopic='',active='',nonce=0;
    function message(text,err=false){state.textContent=text;state.classList.toggle('error',err)}
    const prefix=mode==='local'?'/api/inquiry':'/api/public/inquiry';
    const params=(extra={})=>new URLSearchParams(mode==='local'?extra:{topic_id:getTopic(),...extra});
    async function refresh(){
      const topic=getTopic();const current=++nonce;lastTopic=topic;
      try{
        const overviewPath=mode==='local'?'/api/trace':'/api/public/trace?'+params();
        try{
          const overview=await request(overviewPath);
          if(current!==nonce||topic!==getTopic())return;
          const n=Number(overview.counts?.confirmed_current||0);
          evidenceSummary.textContent=n?`当前有 ${n} 条已确认的原文结论`:
            '暂无已确认的原文结论。可以先在论文库核对 PDF，或直接整理待读清单。';
        }catch(_){if(current===nonce)evidenceSummary.textContent='原文依据概况暂时不可用';}
        const results=await request(prefix+'?'+params());
        if(current!==nonce||topic!==getTopic())return;
        history.replaceChildren();
        make('h3',history,'历史研究简报');
        for(const entry of results.reports||[]){
          const row=make('div',history,'','inquiry-history-row');
          button(row,entry.question,()=>open(entry.id),'inquiry-history-link');
          make('small',row,entry.created_at,'inquiry-muted');
        }
        if(!(results.reports||[]).length)make('p',history,'暂无研究简报。填写上方问题即可生成。','inquiry-muted');
      }catch(e){message('历史记录加载失败：'+e.message,true)}
    }
    async function open(id){
      const current=++nonce,topic=getTopic();active=id;
      try{
        const data=await request(prefix+'/view?'+params({id}));
        if(current!==nonce||topic!==getTopic())return;
        render(data);
      }catch(e){message('研究简报无法读取：'+e.message,true)}
    }
    async function generate(){
      const question=query.value.trim();
      if(question.length<8){message('请写出至少 8 个字符的具体研究问题。',true);return}
      if(model?.checked&&!confirm?.checked){message('启用模型前请先确认发送范围和费用。',true);return}
      run.disabled=true;message('正在核查已有证据并整理研究简报…');
      const topic=getTopic();
      try{
        const payload=mode==='local'?{question,use_model:!!model.checked,confirm_transfer:!!confirm.checked}:
          {topic_id:topic,question};
        const data=await request(prefix+'/run','POST',payload);
        if(topic!==getTopic())return;
        active=data.id;query.value=question;
        await refresh();await open(active);
        message('简报已保存，打开时会重新检查证据状态。');
      }catch(e){message('生成失败：'+e.message,true)}finally{run.disabled=false}
    }
    function render(record){
      const brief=record.brief;output.replaceChildren();
      const top=make('div',output,'','inquiry-result-top');
      make('h3',top,brief.question);
      const status=make('span',top,({current:'依据有效',needs_refresh:'依据已变化，需要复核',no_confirmed_evidence:'暂无有效确认结论'})[record.snapshot_status]||record.snapshot_status,'inquiry-status');
      status.dataset.status=record.snapshot_status;
      const bar=make('div',output,'','inquiry-actions');
      button(bar,'下载 Markdown',()=>{
        const blob=new Blob([record.markdown],{type:'text/markdown;charset=utf-8'});
        const url=URL.createObjectURL(blob);const a=document.createElement('a');
        a.href=url;a.download='research-inquiry.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      });
      if(record.issues?.length){
        const alert=make('div',output,'','inquiry-warning');
        make('strong',alert,'需复核后再引用');
        for(const issue of record.issues)make('p',alert,issue);
      }
      const coverage=brief.coverage;
      make('p',output,`已扫描 ${coverage.papers_scanned} 篇论文 · 匹配 ${coverage.question_matched_claims} 条确认结论 · 有 ${coverage.blocked_claims} 条结论待复核`,'inquiry-muted');
      if(coverage.source_tracking?.pending_sources)
        make('p',output,`有 ${coverage.source_tracking.pending_sources} 个文献来源待补检。请先进入「研究跟进」检查来源状态。`,'inquiry-warning');
      if(coverage.papers_truncated||coverage.evidence_truncated)
        make('p',output,'本次只处理了部分论文。可缩小主题或分批分析。','inquiry-warning');
      const findings=make('section',output,'');make('h4',findings,'已确认结论');
      if(!brief.findings.length)make('p',findings,'尚无可直接回答这一问题的已确认原文结论。','inquiry-muted');
      for(const entry of brief.findings){
        const card=make('article',findings,'','inquiry-finding');
        make('p',card,entry.body,'inquiry-claim');
        make('small',card,`人工已确认 · 结论修订 v${entry.revision}`,'inquiry-muted');
        for(const src of entry.sources){
          const ref=make('div',card,'','inquiry-source');
          make('strong',ref,src.title+' · PDF 第 '+src.page+' 页');
          make('blockquote',ref,src.quote);
          const link=make('a',ref,'核对 PDF 原文');
          link.href=mode==='local'?'/api/pdf/'+encodeURIComponent(src.paper_id)+'#page='+src.page:
            '/api/public/pdf/view?'+new URLSearchParams({topic_id:getTopic(),paper_id:src.paper_id})+'#page='+src.page;
          link.target='_blank';link.rel='noopener noreferrer';
        }
      }
      const papers=make('section',output,'');make('h4',papers,'相关论文（仅题录）');
      
      for(const item of brief.metadata_candidates||[]){
        make('p',papers,item.title+' · '+(item.published||'日期未知'),'inquiry-meta-paper');
      }
      const candidates=make('section',output,'');make('h4',candidates,'实验差异（待核实）');
      if(!brief.disagreement_candidates.length)make('p',candidates,'当前没有形成可自动列出的差异候选，仍需人工比较实验设置。','inquiry-muted');
      for(const item of brief.disagreement_candidates)make('p',candidates,item.dataset+' / '+item.metric+'：'+item.note,'inquiry-gap');
      const gaps=make('section',output,'');make('h4',gaps,'仍需核实');
      if(!brief.gaps.length)make('p',gaps,'没有触发当前规则所覆盖的缺口；不表示文献覆盖已完整。','inquiry-muted');
      for(const gap of brief.gaps)make('p',gaps,gap.title+'：'+gap.detail,'inquiry-gap');
      for(const item of brief.research_questions)make('p',gaps,'待查：'+item.question,'inquiry-suggestion');
      const foot=make('p',output,'','inquiry-muted');
      foot.textContent='依据为生成时已确认的原文记录 · 模型排序：'+
        (brief.model.status==='reordered_existing_claims'?'已启用':'未启用或未成功')+'。';
    }
    return {refresh,open};
  }
  return {mount};
})();
