# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Elsevier Scopus metadata API adapter (requires entitled API key).
"""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from urllib import parse,request,error
import json, re, html
from ..models import Paper
from .request_policy import request_timeout
from .credentials import get_credential
BASE='https://api.elsevier.com/content/search/scopus'

def parse_entry(item:dict)->Paper|None:
    title=html.unescape(str(item.get('dc:title') or '')).strip()
    eid=str(item.get('eid') or item.get('dc:identifier') or '')
    if not title or not eid:return None
    doi=str(item.get('prism:doi') or '').strip().lower()
    date=str(item.get('prism:coverDate') or '')
    links=item.get('link') or []
    url=''
    for link in links if isinstance(links,list) else []:
        if isinstance(link,dict) and link.get('@ref') in ('scopus','scopus-citedby'):
            url=str(link.get('@href') or '');break
    url=url or ('https://doi.org/'+doi if doi else '')
    return Paper(paper_id='elsevier:'+eid,title=title,abstract=str(item.get('dc:description') or ''),
        authors=str(item.get('dc:creator') or ''),published=date,updated=date,
        category=str(item.get('subtypeDescription') or 'article'),url=url,pdf_url='',
        source='elsevier',doi=doi,venue=str(item.get('prism:publicationName') or ''))

class ElsevierSource:
    name='elsevier'
    label='Elsevier · Scopus'
    access='institutional'
    guide_url='https://dev.elsevier.com/tecdoc_api_authentication.html'
    note='必须配置获授权的 API Key；完整数据可能需要校园网/VPN 或机构令牌。PDF 另按订阅授权处理。'
    def fetch(self,config,logger=print,now=None):
        key=get_credential('elsevier')
        if not key:raise RuntimeError('Elsevier 来源未配置 API Key，请在设置中填写。')
        now=now or datetime.now(timezone.utc)
        cutoff=(now-timedelta(days=int(config['lookback_days']))).strftime('%Y-%m-%d')
        terms=config.get('keywords',[])[:3]
        if not terms:return []
        query=' OR '.join('TITLE-ABS-KEY("'+re.sub(r'[^\w\s\-]',' ',str(term))[:90].strip()+'")' for term in terms)
        headers={'Accept':'application/json','X-ELS-APIKey':key}
        token=get_credential('elsevier','insttoken')
        if token:headers['X-ELS-Insttoken']=token
        params={'query':'('+query+') AND PUBYEAR > '+str(int(cutoff[:4])-1),
                'count':min(int(config['max_results']),25),'start':0}
        req=request.Request(BASE+'?'+parse.urlencode(params),headers=headers)
        try:
            with request.urlopen(req,timeout=request_timeout(config,35)) as resp:raw=json.load(resp)
        except error.HTTPError as exc:
            raise RuntimeError(f'Elsevier Scopus 拒绝请求（HTTP {exc.code}），请确认 API Key、机构权限与 IP/VPN。') from None
        except (error.URLError, TimeoutError, OSError, ValueError):
            raise RuntimeError('Elsevier Scopus 检索失败，请检查网络和授权设置。') from None
        entries=raw.get('search-results',{}).get('entry',[])
        return [p for item in entries if (p:=parse_entry(item)) and (not p.published or p.published[:10]>=cutoff)]
