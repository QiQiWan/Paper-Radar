// Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
// Shared presentation rules. The execution progress is never used to infer retrieval success.
(function (root) {
  'use strict';
  const sourceErrors = Object.freeze({
    DNS_ERROR:'域名解析失败',DNS_FAILURE:'域名解析失败',PROXY_ERROR:'代理连接失败',
    TLS_ERROR:'证书验证失败',SSL_ERROR:'证书验证失败',RATE_LIMIT:'请求被限流',
    HTTP_429:'请求被限流',UNAUTHORIZED:'授权失效',AUTH_REQUIRED:'权限不足',HTTP_401:'授权失效',
    HTTP_403:'访问被拒绝',TIMEOUT:'请求超时',SOURCE_TIMEOUT:'请求超时',
    CONNECTION_ERROR:'连接失败',SOURCE_UNAVAILABLE:'无法连接',
    SOURCE_CHILD_START:'检索服务未能启动',SOURCE_CHILD_FAILED:'检索服务意外停止',
    SOURCE_CHILD_ENCODING:'检索结果读取异常',SOURCE_CHILD_EMPTY:'检索网站没有返回可读取的数据',
    SOURCE_CHILD_PROTOCOL:'检索结果格式异常',SOURCE_RESPONSE_INVALID:'网站返回内容无法读取',SOURCE_BUDGET_EXHAUSTED:'本轮检索已到等待上限'
    ,LIMIT_REACHED:'已达到数量上限'
  });
  const number=(v)=>Number.isFinite(Number(v))?Math.max(0,Number(v)):0;
  function sourceState(entry) {
    if(entry?.status==='limit_reached')return '成功 · 达到上限';
    if(entry?.status==='partial_failure')return '部分查询失败';
    if (entry?.ok) return entry.partial?'部分完成':entry.cache_state==='stale'?'缓存待更新':'查询完成';
    if (entry?.timed_out) return '请求超时';
    const key=String(entry?.error_code||entry?.code||'').toUpperCase();
    return sourceErrors[key]||(/TIMEOUT/.test(key)?'请求超时':/PROXY/.test(key)?'代理连接失败':
      /DNS/.test(key)?'域名解析失败':/TLS|SSL|CERT/.test(key)?'证书验证失败':
      /RATE|429/.test(key)?'请求被限流':'连接失败');
  }
  function sourceAdvice(entry) {
    if(entry?.status==='limit_reached')return '已获取设定数量；若需全面覆盖，可提高单次上限并补检。';
    if(entry?.ok)return entry?.partial||entry?.cache_state==='stale'?'还有数据待补检':'';
    const key=String(entry?.error_code||entry?.code||'').toUpperCase();
    if(/DNS/.test(key))return '检查网络 DNS 或更换网络';
    if(/PROXY/.test(key))return '检查系统代理配置';
    if(/TLS|SSL|CERT/.test(key))return '检查系统时间与证书';
    if(/RATE|429/.test(key))return entry?.rate_limit_scope==='daily_budget'?'今日可用额度可能已耗尽，等待每日重置或配置免费 API Key':
      entry?.retry_after_seconds?'服务商建议 '+entry.retry_after_seconds+' 秒后重试；可检查 OpenAlex 额度与密钥':'检查 OpenAlex 免费 API Key、请求频率和每日额度';
    if(/AUTH|401|403/.test(key))return '检查访问权限和密钥';
    if(key==='SOURCE_CHILD_ENCODING')return '重启软件并重试；仍失败时下载诊断包交给维护人员';
    if(key==='SOURCE_CHILD_EMPTY')return '先重试该网站；仍失败时检查软件运行环境或联系维护人员';
    if(/SOURCE_CHILD/.test(key))return '其他来源仍可正常使用；请下载诊断包交给维护人员';
    if(/SOURCE_RESPONSE_INVALID/.test(key))return '稍后重试该网站；若连续失败，请检查软件是否有更新';
    if(/BUDGET/.test(key))return '减少关键词后重试；高级设置中可以增加最长等待时间';
    if(entry?.timed_out||/TIMEOUT/.test(key))return '可稍后重试或减少检索词';
    return '请检查来源连接后重试';
  }
  function taskSummary(run, options={}) {
    const state=String(run?.state||'idle');
    const result=run?.result||{};
    const discovery=result.discovery||result;
    const mode=String(run?.mode||run?.kind||options.mode||'');
    const rows=Array.isArray(discovery.source_results)?discovery.source_results:
      Array.isArray(result.source_results)?result.source_results:[];
    const isSearch=['search','research','search_all'].includes(mode) || rows.length>0;
    const capped=rows.filter(x=>x?.status==='limit_reached').length;
    const ok=rows.filter(x=>x?.ok&&(x?.status==='limit_reached'||!x?.partial)&&x?.cache_state!=='stale').length;
    const partial=rows.filter(x=>x?.ok&&x?.status!=='limit_reached'&&(x?.partial||x?.cache_state==='stale')).length;
    const failed=rows.length-ok-partial;
    const added=number(discovery.new??result.new??0);
    const found=number(discovery.retrieved??result.retrieved??0);
    const pending=number(discovery.pending_backfill??result.pending_backfill??0);
    const counts={sources_ok:ok,sources_failed:failed,sources_partial:partial,sources_capped:capped,sources_total:rows.length,papers_found:found,papers_added:added,pending_backfill:pending};
    if (['running','queued'].includes(state)) return {kind:'running',label:state==='queued'?'排队中':'正在执行',detail:'正在处理，请查看实时进度',...counts};
    if (state==='idle'||!run) return {kind:'idle',label:'尚未运行',detail:'',...counts};
    if (state==='failed'||state==='cancelled') return {kind:'failed',label:state==='cancelled'?'已取消':isSearch?'检索失败':mode==='synthesize'?'报告生成失败':mode==='analyze'?'PDF 分析失败':'任务失败',detail:rows.length?`${ok}/${rows.length} 个来源成功 · ${failed+partial} 个来源未完成`:(run?.error||'请查看运行记录'),...counts};
    // Show the finished artifact to a nontechnical user. Search/source
    // counts alone cannot explain what a synthesis/analyze task produced.
    if (state==='success' && mode==='synthesize') {
      const papers=number(result.paper_count), verified=number(result.finding_count);
      return {kind:'success',label:'报告生成完成',
        detail:`已收录 ${number(result.library_paper_count??papers)} 篇题录 · ${papers} 篇有证据的论文 · ${verified} 条已核对证据${verified?'':' · 尚无可比较的实验依据'}`,...counts};
    }
    if (state==='success' && mode==='analyze') {
      const analysis=result.analysis||{};
      return {kind:analysis.analysis_state==='partial'?'partial':'success',
        label:analysis.analysis_state==='partial'?'PDF 部分分析完成':'PDF 分析完成',
        detail:analysis.analysis_error||`已读取 ${number(analysis.pages)} 页 · ${number(analysis.evidence_count)} 条证据记录`,...counts};
    }
    if(mode==='search_all'&&!rows.length){
      const total=number(result.topics_succeeded)+number(result.topics_failed);
      if(total){
        const success=number(result.topics_succeeded), failed=number(result.topics_failed);
        return {kind:failed?(success?'partial':'failed'):'success',
          label:failed?(success?'部分完成':'检索失败'):'检索完成',
          detail:`${success}/${total} 个主题完成 · 新增 ${added} 篇`,...counts};
      }
    }
    if (isSearch&&rows.length){
      if (!ok&&!partial) return {kind:'failed',label:'检索失败',detail:`0/${rows.length} 个来源成功 · 请检查连接`,...counts};
      if(failed||partial||pending||state==='partial')return {kind:'partial',label:'部分完成',detail:`${ok}/${rows.length} 个来源完成 · 新增 ${added} 篇 · ${failed+partial} 个来源待补检`,...counts};
      if(!found&&!added)return {kind:'empty',label:'未找到匹配论文',detail:`${ok}/${rows.length} 个来源完成 · 可调整关键词`,...counts};
      return {kind:'success',label:'检索完成',detail:`${ok}/${rows.length} 个来源完成 · 新增 ${added} 篇`,...counts};
    }
    if(isSearch)return {kind:'unknown',label:'任务已结束',detail:'未取得来源完成记录，请查看运行详情',...counts};
    return {kind:state==='partial'?'partial':'success',label:state==='partial'?'部分完成':'任务已完成',detail:'',...counts};
  }
  function retrievalAdvice(run){
    const summary=taskSummary(run);
    if(summary.kind==='partial')return {title:'部分论文网站尚未完成',detail:'已获得的论文仍可查看和阅读。',action:'查看论文或稍后重试未完成来源',kind:'partial'};
    if(summary.kind==='failed')return {title:'本次检索未能完成',detail:summary.detail||'暂时没有可用的检索结果。',action:'检查文献来源连接，减少关键词后重试',kind:'failed'};
    if(summary.kind==='empty')return {title:'没有找到匹配论文',detail:'这不代表该方向没有相关研究。',action:'尝试更常见的英文术语，或适当增加检索时间范围',kind:'empty'};
    return null;
  }
  // Operation-level HTTP advice; keep the status for authentication and retry guards.
  function requestMessage(status,detail='',mode='local') {
    const message=String(detail||'').trim();
    if(status===401)return mode==='public'?'登录已过期，请重新登录，已保存的成果仍在账号中。':'当前操作没有权限，请重新打开本地工作台。';
    if(status===403)return '当前账号没有执行此操作的权限，请检查账号或研究主题。';
    if(status===429)return /配额|额度|过去 24 小时|任务队列已满|已有正在等待/.test(message)?message+' 如已提交任务，请先查看任务记录。':'操作过于频繁，请稍后重试。已提交的任务请先到任务记录确认状态。';
    if(status===503||status===502||status===504)return '服务暂时无法响应，请稍后刷新任务状态。不要重复提交可能已经开始的任务。';
    if(status===409)return message||'已有任务正在运行，请等待完成或在任务记录中查看进度。';
    if(status===424)return message||'外部模型服务暂时不可用。请检查模型连接，普通文献检索仍然可以继续。';
    if(status===422)return message||'当前配置不完整，请根据提示检查设置。';
    if(status>=500)return '服务内部错误。请点击右上角“下载运行日志”，并将错误编号提供给维护人员。';
    return message||'操作未完成，请检查输入后重试。';
  }
  const api={sourceState,sourceAdvice,taskSummary,retrievalAdvice,requestMessage};
  root.PRUI=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
