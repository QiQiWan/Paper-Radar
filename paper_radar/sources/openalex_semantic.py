# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Opt-in OpenAlex semantic discovery (one bounded query per topic).

This searches public bibliographic metadata, not paid publisher full texts.
Uses the official semantic endpoint; consumption depends on OpenAlex credits.
"""
from __future__ import annotations
from .credentials import get_credential
from .request_policy import request_timeout, is_configuration_network_error
from datetime import datetime, timedelta, timezone
from urllib import parse,request,error
import json
import time

from .openalex import BASE, parse_work, fetch_openalex_json
from time import monotonic
from .failures import SourceQueryError
from ..quality import valid_recent_publication
from .. import __version__


class OpenAlexSemanticSource:
    name='openalex_semantic'
    label='OpenAlex 语义发现（可选）'
    access='public'
    guide_url='https://help.openalex.org/api/semantic-search/'
    note='可发现不同术语表达的相关论文；单次最多50篇，按 OpenAlex 实际计费或额度使用，默认关闭。'

    def fetch(self,config,logger=print,now=None):
        now=now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now=now.replace(tzinfo=timezone.utc)
        today=now.astimezone(timezone.utc).date()
        goal=str(config.get('research_goal') or '').strip()
        query=(goal if len(goal)>=12 else '; '.join(config.get('keywords',[])[:12])).strip()[:2000]
        if len(query)<8:
            logger('OpenAlex 语义发现缺少具体研究问题或检索词，本次跳过。')
            return []
        start=(today-timedelta(days=int(config['lookback_days']))).isoformat()
        # The OpenAlex semantic vector index supports publication_year, but
        # NOT from_publication_date / to_publication_date (HTTP 400). Fetch the
        # relevant calendar years and enforce the exact lookback locally.
        years = '|'.join(str(y) for y in range(int(start[:4]), today.year + 1))
        params={
            'search.semantic':query,
            'filter':f'publication_year:{years}',
            'per_page':min(50,max(1,int(config.get('max_results',30)))),
            'page':1,
        }
        # No embedded credentials. OpenAlex API key may be set explicitly in
        # environment; never log the key or the fully assembled URL.
        api_key=get_credential('openalex')
        if api_key:
            params['api_key']=api_key
        req=request.Request(BASE+'?'+parse.urlencode(params),headers={
            'User-Agent':f'Paper-Radar/{__version__} (local research semantic search)',
            'Accept':'application/json'})
        logger('OpenAlex 语义发现：正在查询近期论文元数据（可能消耗服务商积分）。')
        try:
            payload=fetch_openalex_json(req,config,started=monotonic(),semantic=True)
        except SourceQueryError as exc:
            if exc.code == 'RATE_LIMIT':
                raise RuntimeError('OpenAlex 语义请求被限流（HTTP 429）') from exc
            raise
        papers=[]
        for item in payload['results']:
            if isinstance(item,dict) and (p:=parse_work(item)) and valid_recent_publication(
                    p.published, now, int(config['lookback_days'])):
                p.source='openalex_semantic'
                papers.append(p)
        return papers
