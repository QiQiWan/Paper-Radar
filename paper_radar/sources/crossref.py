# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Crossref public bibliographic metadata source.
Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from time import monotonic
from urllib import request, parse, error
import time
from .request_policy import retry_seconds, request_timeout, remaining_timeout, is_configuration_network_error
import json
import os
import re
import html
from .. import __version__
from ..models import Paper

BASE = 'https://api.crossref.org/works'

def parse_item(item: dict) -> Paper | None:
    doi = str(item.get('DOI') or '').strip().lower()
    title = ' '.join(item.get('title') or []) if isinstance(item.get('title'), list) else str(item.get('title') or '')
    title = html.unescape(re.sub('<[^>]+>', '', title)).strip()
    if not title or not doi:
        return None
    author = ', '.join(' '.join(filter(None, (person.get('given'), person.get('family'))))
                       for person in (item.get('author') or [])[:25] if isinstance(person, dict))
    date_parts = (item.get('published') or item.get('issued') or {}).get('date-parts') or [[]]
    date = '-'.join(str(v).zfill(2) if i else str(v) for i,v in enumerate(date_parts[0])) if date_parts[0] else ''
    abstract = html.unescape(re.sub('<[^>]*>', ' ', str(item.get('abstract') or ''))).strip()
    venue = item.get('container-title') or []
    venue = venue[0] if isinstance(venue,list) and venue else str(venue or '')
    url = str(item.get('URL') or ('https://doi.org/'+doi))
    indexed=(item.get('indexed') or {}).get('date-time') or date
    return Paper(paper_id='crossref:'+doi, title=title, authors=author, abstract=abstract,
                 published=date, updated=str(indexed), category=str(item.get('type') or ''),
                 url=url, pdf_url='', source='crossref', doi=doi, venue=venue)

class CrossrefSource:
    name = 'crossref'
    label = 'Crossref DOI 论文索引'
    access = 'public'
    guide_url = 'https://www.crossref.org/documentation/retrieve-metadata/rest-api/'
    note = '免费公开元数据；不提供订阅论文 PDF。'
    def fetch(self, config: dict, logger=print, now=None):
        started = monotonic()
        now = now or datetime.now(timezone.utc)
        start = (now-timedelta(days=int(config['lookback_days']))).strftime('%Y-%m-%d')
        terms = ' '.join(config.get('keywords', [])[:3]).strip()
        if not terms:
            logger('Crossref 没有有效关键词，本次跳过。')
            return []
        window_start=str(config.get('_window_start') or start)[:10]
        window_end=str(config.get('_window_end') or now.isoformat())[:10]
        # Indexed-date catches metadata changes, including updates of older
        # works. Without a genuine update cursor, keep coverage conservative.
        window_filter=(f'from-index-date:{window_start},until-index-date:{window_end}'
                       if config.get('_window_start') else
                       f'from-pub-date:{start},until-pub-date:{window_end}')
        target=min(500,int(config['max_results']))
        results=[]
        cursor='*'
        for _ in range(1+(target-1)//50):
            remaining=target-len(results)
            if remaining<=0:break
            params={'query.bibliographic':terms,'filter':window_filter,
                    'rows':min(50,remaining),'cursor':cursor,
                    'select':'DOI,title,author,abstract,published,issued,indexed,container-title,URL,type',
                    'sort':'indexed','order':'desc'}
            contact = os.environ.get('PAPER_RADAR_CONTACT_EMAIL','').strip()
            if contact and re.fullmatch(r'[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+',contact):
                params['mailto'] = contact
            req=request.Request(BASE+'?'+parse.urlencode(params),headers={'Accept':'application/json',
                'User-Agent':f'Paper-Radar/{__version__} (local research tool)'})
            raw=None
            try:
                for attempt in range(3):
                    socket_limit=remaining_timeout(config,request_timeout(config,22),started)
                    try:
                        with request.urlopen(req,timeout=socket_limit) as resp:
                            raw=json.load(resp)
                        break
                    except error.HTTPError as exc:
                        if exc.code not in (429,503) or attempt==2:
                            raise RuntimeError('Crossref 公共接口暂时拒绝请求') from exc
                        time.sleep(retry_seconds(exc.headers.get('Retry-After'),attempt=attempt))
                    except (error.URLError,TimeoutError) as exc:
                        if is_configuration_network_error(exc):
                            raise RuntimeError('Crossref 网络解析、证书或代理失败') from exc
                        if attempt==2:raise RuntimeError('Crossref 暂时无法访问') from exc
                        time.sleep(retry_seconds(None,attempt=attempt))
            except (RuntimeError,TimeoutError):
                if results:
                    from .supervisor import SourceBatch
                    logger('Crossref 部分检索页成功，后续请求未完成，保留现有论文并等待补检。')
                    return SourceBatch(results[:target],partial=True,warning_code='SOURCE_PARTIAL')
                raise
            message=(raw or {}).get('message',{})
            batch=message.get('items') or []
            results.extend(p for item in batch if (p:=parse_item(item)))
            next_cursor=message.get('next-cursor')
            if len(batch)<params['rows'] or not next_cursor or next_cursor==cursor:
                break
            cursor=next_cursor
        return results[:target]
