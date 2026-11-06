# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Source registry with bounded multi-query recall and per-query diagnostics."""
from __future__ import annotations
from typing import Protocol
from datetime import datetime
from time import monotonic, sleep
from math import ceil
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
    from ..agents.contracts import AgentCancelled, AgentDeadlineExceeded
    found = []
    succeeded = 0
    errors = []
    status_rows = []
    selected = config.get('sources', ['arxiv'])
    query_budget = max(3.0, min(90.0, float(config.get('source_query_timeout_seconds',10))))
    total_budget = max(query_budget, min(600.,float(config.get('source_total_timeout_seconds',75))))
    deadline=monotonic()+total_budget
    if progress:
        progress(0, len(selected), '等待来源连接')
    for index, name in enumerate(selected, 1):
        if checkpoint: checkpoint()
        if name not in _REGISTRY:
            raise ValueError(f'未知检索来源：{name}')
        logger(f'开始检索 {_REGISTRY[name].label}（单组最多 {query_budget:g} 秒，总预算 {total_budget:g} 秒）')
        started = monotonic()
        plans = query_plan(config, name)
        batches = []
        query_statuses = []
        for qindex, plan in enumerate(plans):
            if checkpoint: checkpoint()
            remaining=deadline-monotonic()
            if remaining <= 0:
                errors.append(f'{name}：达到本次检索总时间上限')
                query_statuses.append({'keyword':plan['label'],'ok':False,'count':0,
                                       'error':'检索总时限已用尽','timed_out':True})
                break
            if qindex and name != 'arxiv':
                sleep(min(.45, max(0,deadline-monotonic())))
            local_config = dict(config)
            local_config.update(keywords=plan['keywords'], max_results=plan['max_results'])
            if progress:
                progress(index-1,len(selected),f'{name} 第 {qindex+1}/{len(plans)} 组：正在连接（最多 {min(query_budget,remaining):.0f} 秒）')
            try:
                cache_state='disabled'
                if supervised:
                    # Only native desktop installations use the per-user
                    # on-disk cache here. The public portal has its own
                    # cross-tenant rate gate and independent per-source cache.
                    cache=None
                    if getattr(sys,'frozen',False) and name in ('arxiv','openalex','crossref'):
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
                                      max(0,deadline-monotonic())))
                        def on_wait(left):
                            if progress: progress(index-1,len(selected),f'{name} 第 {qindex+1}/{len(plans)} 组：网络等待中，约 {left:.0f} 秒后跳过')
                        papers=fetch_isolated(name,local_config,now=now,
                            timeout_seconds=min(query_budget,max(.2,deadline-monotonic())),
                            checkpoint=checkpoint,heartbeat=on_wait)
                        if cache:cache.put(name,local_config,papers)
                        cache_state='miss'
                else:
                    papers = _REGISTRY[name]().fetch(local_config, logger=logger, now=now)
                if not isinstance(papers, list) or any(not isinstance(p, Paper) for p in papers):
                    raise TypeError('来源返回格式错误（要求 Paper 列表）')
                batches.append(papers)
                query_statuses.append({'keyword': plan['label'], 'ok': True, 'count': len(papers),
                                       'cache_state':cache_state})
            except (AgentCancelled, AgentDeadlineExceeded):
                # A user cancellation/deadline belongs to the whole task. Do not
                # downgrade it into a per-provider failure and continue waiting.
                raise
            except Exception as exc:
                # Avoid printing transport URLs or model/provider credentials.
                timed_out=isinstance(exc, SourceTimeout)
                message=('网络连接超时，已跳过此组检索' if timed_out else
                         ('来源连接失败或权限不可用' if supervised else str(exc)[:160]))
                errors.append(f'{name} / {plan["label"]}: {message}')
                query_statuses.append({'keyword': plan['label'], 'ok': False, 'count': 0,
                                       'error': message, 'timed_out':timed_out})
                logger(f'{name} 子检索“{plan["label"]}”失败：{message}，继续后续来源。')
                if timed_out:
                    # A timed-out endpoint is likely still inaccessible; skip its
                    # remaining keyword groups instead of repeating long waits.
                    break
        ok = bool(batches)
        partial = ok and len(batches) != len(plans)
        unique = _balanced_merge(batches, int(config.get('max_results', 30))) if ok else []
        status_rows.append({'name': name, 'ok': ok, 'partial': partial,
                            'count': len(unique), 'queries': query_statuses,
                            'seconds': round(monotonic()-started, 1),
                            'timed_out':any(x.get('timed_out') for x in query_statuses)})
        if ok:
            succeeded += 1
            found.extend(unique)
            logger(f'{_REGISTRY[name].label}：{len(batches)}/{len(plans)} 组检索成功，去重后获取 {len(unique)} 篇')
        if progress:
            detail = f'{name} 完成 {len(batches)}/{len(plans)} 组查询' if ok else f'{name} 检索失败，已跳过'
            progress(index, len(selected), detail)
    config['_source_results'] = status_rows
    if not succeeded:
        raise AllSourcesUnavailable('所有文献来源均无法连接或查询失败。请检查网络或更换检索来源；详情：'+'; '.join(errors)[:260],status_rows)
    if errors:
        logger('部分文献来源不可用，已跳过并继续；可在来源诊断中查看原因。')
    return found
