// Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
'use strict';
/* Shared v1.33 evidence workbench for local and account-isolated portal.
   No HTML interpolation of model/PDF text; never calls an LLM. */
(() => {
  const label = {ready:'原文已定位',stale:'依据失效',needs_review:'实验待复核',
    confirmed:'已确认',draft:'待确认',finding:'研究发现',comparison:'跨论文数值比较',
    limitation:'研究局限',next_step:'后续研究'};
  function element(tag,parent,text='',css='') {
    const n=document.createElement(tag);n.textContent=String(text??'');
    if(css)n.className=css;
    parent.appendChild(n);return n;
  }
  function action(parent,text,onclick,css=''){
    const n=element('button',parent,text,css);n.type='button';n.onclick=onclick;return n;
  }
  function field(parent,text,tag,attr={}){
    const lbl=element('label',parent,text,'trace-label');const el=element(tag,lbl);
    for(const [k,v] of Object.entries(attr))el[k]=v;
    return el;
  }
  function mount({root,request,mode='public',topic='default',paperId='',page=1,
                  sourceText='',sha='',onMessage=()=>{},onChanged=()=>{}}){
    if(!root||typeof request!=='function')throw Error('证据工作台缺少初始化参数');
    root.replaceChildren();root.classList.add('trace-workbench');
    const base=mode==='public'?'/api/public/trace':'/api/trace';
    const args=()=>mode==='public'?{topic_id:topic}:{};
    const selected=new Set();let data={anchors:[],claims:[],counts:{anchors:0,confirmed_current:0,requires_attention:0}};
    let editing=null,renderNonce=0,sourcePage=Number(page)||1,sourceSha=sha,source=sourceText;
    const head=element('header',root,'','trace-header');
    const summary=element('div',head,'','trace-heading');
    element('h3',summary,paperId?'原文摘录与结论':'研究结论');
    const hint=element('p',summary,'选取原文，整理并确认研究结论。','trace-muted');
    action(head,'刷新依据',()=>refresh(), 'trace-mini');
    const guide=element('a',head,'如何核对与引用','trace-download');guide.href='/guide/trace';
    const exportLink=element('a',head,'导出研究依据','trace-download');
    exportLink.href=base+'/export'+(mode==='public'?'?topic_id='+encodeURIComponent(topic):'');
    const message=element('p',root,'','trace-message');message.hidden=true;message.setAttribute('role','status');
    function msg(text,error=false){message.textContent=text;message.hidden=false;
      message.classList.toggle('error',error);onMessage(text,error);}
    let quote,experiment,expectedSha,pageInput,preview;
    const paperForm=paperId?element('section',root,'','trace-anchor-form'):null;
    if(paperForm){
      element('h4',paperForm,'摘录原文');
      element('p',paperForm,'输入页码并粘贴原文。系统会核对文字与 PDF 是否一致。','trace-muted');
      const cols=element('div',paperForm,'','trace-inline');
      pageInput=field(cols,'页码','input',{type:'number',min:'1',max:'120',value:String(sourcePage)});
      const experimentDetails=element('details',paperForm,'','trace-experiment-link');
      element('summary',experimentDetails,'关联实验（可选）');
      experiment=field(experimentDetails,'实验编号','input',{type:'text',maxLength:80,placeholder:'从实验卡片中复制编号'});
      quote=field(paperForm,'PDF 逐字引文','textarea',{rows:3,maxLength:650,placeholder:'保留数值、标点和单位；至少 10 字符'});
      const buttons=element('div',paperForm,'','trace-actions');
      action(buttons,'填入选中文字',()=>{const txt=String(window.getSelection()?.toString()||'').trim();
        if(txt.length>0&&txt.length<=650)quote.value=txt;
        else msg('请先在页面的解析文本中选中原文（PDF iframe 内请复制粘贴）。',true);
      },'trace-mini');
      action(buttons,'保存摘录',async()=>{
        const button=buttons.lastElementChild;button.disabled=true;
        try{
          const p=Number(pageInput.value);
          if(!Number.isInteger(p)||p<1||p>120)throw Error('请检查 PDF 页码');
          const result=await request(base+'/anchor','POST',{
            ...args(),paper_id:paperId,page:p,quote:quote.value,experiment_id:experiment.value.trim(),
            expected_sha256:expectedSha.value.trim()});
          selected.add(result.id);quote.value='';experiment.value='';msg('摘录已保存，已记录关联笔记的当前版本。');
          await refresh();onChanged();
        }catch(err){msg(err.message,true)}finally{button.disabled=false}
      },'trace-primary');
      expectedSha=element('input',paperForm,'');expectedSha.type='hidden';expectedSha.value=sourceSha||'';
      const sourceDetails=element('details',paperForm,'','trace-source');
      element('summary',sourceDetails,'查看当前页解析文本（支持鼠标选中复制）');
      preview=element('pre',sourceDetails,source||'尚未载入此页解析文本。','trace-source-text');
      if(!source && mode==='local')loadSource();
    }
    const listWrap=element('section',root,'','trace-anchors');
    const listTitle=element('h4',listWrap,'选择依据');
    const filter=field(listWrap,'按论文、原文或状态筛选','input',{type:'search',placeholder:'搜索已定位的证据'});
    const list=element('div',listWrap,'','trace-anchor-list');
    const compose=element('section',root,'','trace-claims');
    element('h4',compose,'撰写结论');
    const selectedCount=element('p',compose,'未选择证据','trace-muted');
    const kind=field(compose,'结论类型','select');
    for(const [value,text] of [['finding','研究发现'],['comparison','跨论文数值比较'],
      ['limitation','研究局限'],['next_step','后续研究']]){
      const option=element('option',kind,text);option.value=value;
    }
    const body=field(compose,'结论内容','textarea',{
      rows:3,maxLength:1200,placeholder:'结合上方引文写下研究判断、局限或待复现内容（至少 12 字符）'});
    const editInfo=element('p',compose,'','trace-muted');
    const saveRow=element('div',compose,'','trace-actions');
    action(saveRow,'保存草稿',async()=>{
      const button=saveRow.firstElementChild;button.disabled=true;
      try{
        const saved=await request(base+'/claim','POST',{
          ...args(),body:body.value,kind:kind.value,anchor_ids:[...selected],
          expected_revision:editing?.revision||0,claim_id:editing?.id||''});
        editing=null;body.value='';selected.clear();editInfo.textContent='';
        msg('草稿已保存。请在结论卡片内逐项核对 PDF、实验条件，再点击“确认”。');
        await refresh();onChanged();
      }catch(err){msg(err.message,true)}finally{button.disabled=false}
    },'trace-primary');
    action(saveRow,'取消编辑',()=>{editing=null;body.value='';selected.clear();editInfo.textContent='';renderList()},'trace-mini');
    const history=element('section',root,'','trace-history');
    element('h4',history,'研究结论');
    const claimList=element('div',history,'','trace-claim-list');
    filter.addEventListener('input',()=>renderList());
    function syncSelected(){selectedCount.textContent=`已选择 ${selected.size} / 12 条依据。${kind.value==='comparison' ? '跨论文数值比较需要先完成实验条件复核。' : ''}`;
      editInfo.textContent=editing?`正在编辑已保存结论 · 修订版本 ${editing.revision}`:'';}
    function renderList(){
      if(!root.isConnected)return;
      const a=data.anchors||[];const q=filter.value.toLowerCase().trim();
      const visible=a.filter(x=>([x.title,x.paper_id,x.quote,x.status].join(' ').toLowerCase().includes(q)));
      list.replaceChildren();
      listTitle.textContent=`选择依据 · ${a.length} 条（当前显示 ${Math.min(100,visible.length)} 条）`;
      if(!a.length)element('p',list,'尚未保存原文证据。打开论文 PDF，分析全文后定位引文。','trace-empty');
      if(a.length && !visible.length)element('p',list,'当前筛选无结果。','trace-empty');
      const show=visible.slice(0,100);
      for(const anchor of show){
        const card=element('article',list,'','trace-item');
        const top=element('div',card,'','trace-item-top');
        const check=element('input',top);check.type='checkbox';check.checked=selected.has(anchor.id);
        check.setAttribute('aria-label','选择 '+(anchor.title||anchor.paper_id));
        check.addEventListener('change',()=>{
          if(check.checked){if(selected.size>=12){check.checked=false;msg('每条结论最多关联 12 条原文证据。',true);return;}selected.add(anchor.id)}
          else selected.delete(anchor.id);
          syncSelected();
        });
        const title=element('strong',top,`${anchor.title||anchor.paper_id} · PDF ${anchor.page} 页`);
        const status=element('span',top,label[anchor.status]||anchor.status,'trace-status');
        status.dataset.status=anchor.status;
        element('blockquote',card,anchor.quote);
        const technical=element('details',card,'','trace-technical');
        element('summary',technical,'技术详情');
        element('p',technical,`文件指纹 SHA-256：${String(anchor.pdf_sha256||'').slice(0,16)}… · ${anchor.experiment_id?'已关联实验':'原文摘录'}`,'trace-muted');
        if(anchor.reading_note){
          const note=anchor.reading_note;
          const noteLabel={unlinked:'未关联阅读笔记',current:`已关联阅读笔记第 ${note.linked_revision} 版`,
            changed:`笔记已更新（原关联 v${note.linked_revision}，当前 v${note.current_revision}）`,removed:'原关联阅读笔记已删除'}[note.state]||'';
          element('p',card,noteLabel+(note.snapshot_excerpt?' · 建立锚点时：'+note.snapshot_excerpt:''),'trace-note');
          if(note.state==='changed'&&note.excerpt)element('p',card,'当前笔记：'+note.excerpt,'trace-note');
        }
        if(anchor.experiment)element('p',card,`实验：${anchor.experiment.dataset} / ${anchor.experiment.metric} = ${anchor.experiment.value} · ${anchor.experiment.review_decision}`,'trace-muted');
        const lnk=element('a',card,'打开 PDF 原文页','trace-pdf-link');
        lnk.href=mode==='public'?'/api/public/pdf/view?'+new URLSearchParams({topic_id:topic,paper_id:anchor.paper_id})+'#page='+anchor.page:
          '/api/pdf/'+encodeURIComponent(anchor.paper_id)+'#page='+anchor.page;
        lnk.target='_blank';lnk.rel='noopener noreferrer';
      }
      syncSelected();
    }
    function renderClaims(){
      claimList.replaceChildren();
      const claims=data.claims||[];
      if(!claims.length)element('p',claimList,'暂无人工研究结论。先从上方勾选原文证据并保存草稿。','trace-empty');
      for(const claim of claims){
        const c=element('article',claimList,'','trace-claim');
        const top=element('div',c,'','trace-item-top');
        element('strong',top,label[claim.kind]||claim.kind);
        const state=element('span',top,label[claim.effective_status]||claim.effective_status,'trace-status');
        state.dataset.status=claim.effective_status;
        element('p',c,claim.body,'trace-claim-body');
        const refs=element('div',c,'','trace-muted');
        refs.textContent=(claim.anchors||[]).map(x=>`${x.paper_id} p.${x.page}`).join(' · ');
        if(claim.blockers?.length)element('p',c,claim.blockers.join('；'),'trace-warning');
        if(claim.review_note)element('p',c,'人工复核：'+claim.review_note,'trace-muted');
        const audit=element('details',c,'','trace-audit');
        element('summary',audit,'修订记录');
        const auditList=element('div',audit,'','trace-audit-list');
        audit.addEventListener('toggle',async()=>{
          if(!audit.open || audit.dataset.loaded)return;
          auditList.textContent='读取人工审核记录…';
          try{
            const query=new URLSearchParams({...args(),claim_id:claim.id});
            const result=await request(base+'/history?'+query.toString());
            auditList.replaceChildren();
            for(const h of result.history||[]){
              const row=element('article',auditList,'','trace-audit-entry');
              element('strong',row,`v${h.revision} · ${h.action} · ${h.event_at}`);
              element('p',row,h.body,'trace-muted');
              if(h.review_note)element('p',row,'复核说明：'+h.review_note,'trace-muted');
            }
            audit.dataset.loaded='yes';
          }catch(err){auditList.textContent='加载失败：'+err.message}
        });
        const tools=element('div',c,'','trace-actions');
        action(tools,'编辑结论',()=>{
          editing={id:claim.id,revision:claim.revision};body.value=claim.body;kind.value=claim.kind;
          selected.clear();for(const aid of claim.anchor_ids||[])selected.add(aid);
          renderList();compose.scrollIntoView({behavior:'smooth',block:'nearest'});
        },'trace-mini');
        if(claim.decision==='confirmed')action(tools,'重新审核',async()=>{
          if(!window.confirm('将已确认结论退回草稿并保留历史记录？'))return;
          try{await request(base+'/confirm','POST',{
            ...args(),claim_id:claim.id,expected_revision:claim.revision,review_note:'',decision:'draft'});
            msg('已退回草稿，保留全部审核历史。');await refresh();onChanged();
          }catch(err){msg(err.message,true)}
        },'trace-mini');
        if(claim.effective_status==='draft' || claim.effective_status==='needs_review' || claim.effective_status==='stale'){
          const line=element('div',c,'','trace-review');
          const note=field(line,'复核依据（不少于 8 字符）','textarea',{
            rows:2,maxLength:360,placeholder:'例如：核对 PDF 第 4 页表 2、单位、数据划分与指标方向'});
          action(line,'确认结论',async()=>{
            if(claim.blockers?.length){msg('当前存在失效或未复核依据：'+claim.blockers.join('；'),true);return}
            const b=line.lastElementChild;b.disabled=true;
            try{await request(base+'/confirm','POST',{
              ...args(),claim_id:claim.id,expected_revision:claim.revision,
              review_note:note.value,decision:'confirmed'});
              msg('人工确认已记录。后续 PDF 或实验复核变化将自动重算有效性。');
              await refresh();onChanged();
            }catch(err){msg(err.message,true)}finally{b.disabled=false}
          },'trace-primary');
        }
      }
    }
    async function refresh(){
      const own=++renderNonce;
      try{
        const q=mode==='public'?'?'+new URLSearchParams({topic_id:topic}):'';
        const result=await request(base+q);
        if(own!==renderNonce||!root.isConnected)return;
        data=result;
        const valid=new Set((data.anchors||[]).map(a=>a.id));
        for(const x of [...selected])if(!valid.has(x))selected.delete(x);
        hint.textContent=`${data.counts.anchors} 条原文 · ${data.counts.confirmed_current} 条当前有效人工结论 · ${data.counts.requires_attention} 条待复核 / 失效${data.truncated?' · 清单已截断，请按论文筛选':''}`;
        renderList();renderClaims();
      }catch(err){msg('读取证据链失败：'+err.message,true)}
    }
    async function loadSource(){
      if(!paperId||mode!=='local'||!preview)return;
      try{
        const result=await request(base+'/source?'+new URLSearchParams({paper_id:paperId,page:String(sourcePage)}));
        if(!root.isConnected||Number(pageInput.value)!==sourcePage)return;
        sourceSha=result.sha256||'';expectedSha.value=sourceSha;
        preview.textContent=result.chunks?.map(x=>x.text).join('\n\n')||'当前页尚无可用全文索引。';
        if(result.truncated)element('p',preview.parentNode,'部分文字超过页面预览限制，可搜索原文片段并选择更短的连续引文。','trace-warning');
      }catch(err){preview.textContent='读取原文失败：'+err.message}
    }
    async function updatePage(n,{text='',sha256=''}={}){
      sourcePage=Math.max(1,Math.min(120,Number(n)||1));
      if(pageInput)pageInput.value=String(sourcePage);
      if(expectedSha)expectedSha.value=sha256||'';
      if(preview)preview.textContent=text||'正在读取 PDF 原文索引…';
      if(mode==='local')await loadSource();
    }
    function setAnchor({quote:preQuote='',page:nextPage,experimentId=''}){
      if(!paperForm)return;
      if(nextPage&&Number(nextPage)!==Number(pageInput.value)){
        pageInput.value=String(nextPage);sourcePage=Number(nextPage);
        if(mode==='local')loadSource();
      }
      quote.value=String(preQuote||'');experiment.value=String(experimentId||'');
      paperForm.scrollIntoView({behavior:'smooth',block:'nearest'});quote.focus();
    }
    refresh();
    return {refresh,setAnchor,updatePage};
  }
  window.PRTrace={mount};
})();
