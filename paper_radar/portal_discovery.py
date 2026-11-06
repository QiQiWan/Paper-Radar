# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Bounded, opt-in public metadata discovery with per-source fault isolation.

Only fixed, registered public adapters are allowed. Search configuration does
not contain account credentials or arbitrary URLs. A source failure never
prevents completed independent sources from being saved.
"""
from __future__ import annotations

import json
import time

ALLOWED_SOURCES = ('arxiv', 'openalex', 'crossref')
LABELS = {'arxiv': 'arXiv', 'openalex': 'OpenAlex', 'crossref': 'Crossref'}
MAX_RESULTS = 40


def validate_sources(value) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        raise ValueError('请选择 1—3 个支持的检索来源')
    if any(type(x) is not str or x not in ALLOWED_SOURCES for x in value):
        raise ValueError('仅支持 arXiv、OpenAlex 和 Crossref 的公开题录检索')
    if len(set(value)) != len(value):
        raise ValueError('检索来源不可重复')
    return [source for source in ALLOWED_SOURCES if source in value]


def topic_sources(db, topic_id: str) -> list[str]:
    record = db.execute('SELECT sources_json FROM topic_sources WHERE topic_id=?', (topic_id,)).fetchone()
    if not record:
        return ['arxiv']  # existing workspaces stay on their previous behavior
    return validate_sources(json.loads(record[0]))


def set_topic_sources(db, topic_id: str, sources) -> list[str]:
    valid = validate_sources(sources)
    db.execute('''INSERT INTO topic_sources(topic_id,sources_json) VALUES (?,?)
        ON CONFLICT(topic_id) DO UPDATE SET sources_json=excluded.sources_json''',
        (topic_id, json.dumps(valid)))
    return valid


def normalize_results(papers) -> list:
    """Collapse repeated DOI/IDs within one run, preferring earlier adapters."""
    out = []
    seen_ids: set[str] = set()
    seen_dois: set[str] = set()
    for paper in papers:
        pid = str(getattr(paper, 'paper_id', '') or '')[:150]
        if not pid or pid in seen_ids or not str(getattr(paper, 'title', '') or '').strip():
            continue
        doi = str(getattr(paper, 'doi', '') or '').lower().strip()
        if doi and doi in seen_dois:
            continue
        seen_ids.add(pid)
        if doi:
            seen_dois.add(doi)
        out.append(paper)
        if len(out) >= MAX_RESULTS:
            break
    return out


def discover(portal, cfg: dict, sources: list[str], should_cancel=None) -> tuple[list, list[dict]]:
    """Return successful papers and independently actionable diagnostics.

    If every source fails, surface failure to the job queue rather than
    marking an empty search as successfully completed.
    """
    from .public_portal import PortalError
    from .portal_jobs import JobCancelled
    results = []
    diagnostics = []
    successful = 0
    def checkpoint():
        if should_cancel and should_cancel():
            raise JobCancelled('文献检索已取消')
    for name in validate_sources(sources):
        checkpoint()
        started = time.monotonic()
        try:
            if name == 'arxiv':
                from .portal_resource_locks import exclusive_file_lock
                try:
                    with portal._arxiv_lock, exclusive_file_lock(
                            portal.root/'.arxiv_global.lock',cancelled=should_cancel,timeout=35):
                        papers, _ = portal._fetch_public_arxiv(cfg, checkpoint)
                except InterruptedError as exc:
                    raise JobCancelled('文献检索已取消') from exc
            else:
                papers = portal._fetch_public_source(name, cfg, should_cancel=should_cancel)
            if not isinstance(papers, list):
                raise ValueError('来源返回结构不正确')
            successful += 1
            results.extend(papers)
            diagnostics.append({'name': name, 'ok': True, 'partial':getattr(papers,'cache_state','')=='stale',
                'count': len(papers), 'timed_out': False,
                'seconds': round(time.monotonic()-started, 2),
                'mode': 'cache' if getattr(papers,'cache_state','')=='hit' else 'isolated_process',
                'cache_state':getattr(papers,'cache_state','miss'),
                'cache_age_seconds':getattr(papers,'cache_age_seconds',0),
                'degraded':getattr(papers,'cache_state','')=='stale'})
        except JobCancelled:
            raise
        except (RuntimeError, OSError, ValueError, TimeoutError) as exc:
            # The underlying adapter may carry provider credentials in a URL;
            # never forward arbitrary exception strings to browsers or logs.
            timed_out = '超时' in str(exc) or isinstance(exc, TimeoutError)
            diagnostics.append({'name': name, 'ok': False, 'partial': False,
                'count': 0, 'timed_out': timed_out,
                'seconds': round(time.monotonic()-started, 2),
                'error_code': 'SOURCE_TIMEOUT' if timed_out else 'SOURCE_UNAVAILABLE',
                'message': '来源连接超时' if timed_out else '来源暂时不可用'})
    if not successful:
        names = '、'.join(LABELS[x['name']] for x in diagnostics)
        issue = '来源连接超时' if diagnostics and all(x['timed_out'] for x in diagnostics) else '来源暂时不可用'
        raise PortalError(f'所有检索来源均失败（{names}）：{issue}；请检查网络或调整来源后重试')
    return normalize_results(results), diagnostics
