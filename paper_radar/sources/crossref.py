# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Crossref public bibliographic metadata source.
Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from urllib import request, parse
import json
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
    return Paper(paper_id='crossref:'+doi, title=title, authors=author, abstract=abstract,
                 published=date, updated=date, category=str(item.get('type') or ''),
                 url=url, pdf_url='', source='crossref', doi=doi, venue=venue)

class CrossrefSource:
    name = 'crossref'
    label = 'Crossref DOI 论文索引'
    access = 'public'
    guide_url = 'https://www.crossref.org/documentation/retrieve-metadata/rest-api/'
    note = '免费公开元数据；不提供订阅论文 PDF。'
    def fetch(self, config: dict, logger=print, now=None):
        now = now or datetime.now(timezone.utc)
        start = (now-timedelta(days=int(config['lookback_days']))).strftime('%Y-%m-%d')
        terms = ' '.join(config.get('keywords', [])[:3]).strip()
        if not terms:
            logger('Crossref 没有有效关键词，本次跳过。')
            return []
        params = {'query.bibliographic': terms, 'filter':f'from-pub-date:{start},until-pub-date:{now.strftime("%Y-%m-%d")}',
                  'rows':min(50,int(config['max_results'])), 'select':'DOI,title,author,abstract,published,issued,container-title,URL,type',
                  'sort':'published','order':'desc'}
        req=request.Request(BASE+'?'+parse.urlencode(params),headers={'Accept':'application/json',
            'User-Agent':f'Paper-Radar/{__version__} (local literature research; https://github.com/QiQiWan/Paper-Radar)'})
        with request.urlopen(req,timeout=35) as resp:
            raw=json.load(resp)
        return [p for item in raw.get('message',{}).get('items',[]) if (p:=parse_item(item))]
