# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""OpenAlex Works metadata adapter (optional, public REST API).

Only use best_oa_location.pdf_url for downloads; a journal landing page
is never silently treated as a free downloadable PDF.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from time import monotonic
from urllib import parse, request, error
import html
import json
import time
import re
from .. import __version__
from ..models import Paper
from .credentials import get_credential
from .request_policy import request_timeout, remaining_timeout, is_configuration_network_error
from .rate_limit import backoff_for_429
from .failures import SourceQueryError

BASE = 'https://api.openalex.org/works'


def fetch_openalex_json(req, config: dict, *, started: float, semantic=False) -> dict:
    """Bound OpenAlex retries by the child deadline and Retry-After header.

    A daily-budget 429 must not generate three more identical paid-endpoint
    calls. A short server-requested pause may be retried once. This also
    distinguishes rate limits from invalid credentials and response formats.
    """
    attempts = 2
    for attempt in range(attempts):
        socket_limit = remaining_timeout(config, request_timeout(config, 35 if semantic else 30), started)
        try:
            with request.urlopen(req, timeout=socket_limit) as response:
                data = json.load(response)
            if not isinstance(data, dict) or not isinstance(data.get('results'), list):
                raise RuntimeError('OpenAlex 响应格式错误')
            return data
        except error.HTTPError as exc:
            if exc.code == 429:
                delay = backoff_for_429(exc.headers, attempt=attempt,
                    remaining_seconds=remaining_timeout(config, 30, started),
                    semantic=semantic)
                if delay is None:
                    raise SourceQueryError('RATE_LIMIT') from exc
                time.sleep(delay)
                continue
            if exc.code in (401, 403, 409):
                raise SourceQueryError('AUTH_REQUIRED') from exc
            if exc.code in (400, 402, 404, 422):
                raise SourceQueryError('BAD_QUERY') from exc
            if attempt == attempts - 1 or exc.code < 500:
                raise SourceQueryError('REMOTE_SERVER_ERROR') from exc
            time.sleep(min(1.0 * (attempt + 1), max(0, remaining_timeout(config, 30, started)-3)))
        except (error.URLError, TimeoutError, OSError) as exc:
            if is_configuration_network_error(exc):
                raise RuntimeError('OpenAlex 网络解析、证书或代理失败') from exc
            if attempt == attempts - 1:
                raise RuntimeError('OpenAlex 网络请求失败') from exc
            time.sleep(min(1.0 * (attempt+1), max(0, remaining_timeout(config, 30, started)-3)))
    raise SourceQueryError('RATE_LIMIT')


def rebuild_abstract(inverted: dict | None) -> str:
    if not isinstance(inverted, dict):
        return ''
    terms: dict[int, str] = {}
    for word, indexes in inverted.items():
        if not isinstance(indexes, list):
            continue
        for pos in indexes:
            if isinstance(pos, int) and 0 <= pos < 10000:
                terms[pos] = word
    return ' '.join(terms[i] for i in sorted(terms))[:12000]


def parse_work(work: dict) -> Paper | None:
    wid = str(work.get('id') or '').rsplit('/', 1)[-1]
    title = html.unescape(re.sub('<[^>]*>', '', str(work.get('title') or work.get('display_name') or ''))).strip()
    if not wid.startswith('W') or not title:
        return None
    doi = str(work.get('doi') or '').lower().replace('https://doi.org/', '').strip()
    oa = work.get('best_oa_location') or {}
    open_access = work.get('open_access') or {}
    direct_pdf = str(oa.get('pdf_url') or '') if oa.get('is_oa') else ''
    source = (work.get('primary_location') or {}).get('source') or {}
    authors = ', '.join(a.get('author', {}).get('display_name', '') for a in work.get('authorships', [])[:30])
    return Paper(
        paper_id=f'openalex:{wid}', title=title,
        abstract=rebuild_abstract(work.get('abstract_inverted_index')),
        authors=authors, published=str(work.get('publication_date') or ''),
        updated=str(work.get('publication_date') or ''),
        category=str(work.get('type') or 'article'),
        url=str(work.get('doi') or work.get('id') or ''),
        pdf_url=direct_pdf, source='openalex', doi=doi,
        venue=str(source.get('display_name') or ''),
        is_oa=int(bool(open_access.get('is_oa'))),
        oa_license=str(oa.get('license') or ''),
        download_url=direct_pdf)


class OpenAlexSource:
    name = 'openalex'
    label = 'OpenAlex 论文索引'
    access = 'public'
    guide_url = 'https://help.openalex.org/api/'
    note = '免费索引；大规模定时使用可配置 OpenAlex API Key。'

    def fetch(self, config, logger=print, now=None):
        started = monotonic()
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None: now = now.replace(tzinfo=timezone.utc)
        since = str(config.get('_window_start') or (now - timedelta(days=int(config['lookback_days']))).isoformat())[:10]
        until = str(config.get('_window_end') or now.isoformat())[:10]
        keywords = config.get('keywords', [])[:3]
        if len(config.get('keywords', [])) > 3:
            logger('OpenAlex 当前最多按前 3 组关键词单独检索；完整关键词将用于本地相关性筛选。')
        if not keywords:
            logger('OpenAlex 需要至少一个英文关键词，本次跳过。')
            return []
        api_key = get_credential('openalex')
        limit = min(int(config['max_results']), 100)
        cap=min(500,int(config['max_results']))
        results = {}
        for idx, keyword in enumerate(keywords):
            if idx:
                time.sleep(.5)
            for page in range(1,1+(cap+limit-1)//limit):
                remaining=cap-len(results)
                if remaining<=0:break
                params = {'search': keyword, 'filter':f'from_publication_date:{since},to_publication_date:{until},type:article|preprint',
                          'per_page':min(limit,remaining), 'page':page, 'sort':'publication_date:desc'}
                if api_key:
                    params['api_key'] = api_key
                url = BASE + '?' + parse.urlencode(params)
                req = request.Request(url, headers={
                    'User-Agent':f'Paper-Radar/{__version__} (+local research tool)', 'Accept':'application/json'})
                try:
                    data = fetch_openalex_json(req, config, started=started)
                except (RuntimeError,TimeoutError):
                    if results:
                        from .supervisor import SourceBatch
                        logger('OpenAlex 部分检索页成功，后续请求未完成，保留现有论文并等待补检。')
                        return SourceBatch(list(results.values())[:config['max_results']],
                                           partial=True,warning_code='SOURCE_PARTIAL')
                    raise
                batch=(data or {}).get('results', [])
                for item in batch:
                    p = parse_work(item)
                    if p: results[p.paper_id] = p
                if len(batch)<params['per_page']:break
        return list(results.values())[:config['max_results']]
