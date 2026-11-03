# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Source registry: add adapters here, without changing the report or UI."""
from __future__ import annotations
from typing import Protocol
from datetime import datetime
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
        logger(f'开始检索 { _REGISTRY[name].label }')
        try:
            papers = _REGISTRY[name]().fetch(config, logger=logger, now=now)
        except Exception as exc:
            logger(f'{name} 获取失败：{exc}')
            errors.append(f'{name}: {exc}')
            status_rows.append({'name': name, 'ok': False, 'count': 0})
            if progress: progress(index, len(selected), f'{name} 检索失败')
            continue
        status_rows.append({'name': name, 'ok': True, 'count': len(papers)})
        succeeded += 1
        logger(f'{_REGISTRY[name].label}：获取 {len(papers)} 篇')
        found.extend(papers)
        if progress: progress(index, len(selected), f'{name} 获取 {len(papers)} 篇')
    config['_source_results'] = status_rows
    if not succeeded:
        raise RuntimeError("所有文献来源均检索失败：" + "; ".join(errors)[:400])
    if errors:
        logger('部分来源未能完成检索；报告将标记本次检索来源的状态。')
    return found
