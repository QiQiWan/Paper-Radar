# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Backward-compatible research topics with separate settings and report folders.

The paper database, downloaded PDFs and model credentials remain shared. Each
saved topic has its own keywords/source choices/Zotero reference and reports.
Switching topics does not run retrieval and does not delete historical files.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from .core import APP_CONFIG, TOPIC_FIELDS, read_config, save_config


def _topic(cfg: dict, topic_id: str) -> dict:
    return next((x for x in cfg['topics'] if x['id'] == topic_id), None)


def topic_list(path: Path = APP_CONFIG) -> dict:
    cfg = read_config(path)
    return {'active_topic_id': cfg['active_topic_id'],
            'topics': [{'id': x['id'], 'name': x['name'],
                        'keyword_count': len(x['keywords']), 'sources': list(x['sources'])}
                       for x in cfg['topics']]}


def manage_topic(action: str, *, topic_id: str = '', name: str = '',
                 path: Path = APP_CONFIG) -> dict:
    cfg = read_config(path)
    tid = str(topic_id or '').strip()
    label = str(name or '').strip()
    if action == 'create':
        if len(cfg['topics']) >= 20:
            raise ValueError('最多允许创建20个研究主题。')
        if not 1 <= len(label) <= 55:
            raise ValueError('主题名称需要1至55个字符。')
        if any(t['name'].casefold() == label.casefold() for t in cfg['topics']):
            raise ValueError('已有同名研究主题。')
        new_id = uuid4().hex[:12]
        snapshot = {field: deepcopy(cfg[field]) for field in TOPIC_FIELDS}
        # Avoid accidentally attaching a new research topic to the prior Zotero collection.
        snapshot.update(zotero_enabled=False, zotero_collection_key='', zotero_refresh_keywords=False)
        cfg['topics'].append({'id': new_id, 'name': label, **snapshot})
        cfg.update(snapshot)
        cfg['active_topic_id'] = new_id
    elif action == 'switch':
        target = _topic(cfg, tid)
        if target is None:
            raise ValueError('研究主题不存在，请刷新后重试。')
        cfg.update({field: deepcopy(target[field]) for field in TOPIC_FIELDS})
        cfg['active_topic_id'] = tid
    elif action == 'rename':
        target = _topic(cfg, tid)
        if target is None:
            raise ValueError('研究主题不存在。')
        if not 1 <= len(label) <= 55:
            raise ValueError('主题名称需要1至55个字符。')
        if any(t['id'] != tid and t['name'].casefold() == label.casefold() for t in cfg['topics']):
            raise ValueError('已有同名研究主题。')
        target['name'] = label
    elif action == 'delete':
        if tid == 'default':
            raise ValueError('默认研究主题不允许删除。')
        if _topic(cfg, tid) is None:
            raise ValueError('研究主题不存在。')
        cfg['topics'] = [t for t in cfg['topics'] if t['id'] != tid]
        if cfg['active_topic_id'] == tid:
            default = _topic(cfg, 'default') or cfg['topics'][0]
            cfg.update({field: deepcopy(default[field]) for field in TOPIC_FIELDS})
            cfg['active_topic_id'] = default['id']
    else:
        raise ValueError('未知研究主题操作。')
    save_config(cfg, path)
    return topic_list(path)
