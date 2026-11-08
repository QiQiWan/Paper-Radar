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
    SOURCE_CHILD_START:'子进程无法启动',SOURCE_CHILD_FAILED:'子进程异常退出',
    SOURCE_CHILD_ENCODING:'子进程编码异常',SOURCE_CHILD_EMPTY:'子进程无输出',
    SOURCE_CHILD_PROTOCOL:'子进程返回无效数据',SOURCE_RESPONSE_INVALID:'来源响应无法解析',SOURCE_BUDGET_EXHAUSTED:'时间预算已用尽'
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
    if(key==='SOURCE_CHILD_ENCODING')return '升级到 v1.40.3，完全退出程序后重新启动';
    if(key==='SOURCE_CHILD_EMPTY')return '检查 Windows 安全软件与 Python 启动方式';
    if(/SOURCE_CHILD/.test(key))return '检查 Python 环境、运行路径和安全软件';
    if(/SOURCE_RESPONSE_INVALID/.test(key))return '检查来源接口状态或升级软件';
    if(/BUDGET/.test(key))return '减少每个来源的检索组数或提高总时间预算';
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
    if (state==='failed'||state==='cancelled') return {kind:'failed',label:state==='cancelled'?'已取消':isSearch?'检索失败':'任务失败',detail:rows.length?`${ok}/${rows.length} 个来源成功 · ${failed+partial} 个来源未完成`:(run?.error||'请查看运行记录'),...counts};
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
  const api={sourceState,sourceAdvice,taskSummary};
  root.PRUI=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
