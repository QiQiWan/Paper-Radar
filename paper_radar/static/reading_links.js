// Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
'use strict';
// Canonical, bounded deep-link state. No tokens, local PDF paths or API keys are ever serialized.
window.PRReading = (() => {
  const localViews = new Set(['dashboard','library','tasks','following','inquiry','reports','settings','zotero','downloads']);
  const publicViews = new Set(['workspace','library','tasks','following','inquiry','reports','account']);
  const rules = {
    q:120, focus:150, scope:12, filter:18, sort:18, group:18, paper:150,
    report:180, topic:48, page:5, pos:5, shown:5,
  };
  const choices = {
    scope:['topic','all'],
    filter:['all','starred','unread','reading','done','to_read','liked','inbox','actionable','skipped'],
    sort:['recent','relevance','default','oldest','title'],
    group:['keyword','category','source','year','initial','none'],
  };
  function clean(key,raw) {
    if (!Object.hasOwn(rules,key) || typeof raw !== 'string') return '';
    const value=raw.trim().slice(0,rules[key]);
    if (!value || /[\x00-\x1f\x7f]/.test(value)) return '';
    if (choices[key] && !choices[key].includes(value)) return '';
    if (key==='page') return String(Math.max(1,Math.min(9999,Number(value)||1)));
    if (key==='pos') return String(Math.max(0,Math.min(10000,Number(value)||0)));
    if (key==='shown') return String(Math.max(0,Math.min(5000,Number(value)||0)));
    if (key==='topic' && !/^[a-zA-Z0-9_-]{1,48}$/.test(value)) return '';
    if (key==='report' && (value.startsWith('.') || value.includes('/') || value.includes('\\')))return '';
    if ((key==='paper'||key==='focus') && (value==='.'||value==='..')) return '';
    return value;
  }
  function params() { return new URLSearchParams(window.location.search); }
  function get(key){return clean(key,params().get(key)||'');}
  function link(mode,view,state={}) {
    const local=mode==='local';
    const allowed=local?localViews:publicViews;
    if (!allowed.has(view))view=local?'dashboard':'workspace';
    const p=new URLSearchParams();
    if(state.topic){const v=clean('topic',String(state.topic));if(v)p.set('topic',v);}
    if(view==='library'){
      for(const key of ['q','filter','sort',...(local?['scope','group']:[]),'shown','focus','paper','page']){
        const v=clean(key,String(state[key]??''));if(v)p.set(key,v);
      }
    }else if(view==='reports'){
      for(const key of ['report','pos']){
        const v=clean(key,String(state[key]??''));if(v)p.set(key,v);
      }
    }
    const route=local?'/app/'+view:'/'+(view==='account'?'settings':view);
    return route+(p.toString()?'?'+p:'');
  }
  function write(url,{push=false}={}){
    const target=new URL(url,window.location.origin);
    if(target.origin!==window.location.origin)return;
    const relative=target.pathname+target.search;
    if(relative===window.location.pathname+window.location.search)return;
    window.history[push?'pushState':'replaceState'](null,'',relative);
  }
  function progress(el){
    if(!el)return 0;
    const maximum=Math.max(0,el.scrollHeight-el.clientHeight);
    return maximum?Math.max(0,Math.min(10000,Math.round(el.scrollTop/maximum*10000))):0;
  }
  function restoreProgress(el,raw){
    if(!el)return;
    const maximum=Math.max(0,el.scrollHeight-el.clientHeight);
    el.scrollTop=maximum * (Number(clean('pos',String(raw??'0'))||0)/10000);
  }
  async function share(){
    const link=window.location.href;
    if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(link);return link;}
    // Explicit fallback: user can copy the selected URL from this prompt.
    window.prompt('复制当前页面链接',link);return link;
  }
  const localHelp={dashboard:'quickstart',library:'triage',tasks:'usage',following:'following',inquiry:'inquiry',reports:'reading#reports',
    settings:'model',zotero:'zotero',downloads:'sources'};
  const portalHelp={workspace:'portal',library:'triage',tasks:'portal',following:'following',inquiry:'inquiry',reports:'reading#reports',account:'portal'};
  function help(mode,view){return '/guide/'+((mode==='local'?localHelp:portalHelp)[view]||'quickstart');}
  return {clean,params,get,link,write,progress,restoreProgress,share,help};
})();
