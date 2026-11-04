# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Opt-in OpenAlex semantic discovery (one bounded query per topic).

This searches public bibliographic metadata, not paid publisher full texts.
Uses the official semantic endpoint; consumption depends on OpenAlex credits.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from urllib import parse,request,error
import json
import os
import time

from .openalex import BASE, parse_work
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
        params={
            'search.semantic':query,
            'filter':f'from_publication_date:{start},to_publication_date:{today.isoformat()}',
            'per_page':min(50,max(1,int(config.get('max_results',30)))),
            'page':1,
        }
        # No embedded credentials. OpenAlex API key may be set explicitly in
        # environment; never log the key or the fully assembled URL.
        api_key=os.environ.get('OPENALEX_API_KEY','').strip()
        if api_key:
            params['api_key']=api_key
        req=request.Request(BASE+'?'+parse.urlencode(params),headers={
            'User-Agent':f'Paper-Radar/{__version__} (local research semantic search)',
            'Accept':'application/json'})
        logger('OpenAlex 语义发现：正在查询近期论文元数据（可能消耗服务商积分）。')
        for attempt in range(3):
            try:
                with request.urlopen(req,timeout=35) as response:
                    raw=response.read(4*1024*1024+1)
                if len(raw)>4*1024*1024:
                    raise RuntimeError('OpenAlex 语义查询响应超出大小限制。')
                payload=json.loads(raw)
                if not isinstance(payload,dict) or not isinstance(payload.get('results'),list):
                    raise RuntimeError('OpenAlex 语义查询返回数据格式错误。')
                papers=[]
                for item in payload['results']:
                    if isinstance(item,dict) and (p:=parse_work(item)):
                        p.source='openalex_semantic'
                        papers.append(p)
                return papers
            except error.HTTPError as exc:
                if exc.code in (400,401,402,403,429):
                    raise RuntimeError(f'OpenAlex 语义请求被拒绝（HTTP {exc.code}），请核对 API Key、余额和额度。') from None
                if attempt==2:
                    raise RuntimeError(f'OpenAlex 语义查询失败（HTTP {exc.code}）。') from None
            except (error.URLError,TimeoutError,OSError) as exc:
                if attempt==2:
                    raise RuntimeError('OpenAlex 语义查询连接失败，请检查网络。') from exc
            if attempt<2:
                time.sleep(1.5*(attempt+1))
        return []
