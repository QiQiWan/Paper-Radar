# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Source registry with bounded multi-query recall and per-query diagnostics."""
from __future__ import annotations
from typing import Protocol
from datetime import datetime
from time import monotonic, sleep
from math import ceil
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
    if source_name == 'arxiv' or len(words) <= 1:
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


def fetch_enabled(config: dict, logger=print, now=None, progress=None) -> list[Paper]:
    found = []
    succeeded = 0
    errors = []
    status_rows = []
    selected = config.get('sources', ['arxiv'])
    if progress:
        progress(0, len(selected), '等待来源连接')
    for index, name in enumerate(selected, 1):
        if name not in _REGISTRY:
            raise ValueError(f'未知检索来源：{name}')
        logger(f'开始检索 {_REGISTRY[name].label}')
        started = monotonic()
        plans = query_plan(config, name)
        batches = []
        query_statuses = []
        for qindex, plan in enumerate(plans):
            if qindex and name != 'arxiv':
                # Avoid a burst of repeated requests; official provider limits
                # can be stricter and are also enforced by each source adapter.
                sleep(0.45)
            local_config = dict(config)
            local_config.update(keywords=plan['keywords'], max_results=plan['max_results'])
            try:
                papers = _REGISTRY[name]().fetch(local_config, logger=logger, now=now)
                if not isinstance(papers, list) or any(not isinstance(p, Paper) for p in papers):
                    raise TypeError('来源返回格式错误（要求 Paper 列表）')
                batches.append(papers)
                query_statuses.append({'keyword': plan['label'], 'ok': True, 'count': len(papers)})
            except Exception as exc:
                errors.append(f'{name} / {plan["label"]}: {exc}')
                query_statuses.append({'keyword': plan['label'], 'ok': False, 'count': 0,
                                       'error': str(exc)[:160]})
                logger(f'{name} 子检索“{plan["label"]}”失败：{exc}')
        ok = bool(batches)
        partial = ok and len(batches) != len(plans)
        unique = _balanced_merge(batches, int(config.get('max_results', 30))) if ok else []
        status_rows.append({'name': name, 'ok': ok, 'partial': partial,
                            'count': len(unique), 'queries': query_statuses,
                            'seconds': round(monotonic()-started, 1)})
        if ok:
            succeeded += 1
            found.extend(unique)
            logger(f'{_REGISTRY[name].label}：{len(batches)}/{len(plans)} 组检索成功，去重后获取 {len(unique)} 篇')
        if progress:
            detail = f'{name} 完成 {len(batches)}/{len(plans)} 组查询' if ok else f'{name} 检索失败'
            progress(index, len(selected), detail)
    config['_source_results'] = status_rows
    if not succeeded:
        raise RuntimeError('所有文献来源均检索失败：' + '; '.join(errors)[:400])
    if errors:
        logger('部分来源或子检索未完成；报告将保留逐来源执行状态。')
    return found
