# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Source registry with bounded multi-query recall and per-query diagnostics."""
from __future__ import annotations
from typing import Protocol
from datetime import datetime
from time import monotonic, sleep
from math import ceil
from .failures import MESSAGES, SourceQueryError
import sys
from ..models import Paper

class SourceAdapter(Protocol):
    name: str
    label: str
    def fetch(self, config: dict, logger=print, now: datetime | None = None) -> list[Paper]: ...

_REGISTRY: dict[str, type] = {}

def register(cls):
    if not isinstance(getattr(cls, 'name', None), str) or cls.name in _REGISTRY:
        raise ValueError('无效或重复的来源名称')
    _REGISTRY[cls.name] = cls
    return cls

def available_sources() -> list[dict]:
    return [{'name': key, 'label': value.label, 'access': getattr(value,'access','public'),
             'guide_url': getattr(value,'guide_url',''), 'note': getattr(value,'note','')}
            for key, value in _REGISTRY.items()]


def query_plan(config: dict, source_name: str) -> list[dict]:
    """One arXiv OR query; independent capped queries for the other sources.

    An independent query prevents the first few high-volume terms from consuming
    the entire retrieval quota. Results are combined fairly before local ranking.
    """
    words = [str(w).strip() for w in config.get('keywords', []) if str(w).strip()]
    cap = max(1, min(int(config.get('max_queries_per_source', 8)), 20))
    if source_name in ('arxiv','openalex_semantic') or len(words) <= 1:
        return [{'label': '组合检索' if source_name == 'arxiv' else (words[0] if words else '学科分类'),
                 'keywords': words[:cap], 'max_results': int(config.get('max_results', 30))}]
    words = words[:cap]
    total = max(1, int(config.get('max_results', 30)))
    per_query = max(8, min(30, ceil(total / len(words)) * 2))
    return [{'label': word[:110], 'keywords': [word], 'max_results': per_query} for word in words]


def _balanced_merge(batches: list[list[Paper]], limit: int) -> list[Paper]:
    result = []
    seen = set()
    # Round-robin avoids letting the first keyword monopolize local candidates.
    for i in range(max((len(b) for b in batches), default=0)):
        for batch in batches:
            if i >= len(batch):
                continue
            paper = batch[i]
            identity = str(paper.doi or paper.paper_id).lower().strip()
            if identity in seen:
                continue
            result.append(paper)
            seen.add(identity)
            if len(result) >= limit:
                return result
    return result


class AllSourcesUnavailable(RuntimeError):
    """Every configured source failed; attach safe diagnostics for the UI."""
    def __init__(self, message: str, source_results: list[dict]):
        super().__init__(message)
        self.source_results = source_results


def fetch_enabled(config: dict, logger=print, now=None, progress=None, *,
                  supervised: bool = False, checkpoint=None) -> list[Paper]:
    """Continue healthy sources even when others are unreachable.

    In an actual task, `supervised` isolates each official source query in a
    terminable subprocess. Tests and custom injected adapters retain the
    synchronous protocol by default. The total budget bounds the search step.
    """
    from .supervisor import fetch_isolated, SourceTimeout
    from .retrieval_trace import current_trace
    trace = current_trace()
    from ..agents.contracts import AgentCancelled, AgentDeadlineExceeded
    found = []
    succeeded = 0
    errors = []
    status_rows = []
    selected = config.get('sources', ['arxiv'])
    # Legacy (10, 75) was shorter than the HTTP socket timeout of several
    # adapters. Upgrade the old defaults even for callers bypassing validate_config.
    old_query = float(config.get('source_query_timeout_seconds',35))
    old_total = float(config.get('source_total_timeout_seconds',210))
    if old_query == 10 and old_total == 75:
        old_query, old_total = 35., 210.
    query_budget = max(3.0, min(90.0,old_query))
    total_budget = max(query_budget, min(600.,old_total))
    deadline=monotonic()+total_budget
    if progress:
        progress(0, len(selected), '等待来源连接')
    for index, name in enumerate(selected, 1):
        if checkpoint: checkpoint()
        if name not in _REGISTRY:
            raise ValueError(f'未知检索来源：{name}')
        logger(f'开始检索 {_REGISTRY[name].label}（单组最多 {query_budget:g} 秒，总预算 {total_budget:g} 秒）')
        started = monotonic()
        # Share the remaining global budget fairly so a slow first provider
        # cannot exhaust the window before the last provider is tried.
        remaining_sources = len(selected) - index + 1
        fair_share = max(3.0, (deadline - started) / max(1, remaining_sources))
        source_deadline = min(deadline, started + fair_share)
        plans = query_plan(config, name)
        if trace: trace.record('source_start', source=name, source_index=index,
                               source_total=len(selected), query_total=len(plans),
                               timeout_ms=round(max(0,source_deadline-started)*1000))
        batches = []
        query_statuses = []
        rate_hints = {}
        for qindex, plan in enumerate(plans):
            if checkpoint: checkpoint()
            remaining=min(deadline,source_deadline)-monotonic()
            if remaining <= 3.:
                errors.append(f'{name}：本轮来源预算已用尽')
                if trace: trace.record('query_error',source=name,query_index=qindex+1,
                                      query_total=len(plans),error_code='SOURCE_BUDGET_EXHAUSTED')
                query_statuses.append({'keyword':plan['label'],'ok':False,'count':0,
                                       'error':MESSAGES['SOURCE_BUDGET_EXHAUSTED'],
                                       'error_code':'SOURCE_BUDGET_EXHAUSTED','timed_out':False})
                break
            if qindex and name != 'arxiv':
                sleep(min(.45, max(0,source_deadline-monotonic())))
            local_config = dict(config)
            local_config.update(keywords=plan['keywords'], max_results=plan['max_results'])
            window=(config.get('_following_windows') or {}).get(name)
            if window:
                local_config['_window_start']=window['start']
                local_config['_window_end']=window['end']
            if name == 'arxiv':
                local_config['_watch_arxiv_ids']=list(config.get('_watch_arxiv_ids') or [])
            if progress:
                progress(index-1,len(selected),f'{name} 第 {qindex+1}/{len(plans)} 组：正在连接（最多 {min(query_budget,remaining):.0f} 秒）')
            query_started=monotonic()
            if trace: trace.record('query_start', source=name, query_index=qindex+1,
                                   query_total=len(plans),timeout_ms=round(min(query_budget,remaining)*1000))
            try:
                cache_state='disabled'
                if supervised:
                    # Only native desktop installations use the per-user
                    # on-disk cache here. The public portal has its own
                    # cross-tenant rate gate and independent per-source cache.
                    cache=None
                    if (getattr(sys,'frozen',False) or config.get('_local_source_cache')) and name in ('arxiv','openalex','crossref'):
                        from ..source_cache import SourceCache
                        from ..core import APP_ROOT
                        cache=SourceCache(APP_ROOT/'data')
                    cached=cache.get(name,local_config) if cache else None
                    if cached is not None:
                        papers=cached
                        cache_state='hit'
                    else:
                        if qindex and getattr(sys,'frozen',False):
                            # Conservative per-provider pacing for desktop
                            # searches: arXiv slower than metadata indexes.
                            sleep(min(3.0 if name=='arxiv' else 1.0,
                                      max(0,source_deadline-monotonic())))
                        def on_wait(left):
                            if progress: progress(index-1,len(selected),f'{name} 第 {qindex+1}/{len(plans)} 组：网络等待中，约 {left:.0f} 秒后跳过')
                        def on_transport(row):
                            if row.get('event') == 'http_error' and row.get('http_status') == 429:
                                for key in ('rate_limit_remaining','rate_limit_reset_seconds',
                                            'retry_after_seconds','rate_limit_scope'):
                                    if key in row:
                                        rate_hints[key] = row[key]
                            if trace:
                                trace.record(row['event'],**{k:v for k,v in row.items()
                                   if k not in ('event','at','source')},source=name)
                        papers=fetch_isolated(name,local_config,now=now,
                            timeout_seconds=min(query_budget,max(.2,source_deadline-monotonic())),
                            checkpoint=checkpoint,heartbeat=on_wait,trace_hook=on_transport)
                        if cache and not getattr(papers,'partial',False):
                            cache.put(name,local_config,papers)
                        cache_state='miss'
                else:
                    papers = _REGISTRY[name]().fetch(local_config, logger=logger, now=now)
                if not isinstance(papers, list) or any(not isinstance(p, Paper) for p in papers):
                    raise TypeError('来源返回格式错误（要求 Paper 列表）')
                batches.append(papers)
                if trace: trace.record('query_finish',source=name,query_index=qindex+1,
                    query_total=len(plans),count=len(papers),ok=True,
                    partial=bool(getattr(papers,'partial',False)),
                    **getattr(papers,'stats',{}),
                    elapsed_ms=round((monotonic()-query_started)*1000))
                # A result limit is evidence of possible truncation. Keep the
                # checkpoint pending so subsequent searches can be widened.
                saturated=len(papers)>=int(plan['max_results'])
                query_statuses.append({'keyword': plan['label'], 'ok': True, 'count': len(papers),
                                       'cache_state':cache_state,'saturated':saturated,
                                       'partial':bool(getattr(papers,'partial',False)),
                                       'error_code':getattr(papers,'warning_code',''),
                                       'stats':getattr(papers,'stats',{})})
            except (AgentCancelled, AgentDeadlineExceeded):
                # A user cancellation/deadline belongs to the whole task. Do not
                # downgrade it into a per-provider failure and continue waiting.
                raise
            except Exception as exc:
                # Avoid printing transport URLs or model/provider credentials.
                code = ('SOURCE_TIMEOUT' if isinstance(exc,SourceTimeout) else
                        exc.code if isinstance(exc,SourceQueryError) else 'SOURCE_UNAVAILABLE')
                timed_out=code=='SOURCE_TIMEOUT'
                if trace: trace.record('query_error',source=name,query_index=qindex+1,
                    query_total=len(plans),error_code=code,
                    elapsed_ms=round((monotonic()-query_started)*1000))
                message=MESSAGES.get(code,MESSAGES['SOURCE_UNAVAILABLE']) if supervised else str(exc)[:160]
                errors.append(f'{name} / {plan["label"]}: {message}')
                query_statuses.append({'keyword': plan['label'], 'ok': False, 'count': 0,
                                       'error': message, 'error_code':code,
                                       'timed_out':timed_out})
                logger(f'{name} 子检索“{plan["label"]}”失败：{message}，继续后续来源。')
                if code in ('SOURCE_TIMEOUT','DNS_ERROR','TLS_ERROR','PROXY_ERROR',
                            'NETWORK_ERROR','RATE_LIMIT','AUTH_REQUIRED',
                            'REMOTE_SERVER_ERROR','SOURCE_UNAVAILABLE',
                            'SOURCE_CHILD_FAILED','SOURCE_CHILD_ENCODING','SOURCE_CHILD_EMPTY',
                            'SOURCE_CHILD_PROTOCOL','SOURCE_CHILD_START') and (
                            code != 'SOURCE_UNAVAILABLE' or isinstance(exc,SourceQueryError)):
                    # A known provider/network failure should stop repeated
                    # attempts to the same endpoint. Unknown query-local
                    # application errors must not skip other valid keywords.
                    break
        ok = bool(batches)
        unique = _balanced_merge(batches, int(config.get('max_results', 30))) if ok else []
        # A final merge can hit its cap even if every individual query returned
        # fewer results than its per-query limit. Treat that as incomplete too.
        merged_limit=int(config.get('max_results',30))
        # Duplicate hits across keyword queries are expected and do NOT mean
        # the final result cap dropped any distinct publications. A false
        # truncation warning would leave healthy source checkpoints pending.
        distinct_count=len({str(p.doi or p.paper_id).lower().strip()
                            for batch in batches for p in batch})
        truncated=ok and distinct_count > len(unique)
        capped=bool(ok and (truncated or any(q.get('saturated') for q in query_statuses)))
        query_failure=any(not q.get('ok') for q in query_statuses)
        source_warning=any(q.get('partial') for q in query_statuses)
        partial = ok and (len(batches) != len(plans) or truncated or
            any(q.get('saturated') or q.get('partial') for q in query_statuses))
        outcome=('failed' if not ok else 'partial_failure' if query_failure or source_warning
                 else 'limit_reached' if capped else 'empty' if not unique else 'success')
        code=next((x.get('error_code') for x in query_statuses
                   if not x.get('ok') and x.get('error_code')), '')
        if not code:
            code=next((x.get('error_code') for x in query_statuses
                       if x.get('partial') and x.get('error_code')), '')
        if not code:
            code='LIMIT_REACHED' if capped else 'SOURCE_UNAVAILABLE' if not ok else 'OK'
        status_rows.append({'name': name, 'ok': ok, 'partial': partial,
                            'status':outcome, 'limit_reached':capped,
                            'count': len(unique), 'queries': query_statuses,
                            'seconds': round(monotonic()-started, 1),
                            'timed_out':any(x.get('timed_out') for x in query_statuses),
                            'error_code':code, **rate_hints})
        if ok:
            succeeded += 1
            found.extend(unique)
            logger(f'{_REGISTRY[name].label}：{len(batches)}/{len(plans)} 组检索成功，去重后获取 {len(unique)} 篇')
        if progress:
            detail = f'{name} 完成 {len(batches)}/{len(plans)} 组查询' if ok else f'{name} 检索失败，已跳过'
            progress(index, len(selected), detail)
        if trace: trace.record('source_finish',source=name,count=len(unique),
            ok=ok, partial=partial, source_status=outcome,
            error_code=status_rows[-1]['error_code'] or 'OK',
            elapsed_ms=round((monotonic()-started)*1000))
    config['_source_results'] = status_rows
    if not succeeded:
        raise AllSourcesUnavailable('所有文献来源均无法连接或查询失败。请检查网络或更换检索来源；详情：'+'; '.join(errors)[:260],status_rows)
    if errors:
        logger('部分文献来源不可用，已跳过并继续；可在来源诊断中查看原因。')
    return found
