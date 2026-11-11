# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""OpenReview API 2 public submission search (metadata, opt-in).

Searches public forum notes only. No login or private invitation access, no
third-party URLs, and no assumption that a public manuscript is accepted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib import request, parse, error
import html
import json
import re
import time

from .. import __version__
from ..models import Paper
from .request_policy import retry_seconds, request_timeout, is_configuration_network_error
from .supervisor import SourceBatch
from ..quality import matches

BASE = 'https://api2.openreview.net/notes/search'
MAX_RESULTS = 500


def _value(note: dict, key: str):
    data = (note.get('content') or {}).get(key, '')
    if isinstance(data, dict):
        data = data.get('value', '')
    return data


def _date(value):
    if value is None or value == '':
        return ''
    try:
        # OpenReview API uses Unix millisecond timestamps.
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
    except (ValueError, OverflowError, TypeError, OSError):
        return ''


def parse_note(note: dict) -> Paper | None:
    if not isinstance(note, dict) or note.get('replyto'):
        return None
    identifier = str(note.get('id') or '').strip()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}', identifier):
        return None
    title = html.unescape(re.sub(r'<[^>]*>', '', str(_value(note, 'title') or ''))).strip()
    if not title:
        return None
    abstract = _value(note, 'abstract') or ''
    if isinstance(abstract, list):
        abstract = ' '.join(map(str, abstract))
    authors = _value(note, 'authors') or []
    authors = ', '.join(str(s) for s in authors[:60]) if isinstance(authors, list) else str(authors)
    doi = str(_value(note, 'doi') or '').removeprefix('https://doi.org/').strip().lower()
    venue = str(_value(note, 'venue') or _value(note, 'venueid') or '').strip()[:180]
    published = _date(note.get('pdate') or note.get('cdate'))
    updated = _date(note.get('mdate') or note.get('pdate') or note.get('cdate'))
    return Paper(paper_id='openreview:' + identifier, title=title[:2000],
                 abstract=html.unescape(re.sub(r'<[^>]*>', '', str(abstract)))[:12000],
                 authors=authors[:2500], published=published, updated=updated,
                 category='conference-submission',
                 url='https://openreview.net/forum?id=' + identifier,
                 pdf_url='https://openreview.net/pdf?id=' + identifier if _value(note, 'pdf') else '',
                 source='openreview', doi=doi, venue=venue,
                 is_oa=int(bool(_value(note, 'pdf'))))


class OpenReviewSource:
    name = 'openreview'
    label = 'OpenReview 公开会议论文'
    access = 'public'
    guide_url = 'https://docs.openreview.net/getting-started/using-the-api'
    note = '公开投稿及会议论文；标题与摘要关键词检索，不保证已被会议录用。'

    def fetch(self, config: dict, logger=print, now=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        words = [str(w).strip() for w in (config.get('keywords') or []) if str(w).strip()]
        if not words:
            logger('OpenReview 需要至少一个关键词。')
            return []
        # Invitations are optional official OpenReview IDs, never arbitrary URLs.
        invitation = str(config.get('openreview_invitation') or '').strip()
        if invitation and not re.fullmatch(r'[A-Za-z0-9_.\-+/]{5,220}', invitation):
            raise ValueError('OpenReview 投稿邀请 ID 格式无效')
        since = str(config.get('_window_start') or (now - timedelta(days=int(config.get('lookback_days', 7)))).date())[:10]
        until = str(config.get('_window_end') or now.date())[:10]
        target = min(MAX_RESULTS, max(1, int(config.get('max_results', 30))))
        found: dict[str, Paper] = {}
        stats = {'remote_hits':0, 'date_rejected':0, 'invalid_notes':0}

        def pages(term: str, *, fallback_phrase: str = '') -> int:
            """Fetch bounded official search pages; return raw note count."""
            offset = 0
            observed = 0
            while offset < target:
                page_size = min(100, target - offset)
                params = {'term':term[:180], 'content':'all', 'source':'forum',
                          'limit':page_size, 'offset':offset}
                if invitation:
                    params['invitation'] = invitation
                req = request.Request(BASE + '?' + parse.urlencode(params), headers={
                    'Accept':'application/json', 'User-Agent':f'Paper-Radar/{__version__} (+research metadata)'})
                payload = None
                for attempt in range(3):
                    try:
                        with request.urlopen(req, timeout=request_timeout(config,20)) as response:
                            payload = json.load(response)
                        break
                    except error.HTTPError as exc:
                        if exc.code in (400, 401, 403, 404):
                            raise RuntimeError(f'OpenReview 拒绝查询（HTTP {exc.code}），请检查关键词或投稿邀请 ID') from exc
                        if attempt == 2:
                            raise RuntimeError('OpenReview 查询失败或触发限流') from exc
                        time.sleep(retry_seconds(exc.headers.get('Retry-After'),attempt=attempt))
                    except (error.URLError, TimeoutError, OSError) as exc:
                        if is_configuration_network_error(exc):
                            raise RuntimeError('OpenReview 网络解析、证书或代理失败') from exc
                        if attempt == 2:
                            raise RuntimeError('OpenReview 网络不可用') from exc
                        time.sleep(retry_seconds(None,attempt=attempt))
                if not isinstance(payload, dict) or not isinstance(payload.get('notes'), list):
                    raise RuntimeError('OpenReview 返回数据格式不正确')
                batch = payload['notes']
                observed += len(batch)
                stats['remote_hits'] += len(batch)
                for note in batch:
                    paper = parse_note(note)
                    if not paper:
                        stats['invalid_notes'] += 1
                        continue
                    if fallback_phrase and not (matches(paper.title, fallback_phrase) or
                                                matches(paper.abstract, fallback_phrase)):
                        continue
                    if not (paper.updated and since <= paper.updated <= until or
                            paper.published and since <= paper.published <= until):
                        stats['date_rejected'] += 1
                        continue
                    found[paper.paper_id] = paper
                offset += len(batch)
                if len(batch) < page_size:
                    break
                if offset >= target:
                    logger('OpenReview 达到本次查询页上限；若近期文献较少可缩短关键词。')
            return observed

        for word in words[:3]:
            remote = pages(word)
            # OpenReview sometimes indexes only one component of a compound
            # phrase. One bounded broad recall is allowed when the exact term
            # returned no notes; every fallback hit is rechecked locally.
            pieces = re.findall(r'[A-Za-z][A-Za-z0-9-]{3,}',word)
            if remote == 0 and len(pieces) >= 2:
                anchor = max(pieces, key=len)
                if len(anchor) >= 5:
                    logger('OpenReview 完整短语未命中，尝试一次单词召回并校验完整短语。')
                    pages(anchor, fallback_phrase=word)
        return SourceBatch(list(found.values())[:target], stats=stats)
