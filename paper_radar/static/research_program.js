// Paper-Radar v1.44/v1.45: research synthesis + continuity workbench.
// Content is always created as text nodes; never render model-generated HTML.
'use strict';
window.PRResearchProgram=(()=>{
  const element=(tag,parent,text='',className='')=>{
    const el=document.createElement(tag);el.textContent=String(text??'');
    if(className)el.className=className;
    if(parent)parent.appendChild(el);return el;
  };
  const btn=(parent,label,fn,variant='')=>{
    const el=element('button',parent,label,'rp-btn '+variant);
    el.type='button';el.addEventListener('click',fn);return el;
  };
  const field=(parent,label,type='text')=>{
    const wrap=element('label',parent,'','rp-field');element('span',wrap,label);
    const el=element(type==='textarea'?'textarea':'input',wrap);
    if(type==='textarea')el.rows=3;else el.type=type;
    return el;
  };
  const asFile=(data,name)=>{
    const url=URL.createObjectURL(new Blob([data],{type:'text/markdown;charset=utf-8'}));
    const link=document.createElement('a');link.href=url;link.download=name;link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  function mount({root,request,mode='local',getTopic,getSharedQuestion=null,onAnalysisComplete=null,getModelState=null,onModelSetup=null}){
    if(!root||typeof request!=='function'||typeof getTopic!=='function')throw Error('科研工作台参数无效');
    root.replaceChildren();root.classList.add('research-program');
    const title=element('div',root,'','rp-heading');
    element('h2',title,getSharedQuestion?'研究分析工具':'科研综合与持续跟进');
    element('p',title,'使用同一个研究问题，比较已确认的论文证据；也可以管理研究笔记和周期简报。','rp-muted');
    if(mode==='local'){
      const link=element('a',title,'打开 PDF 双盲标注工作台（需先启动本地审核服务）','rp-review-link');
      link.href='http://127.0.0.1:8778/';link.target='_blank';link.rel='noopener noreferrer';
    }

    const tabs=element('div',root,'','rp-tabs');
    const bodies=[];
    const changeTab=(id)=>{
      bodies.forEach((p,i)=>p.hidden=(i!==id));
      [...tabs.children].forEach((b,i)=>{b.setAttribute('aria-selected',String(i===id));b.classList.toggle('selected',i===id)});
    };
    ['综合分析','研究记忆','周期简报'].forEach((s,i)=>btn(tabs,s,()=>changeTab(i),'rp-tab'));
    const createPanel=()=>bodies.push(element('section',root,'','rp-pane'));
    createPanel();createPanel();createPanel();
    const state=element('p',root,'','rp-status');state.setAttribute('role','status');
    const notice=(msg,error=false)=>{state.textContent=msg;state.classList.toggle('error',error)};
    const base=mode==='local'?'/api':'/api/public';
    const uri=(route,extra={})=>{
      const p=new URLSearchParams(mode==='public'?{topic_id:getTopic(),...extra}:extra);
      return base+route+(String(p)?'?'+p:'');
    };
    const post=(route,body)=>request(base+route,'POST',mode==='public'?{topic_id:getTopic(),...body}:body);
    const permit=(parent)=>{
      if(mode!=='local')return ()=>({use_model:false,confirm_transfer:false});
      const wrap=element('details',parent,'','rp-advanced');
      element('summary',wrap,'AI 辅助（默认开启，发送前需确认）');
      const model=field(wrap,'启用 AI 解释','checkbox');model.checked=typeof getModelState==='function'?!!getModelState().enabled:true;
      const confirmation=field(wrap,'本次同意发送匹配的已确认结论和原文短引文至模型服务','checkbox');
      element('p',wrap,'没有模型密钥或未授权时继续本地分析；AI 生成的解释仍需核对。','rp-muted');
      if(typeof onModelSetup==='function')btn(wrap,'连接模型',onModelSetup);
      return ()=>{const ready=typeof getModelState==='function'?!!getModelState().ready:false;
        if(model.checked&&!ready)notice('模型尚未连接，本次先用已有资料进行分析。');
        return {use_model:!!model.checked&&ready,confirm_transfer:!!model.checked&&ready&&!!confirmation.checked};};
    };

    const syn=bodies[0];
    const question=getSharedQuestion?null:field(syn,'具体研究问题','textarea');
    if(question){question.maxLength=500;question.placeholder='例如：不同神经算子在跨网格与跨几何泛化方面有哪些可核实的实验结论？';}
    else element('p',syn,'将使用本页上方填写的研究问题。','rp-muted');
    const allowSyn=permit(syn);
    const synControls=element('div',syn,'','rp-controls');
    const synResult=element('div',syn,'','rp-results');
    const synHistory=element('div',syn,'','rp-history');
    const synRun=btn(synControls,'生成科研综合报告',async()=>{
      const value=String(getSharedQuestion?getSharedQuestion():(question?.value||'')).trim();if(value.length<8){notice('研究问题至少需要 8 个字符。',true);return}
      const authorization=allowSyn();if(authorization.use_model&&!authorization.confirm_transfer){notice('请先确认本次模型数据传输。',true);return}
      synRun.disabled=true;notice('正在核对原文和科研结论…');
      try{
        const result=await post('/research-synthesis/run',mode==='local'?{question:value,...authorization}:{question:value});
        renderSynthesis(result);await refreshSynthesis();notice('科研综合报告已保存。');
        if(typeof onAnalysisComplete==='function')onAnalysisComplete(result);
      }catch(e){notice('综合分析失败：'+e.message,true)}finally{synRun.disabled=false}
    },'primary');
    btn(synControls,'刷新历史',()=>refreshSynthesis());
    const reportActions=(parent,markdown,filename)=>btn(parent,'导出 Markdown',()=>asFile(markdown,filename));
    function renderSynthesis(value){
      synResult.replaceChildren();const r=value.report,cover=r.coverage;
      const top=element('div',synResult,'','rp-result-header');
      element('h3',top,r.question);element('span',top,value.snapshot_status==='current'?'依据有效':'依据变化，需复核','rp-pill '+(value.snapshot_status!=='current'?'warn':''));
      const actions=element('div',synResult,'','rp-controls');reportActions(actions,value.markdown,'research-synthesis.md');
      element('p',synResult,`已确认结论 ${cover.matched_claims} 条 · 独立论文 ${cover.independent_papers} 篇 · 待核查 ${cover.blocked_claims} 条`,'rp-muted');
      for(const w of r.warnings||[])element('p',synResult,w,'rp-note');
      element('h4',synResult,'有原文依据的研究结论');
      for(const f of r.findings){
        const card=element('article',synResult,'','rp-evidence-card');
        element('strong',card,f.statement);element('small',card,'人工确认 · 修订 '+f.revision,'rp-muted');
        for(const s of f.evidence){
          const ref=element('div',card,'','rp-source');
          element('span',ref,`${s.title} · PDF 第 ${s.page} 页`,'rp-muted');
          element('blockquote',ref,s.quote);
          const link=element('a',ref,'查看原文');
          link.href=mode==='local'?'/api/pdf/'+encodeURIComponent(s.paper_id)+'#page='+s.page:
            '/api/public/pdf/view?'+new URLSearchParams({topic_id:getTopic(),paper_id:s.paper_id})+'#page='+s.page;
          link.target='_blank';link.rel='noopener noreferrer';
          const action=element('span',ref,'','rp-small-actions');
          btn(action,'相关',()=>sendFeedback(s.paper_id,'relevant'));
          btn(action,'不相关',()=>sendFeedback(s.paper_id,'irrelevant'));
        }
      }
      if(!r.findings.length)element('p',synResult,'当前尚无与问题匹配的已确认原文结论。可以先在论文库核对 PDF 并确认结论。','rp-muted');
      element('h4',synResult,'可进一步核查的 PDF 原文片段');
      for(const c of r.unreviewed_excerpts||[]){
        const candidate=element('article',synResult,'','rp-evidence-card');
        element('small',candidate,`${c.title} · 第 ${c.page} 页 · 待人工核验`,'rp-muted');
        element('blockquote',candidate,c.quote);
      }
      if(!(r.unreviewed_excerpts||[]).length)element('p',synResult,'暂无额外全文片段候选。','rp-muted');
      element('h4',synResult,'方法类别（候选）');
      const methods=element('div',synResult,'','rp-chips');
      for(const m of r.method_profiles)element('span',methods,`${m.method} · ${m.paper_count} 篇`,'rp-chip');
      if(!r.method_profiles.length)element('p',synResult,'当前证据不足以归纳方法类别。','rp-muted');
      element('h4',synResult,'方法对照证据');
      for(const item of r.method_comparison||[])
        element('p',synResult,`${item.method} · ${item.title}：${item.statement}（PDF 第 ${item.page} 页）`,'rp-note');
      element('h4',synResult,'跨论文实验差异（待复核）');
      for(const d of r.conflict_candidates){
        const card=element('div',synResult,'','rp-note');
        element('strong',card,d.type==='language_tension'?
          `结论措辞可能不同：${(d.claim_ids||[]).join(' / ')}`:`${d.dataset} / ${d.metric}`);
        element('p',card,d.warning);
      }
      if(!r.conflict_candidates.length)element('p',synResult,'尚未形成同条件数值差异候选；仍需人工检查论文之间是否存在冲突。','rp-muted');
      element('h4',synResult,'模型综合意见');
      for(const s of r.model.sections)element('p',synResult,`${s.title}：${s.text}（待人工复核）`,'rp-note');
      if(!r.model.sections.length)element('p',synResult,'本次未生成模型综合意见。','rp-muted');
      element('h4',synResult,'下一步待核实的问题');
      for(const q of [...r.research_gaps,...r.model.questions])element('p',synResult,q.question||q.text,'rp-note');
    }
    async function refreshSynthesis(){
      try{
        const data=await request(uri('/research-synthesis'));
        synHistory.replaceChildren();element('h4',synHistory,'历史综合报告');
        for(const r of (data.reports||[]).slice(0,12)){
          const row=element('div',synHistory,'','rp-history-row');
          btn(row,r.question,async()=>{
            try{renderSynthesis(await request(uri('/research-synthesis/view',{id:r.id})))}
            catch(e){notice(e.message,true)}
          });element('small',row,r.created_at,'rp-muted');
        }
      }catch(e){notice('读取综合报告历史失败：'+e.message,true)}
    }

    const memory=bodies[1];
    element('p',memory,'记忆分为个人笔记、研究问题、工作假设和已确认结论。已确认结论必须关联有效的人工核验记录。','rp-muted');
    const kindWrap=element('label',memory,'','rp-field');element('span',kindWrap,'记录类型');
    const kind=element('select',kindWrap);[['note','个人笔记'],['question','研究问题'],['hypothesis','工作假设'],['confirmed_claim','已确认结论']].forEach(([key,value])=>{const item=element('option',kind,value);item.value=key});
    const memoryText=field(memory,'记录内容','textarea');memoryText.maxLength=1200;
    const claimRefs=field(memory,'已确认结论关联（仅保存为确认型记忆时填写）');
    claimRefs.placeholder='可粘贴已确认结论的编号；普通研究笔记无需填写';
    claimRefs.closest('label').hidden=true;
    kind.addEventListener('change',()=>{claimRefs.closest('label').hidden=kind.value!=='confirmed_claim'});
    const memoryButtons=element('div',memory,'','rp-controls');
    const memList=element('div',memory,'','rp-results');
    let editing=null;
    btn(memoryButtons,'保存研究记忆',async()=>{
      try{
        const ids=claimRefs.value.split(',').map(s=>s.trim()).filter(Boolean);
        const result=await post('/research-memory/save',{kind:kind.value,body:memoryText.value,
          claim_ids:ids,item_id:editing?.id||'',expected_revision:editing?.revision||0});
        editing=null;memoryText.value='';claimRefs.value='';notice('研究记忆已保存，版本 '+result.revision);
        await refreshMemory();
      }catch(e){notice(e.message,true)}
    },'primary');
    btn(memoryButtons,'取消编辑',()=>{editing=null;memoryText.value='';claimRefs.value=''});
    async function refreshMemory(){
      try{
        const data=await request(uri('/research-memory'));memList.replaceChildren();
        element('h4',memList,`当前研究记忆 · ${data.items.length} 条`);
        for(const item of data.items){
          if(item.status==='retired')continue;
          const card=element('div',memList,'','rp-memory-item');
          element('span',card,item.kind==='confirmed_claim'?'有确认结论依据':item.kind+' · 用户记录','rp-pill '+(item.evidence_status==='needs_review'?'warn':''));
          element('p',card,item.body);element('small',card,`${item.evidence_status} · v${item.revision}`,'rp-muted');
          const bar=element('div',card,'','rp-controls');
          btn(bar,'编辑',()=>{editing=item;kind.value=item.kind;memoryText.value=item.body;
             claimRefs.value=(item.claim_ids||[]).join(',');memoryText.focus()});
          btn(bar,'归档',async()=>{
            try{await post('/research-memory/retire',{item_id:item.id,expected_revision:item.revision});await refreshMemory()}
            catch(e){notice(e.message,true)}
          });
        }
      }catch(e){notice('读取研究记忆失败：'+e.message,true)}
    }
    async function sendFeedback(paperId,decision){
      try{await post('/research-memory/feedback',{paper_id:paperId,decision});notice('已记录相关性反馈，只用于调整后续候选优先级。')}
      catch(e){notice('反馈失败：'+e.message,true)}
    }

    const brief=bodies[2];
    const allowBrief=permit(brief);
    const row=element('div',brief,'','rp-controls');
    const cadence=element('select',row);[['daily','每日'],['weekly','每周'],['monthly','每月']].forEach(([key,label])=>{
      const op=element('option',cadence,label);op.value=key;
    });
    const enabled=element('label',row,'','rp-check');
    const check=element('input',enabled);check.type='checkbox';element('span',enabled,'检索完成后自动生成上一完整周期的简报');
    const briefActions=element('div',brief,'','rp-controls');
    const briefResult=element('div',brief,'','rp-results');
    const briefHistory=element('div',brief,'','rp-history');
    btn(briefActions,'保存简报设置',async()=>{
      try{await post('/research-briefs/preferences',{cadence:cadence.value,enabled:check.checked});notice('周期简报设置已保存。')}
      catch(e){notice(e.message,true)}
    });
    const briefRun=btn(briefActions,'生成上一完整周期简报',async()=>{
      briefRun.disabled=true;notice('正在比较研究事件与已确认结论…');
      try{const authorization=allowBrief();
        if(authorization.use_model&&!authorization.confirm_transfer){
          notice('请先确认本次模型数据传输。',true);return;
        }
        const data=await post('/research-briefs/run',{cadence:cadence.value,
        ...(mode==='local'?authorization:{})});
        if(data.state==='processing'||data.state==='busy'){
          briefResult.replaceChildren();
          element('p',briefResult,`科研简报任务已保存检查点（${data.state==='processing'?'仍有事件待处理':'另一个进程正在处理'}）。再次点击本按钮将接续执行，不会重复计算已消费的事件。`,'rp-note');
          await refreshBriefs();notice('任务尚未完成，检查点已保留。');
          return;
        }
        renderBrief(data);await refreshBriefs();notice('简报已保存；历史快照不会被覆盖，新增内容单独补录。')
      }catch(e){notice('生成简报失败：'+e.message,true)}finally{briefRun.disabled=false}
    },'primary');
    function renderBrief(value){
      briefResult.replaceChildren();const r=value.report;
      element('h3',briefResult,`${r.cadence} · ${r.period_start.slice(0,10)} — ${r.period_end.slice(0,10)}`);
      element('p',briefResult,`${r.event_count} 条事件 · 新确认 ${r.claim_changes.new.length} · 修订 ${r.claim_changes.revised.length} · 失效 ${r.claim_changes.no_longer_current.length}`,'rp-muted');
      if(value.snapshot_status!=='current')element('p',briefResult,'证据或结论已变化，请重新核查历史简报。','rp-note');
      const changes=value.revision_diff||{};
      const appended=value.amendment_summary||{};
      if((appended.events||0)>0)element('p',briefResult,`本期已追加 ${appended.events} 条晚到事件，原报告保持不变。`,'rp-note');
      if((appended.revisions||0)>0)element('p',briefResult,`已生成 ${appended.revisions} 条证据修订记录，待查看变更。`,'rp-note');
      if((changes.new||[]).length||(changes.revised||[]).length||(changes.no_longer_current||[]).length){
        element('h4',briefResult,'已确认结论与历史版本差异');
        for(const [label,key] of [['新增','new'],['修订','revised'],['失效','no_longer_current']]){
          const ids=changes[key]||[];
          if(ids.length)element('p',briefResult,`${label} ${ids.length} 项：${ids.slice(0,8).join('、')}${ids.length>8?' 等':''}`,'rp-event');
        }
      }
      const actions=element('div',briefResult,'','rp-controls');reportActions(actions,value.markdown,'research-period-brief.md');
      element('h4',briefResult,'新增与修改论文');
      for(const e of r.event_summaries)element('p',briefResult,`${e.title} · ${e.kind} · ${e.source}`,'rp-event');
      if(!r.event_summaries.length)element('p',briefResult,'本期未记录新论文事件。','rp-muted');
      element('h4',briefResult,'待验证研究问题');
      for(const q of r.research_questions)element('p',briefResult,q.text,'rp-note');
      if(r.pending_sources.length)element('p',briefResult,`仍有 ${r.pending_sources.length} 个来源待补检。`,'rp-note');
    }
    async function refreshBriefs(){
      try{
        const data=await request(uri('/research-briefs'));
        const pref=data.preferences||{};check.checked=!!pref.enabled;cadence.value=pref.cadence||'weekly';
        briefHistory.replaceChildren();element('h4',briefHistory,'历史周期简报');
        for(const item of data.briefs||[]){
          const entry=element('div',briefHistory,'','rp-history-row');
          btn(entry,`${item.cadence} · ${item.period_start.slice(0,10)} — ${item.period_end.slice(0,10)}`,async()=>{
            try{renderBrief(await request(uri('/research-briefs/view',{id:item.id})))}
            catch(e){notice(e.message,true)}
          });
        }
      }catch(e){notice('读取周期简报失败：'+e.message,true)}
    }
    let activeTopic='';
    async function refresh(){
      const topic=getTopic();if(topic!==activeTopic){activeTopic=topic;editing=null;
        synResult.replaceChildren();briefResult.replaceChildren();memList.replaceChildren()}
      await Promise.all([refreshSynthesis(),refreshMemory(),refreshBriefs()]);
    }
    changeTab(0);
    return {refresh,show:changeTab};
  }
  return {mount};
})();
