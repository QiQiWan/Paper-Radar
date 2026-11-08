# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Springer Nature official metadata API adapter; no licensed full-text access.
"""
from __future__ import annotations
from urllib import parse, request, error
from datetime import datetime, timedelta, timezone
import html, json, re
from .credentials import get_credential
from ..models import Paper
from .request_policy import request_timeout

BASE = 'https://api.springernature.com/meta/v2/json'

def parse_record(record: dict) -> Paper | None:
    doi=str(record.get('doi') or '').strip().lower()
    title=html.unescape(re.sub('<[^>]+>','',str(record.get('title') or ''))).strip()
    if not title: return None
    authors=record.get('creators') or []
    if isinstance(authors,list):
        authors=', '.join(str(x.get('creator') or '') for x in authors if isinstance(x,dict))
    else: authors=str(authors)
    date=str(record.get('publicationDate') or record.get('onlineDate') or '')
    url=''
    for link in record.get('url',[]) if isinstance(record.get('url',[]),list) else []:
        if isinstance(link,dict) and link.get('value','').startswith('https://'):
            url=link['value'];break
    url=url or ('https://doi.org/'+doi if doi else '')
    return Paper(paper_id='springer:'+ (doi or str(record.get('identifier',''))[:70]),
                 title=title, abstract=html.unescape(re.sub('<[^>]+>',' ',str(record.get('abstract') or ''))).strip(),
                 authors=authors,published=date,updated=date,category=str(record.get('contentType') or 'article'),
                 url=url,pdf_url='',source='springer',doi=doi,venue=str(record.get('publicationName') or record.get('journalTitle') or ''))

class SpringerSource:
    name='springer'
    label='Springer Nature 元数据'
    access='api_key'
    guide_url='https://dev.springernature.com/docs/api-endpoints/meta-api/'
    note='需要 Springer Nature API Key；获取的是论文题录，付费全文需按机构授权访问。'
    def fetch(self, config, logger=print, now=None):
        key=get_credential('springer')
        if not key: raise RuntimeError('Springer 来源未配置 API Key，请在设置中填写。')
        now=now or datetime.now(timezone.utc)
        from_date=(now-timedelta(days=int(config['lookback_days']))).strftime('%Y-%m-%d')
        keyword=' '.join(config.get('keywords',[])[:3]).strip()
        if not keyword: return []
        params={'q':f'keyword: {keyword}', 's':1,'p':min(int(config['max_results']),30),'api_key':key}
        # API key is in the query by official API design; never log the request URL.
        try:
            with request.urlopen(request.Request(BASE+'?'+parse.urlencode(params),headers={'Accept':'application/json'}),timeout=request_timeout(config,35)) as resp:
                raw=json.load(resp)
        except error.HTTPError as exc:
            raise RuntimeError(f'Springer API 拒绝请求（HTTP {exc.code}），请检查密钥、额度或接口权限。') from None
        except (error.URLError, TimeoutError, OSError, ValueError):
            # Never return urllib exception details: Springer passes API Key in URL.
            raise RuntimeError('Springer API 请求失败，请检查网络、接口权限与 API Key。') from None
        papers=[]
        for record in raw.get('records',[]):
            p=parse_record(record)
            if p and (not p.published or p.published[:10]>=from_date):papers.append(p)
        return papers
