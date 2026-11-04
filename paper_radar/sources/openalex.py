# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""OpenAlex Works metadata adapter (optional, public REST API).

Only use best_oa_location.pdf_url for downloads; a journal landing page
is never silently treated as a free downloadable PDF.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from urllib import parse, request, error
import html
import json
import os
import time
import re
from .. import __version__
from ..models import Paper

BASE = 'https://api.openalex.org/works'


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
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None: now = now.replace(tzinfo=timezone.utc)
        since = (now - timedelta(days=int(config['lookback_days']))).date().isoformat()
        keywords = config.get('keywords', [])[:3]
        if len(config.get('keywords', [])) > 3:
            logger('OpenAlex 当前最多按前 3 组关键词单独检索；完整关键词将用于本地相关性筛选。')
        if not keywords:
            logger('OpenAlex 需要至少一个英文关键词，本次跳过。')
            return []
        api_key = os.environ.get('OPENALEX_API_KEY','').strip()
        limit = min(int(config['max_results']), 100)
        results = {}
        for idx, keyword in enumerate(keywords):
            if idx:
                time.sleep(.5)
            params = {'search': keyword, 'filter':f'from_publication_date:{since},to_publication_date:{now.astimezone(timezone.utc).date().isoformat()},type:article|preprint',
                      'per_page':limit, 'page':1, 'sort':'publication_date:desc'}
            if api_key:
                params['api_key'] = api_key
            url = BASE + '?' + parse.urlencode(params)
            req = request.Request(url, headers={
                'User-Agent':f'Paper-Radar/{__version__} (+local research tool)', 'Accept':'application/json'})
            data = None
            for attempt in range(3):
                try:
                    with request.urlopen(req, timeout=30) as response:
                        data = json.load(response)
                    break
                except (error.HTTPError, error.URLError, TimeoutError) as exc:
                    if isinstance(exc, error.HTTPError) and exc.code in (400,401,403):
                        raise RuntimeError('OpenAlex 查询参数或访问凭证被拒绝') from exc
                    if attempt == 2:
                        raise RuntimeError(f'OpenAlex 查询失败：{exc}') from exc
                    time.sleep(1.5 * (attempt + 1))
            for item in (data or {}).get('results', []):
                p = parse_work(item)
                if p: results[p.paper_id] = p
            if len(results) >= config['max_results']:
                break
        return list(results.values())[:config['max_results']]
