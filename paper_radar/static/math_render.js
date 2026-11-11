// Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
// Local, no-CDN MathML renderer for frequently used TeX in article metadata.
// All untrusted strings are handled as text nodes, never interpreted as HTML.
'use strict';
window.PRMath = (() => {
  const NS='http://www.w3.org/1998/Math/MathML';
  const GREEK={alpha:'α',beta:'β',gamma:'γ',delta:'δ',epsilon:'ϵ',varepsilon:'ε',theta:'θ',lambda:'λ',mu:'μ',nu:'ν',pi:'π',rho:'ρ',sigma:'σ',tau:'τ',phi:'ϕ',varphi:'φ',omega:'ω',Gamma:'Γ',Delta:'Δ',Theta:'Θ',Lambda:'Λ',Pi:'Π',Sigma:'Σ',Omega:'Ω',infty:'∞',partial:'∂',nabla:'∇',times:'×',cdot:'·',pm:'±',mp:'∓',leq:'≤',geq:'≥',neq:'≠',approx:'≈',sim:'∼',equiv:'≡',to:'→',rightarrow:'→',leftarrow:'←',leftrightarrow:'↔',Rightarrow:'⇒',in:'∈',notin:'∉',subset:'⊂',subseteq:'⊆',cup:'∪',cap:'∩',forall:'∀',exists:'∃',sum:'∑',prod:'∏',int:'∫',oint:'∮',log:'log',sin:'sin',cos:'cos',exp:'exp',min:'min',max:'max',argmin:'arg min',argmax:'arg max',mathbb:'',mathcal:'',mathbf:'',mathrm:'',text:'',operatorname:'',quad:' ',qquad:'  ',left:'',right:'',Vert:'‖',lVert:'‖',rVert:'‖',vert:'|',lvert:'|',rvert:'|',langle:'⟨',rangle:'⟩',cdots:'⋯',ldots:'…',dots:'…',propto:'∝',otimes:'⊗',oplus:'⊕',degree:'°',circ:'∘',implies:'⇒',iff:'⇔'};
  const OPERATORS=new Set(['+','-','*','=','/','(',')','[',']','{','}','|','<','>',';',',',':','·','×','≤','≥','≠','≈','→','↔','⇒','∈','∉','⊂','⊆','∪','∩']);
  function m(tag, text){const el=document.createElementNS(NS,tag);if(text!==undefined)el.textContent=String(text);return el;}
  function group(children){const row=m('mrow');for(const child of children)row.appendChild(child);return row;}
  function parse(tex){
    const src=String(tex||'').slice(0,3000);let i=0,steps=0;
    function atom(){
      if(++steps>4500)throw Error('formula too complex');
      if(i>=src.length)return null;
      const ch=src[i++];
      if(ch==='{')return sequence('}');
      if(ch==='\\'){
        if(i>=src.length)return m('mtext','\\');
        const match=src.slice(i).match(/^[a-zA-Z]+/);let cmd;
        if(match){cmd=match[0];i+=cmd.length;}
        else{cmd=src[i++];}
        if(cmd==='frac'||cmd==='dfrac'||cmd==='tfrac'){
          const out=m('mfrac');out.appendChild(readArg());out.appendChild(readArg());return out;
        }
        if(['hat','widehat','bar','overline','tilde','widetilde','dot','ddot','vec','underline'].includes(cmd)){
          const target=readArg();
          const below=cmd==='underline';const wrapper=m(below?'munder':'mover');
          wrapper.setAttribute('accent', 'true');wrapper.appendChild(target);
          wrapper.appendChild(m('mo',({hat:'^',widehat:'^',bar:'¯',overline:'¯',tilde:'~',widetilde:'~',dot:'˙',ddot:'¨',vec:'→',underline:'_'}[cmd])));
          return wrapper;
        }
        if(cmd==='sqrt'){
          if(src[i]==='['){i++;while(i<src.length&&src[i]!==']')i++;i++;}
          const out=m('msqrt');out.appendChild(readArg());return out;
        }
        if(['mathrm','mathbf','mathbb','mathcal','text','operatorname'].includes(cmd)){
          const sub=readArg();if(cmd==='text'||cmd==='operatorname'){
            const txt=m('mtext',sub.textContent);return txt;
          }
          const style=m('mstyle');if(cmd==='mathbf')style.setAttribute('mathvariant','bold');
          if(cmd==='mathbb')style.setAttribute('mathvariant','double-struck');
          if(cmd==='mathcal')style.setAttribute('mathvariant','script');
          if(cmd==='mathrm')style.setAttribute('mathvariant','normal');
          style.appendChild(sub);return style;
        }
        if(cmd==='left'||cmd==='right'||cmd==='!')return atom();
        const sym=Object.hasOwn(GREEK,cmd)?GREEK[cmd]:cmd;
        if(!sym)return atom();
        return m(OPERATORS.has(sym)?'mo':sym.length>1?'mi':'mi',sym);
      }
      if(/\s/.test(ch))return m('mspace');
      if(/[0-9.]/.test(ch)){
        let n=ch;while(i<src.length&&/[0-9.]/.test(src[i]))n+=src[i++];return m('mn',n);
      }
      if(/[A-Za-z]/.test(ch))return m('mi',ch);
      return m(OPERATORS.has(ch)?'mo':'mi',ch);
    }
    function readArg(){while(i<src.length&&/\s/.test(src[i]))i++;return atom()||m('mrow');}
    function sequence(stop){
      const out=[];
      while(i<src.length){
        if(stop&&src[i]===stop){i++;break;}
        if(!stop&&src[i]==='}')throw Error('unbalanced group');
        const node=atom();if(!node)break;
        if(src[i]==='^'||src[i]==='_'){
          let sup=null,sub=null;
          for(let j=0;j<2&&(src[i]==='^'||src[i]==='_');j++){
            const sym=src[i++];const arg=readArg();
            if(sym==='^')sup=arg;else sub=arg;
          }
          const combo=m(sup&&sub?'msubsup':sup?'msup':'msub');combo.appendChild(node);
          if(sub)combo.appendChild(sub);if(sup)combo.appendChild(sup);
          out.push(combo);
        }else out.push(node);
      }
      return group(out);
    }
    return sequence('');
  }
  function mathNode(tex,display){const math=m('math');math.setAttribute('display',display?'block':'inline');math.setAttribute('aria-label',tex);math.appendChild(parse(tex));return math;}
  const EXPR=/\\\[([\s\S]+?)\\\]|\$\$([\s\S]+?)\$\$|\\\(([\s\S]+?)\\\)|(?<!\\)\$([^$\n]{1,800})\$/g;
  function renderElement(el){
    if(!el || el.dataset.mathRendered==='1')return;
    // Replace only text nodes: keep <strong>, line breaks, and safe hyperlinks.
    const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
    const targets=[];
    while(walker.nextNode()){
      const textNode=walker.currentNode;
      if(textNode.parentElement?.closest('math,code,pre,script,style'))continue;
      if(/\\\[|\\\(|\$/.test(textNode.nodeValue||''))targets.push(textNode);
    }
    let changed=0;
    for(const textNode of targets){
      const text=textNode.nodeValue||'';EXPR.lastIndex=0;
      let match,last=0,count=0;const frag=document.createDocumentFragment();
      while((match=EXPR.exec(text)) && count<25){
        if(match.index>last)frag.appendChild(document.createTextNode(text.slice(last,match.index)));
        const tex=match[1]||match[2]||match[3]||match[4];
        try{frag.appendChild(mathNode(tex,!!(match[1]||match[2])));changed++;}
        catch(_){frag.appendChild(document.createTextNode(match[0]));}
        last=EXPR.lastIndex;count++;
      }
      if(!count)continue;
      if(last<text.length)frag.appendChild(document.createTextNode(text.slice(last)));
      textNode.parentNode?.replaceChild(frag,textNode);
    }
    if(changed)el.dataset.mathRendered='1';
  }
  // Restricted selector: original abstracts, titles and reading evidence only.
  const SELECTOR='.paper-title,.paper-summary,.paper-detail p,.paper-reason,.evidence-card p,.evidence-card blockquote,.reader-task-output p,.reader-task-output blockquote,.inquiry-meta-paper,.inquiry-claim,.paper-reading-assistant blockquote,.report-body p,#paperList .paper-title-group strong,#paperList .paper>p,#paperList .paper small,#fulltextPanel .evidence-heading strong,.paper-reading-assistant .reader-task-output p';
  let queued=false;
  function renderAll(){document.querySelectorAll(SELECTOR).forEach(renderElement);}
  function schedule(){if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;renderAll();});}
  function init(){renderAll();new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
  return {renderAll,renderElement,parse};
})();
