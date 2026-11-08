# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""ScienceDirect Search v2 metadata-only adapter.

Requires an entitled Elsevier API Key. The supported GET interface is a
compatibility path; no subscriptions or full-text downloads are bypassed.
"""
from __future__ import annotations
from .request_policy import request_timeout
from datetime import datetime, timedelta, timezone
from urllib import request, parse, error
import json
from .credentials import get_credential
from .elsevier import parse_entry

BASE = 'https://api.elsevier.com/content/search/sciencedirect'

class ScienceDirectSource:
    name = 'sciencedirect'
    label = 'Elsevier · ScienceDirect'
    access = 'institutional'
    guide_url = 'https://dev.elsevier.com/documentation/SCIDIRSearchAPI.wadl'
    note = '需要经 Elsevier 授权的 ScienceDirect API Key；可能还需要机构 IP/VPN 或机构令牌。仅检索题录。'

    def fetch(self, config, logger=print, now=None):
        key = get_credential('elsevier')
        if not key:
            raise RuntimeError('ScienceDirect 未配置 Elsevier API Key，请在设置 → 出版社检索授权中填写。')
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cutoff = (now - timedelta(days=int(config['lookback_days']))).date().isoformat()
        terms = [str(s).strip().replace('"', '')[:90] for s in config.get('keywords', [])[:3] if str(s).strip()]
        if not terms:
            return []
        headers = {'Accept':'application/json','X-ELS-APIKey':key,'User-Agent':'Paper-Radar/1.9'}
        token = get_credential('elsevier','insttoken')
        if token:
            headers['X-ELS-Insttoken'] = token
        params = {'query':' OR '.join('"'+s+'"' for s in terms),
                  'count':min(int(config['max_results']),25),'start':0}
        req = request.Request(BASE+'?'+parse.urlencode(params),headers=headers)
        try:
            with request.urlopen(req,timeout=request_timeout(config,35)) as resp:
                payload = json.load(resp)
        except error.HTTPError as exc:
            raise RuntimeError(f'ScienceDirect 拒绝请求（HTTP {exc.code}）；请检查 API 权限、校园网/VPN 或机构令牌。') from None
        except (error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise RuntimeError('ScienceDirect 检索失败，请检查网络与机构授权。') from None
        results = payload.get('search-results',{})
        if not isinstance(results,dict):
            raise RuntimeError('ScienceDirect 返回的数据格式不正确。')
        out = []
        for item in results.get('entry',[]) or []:
            parsed = parse_entry(item)
            if parsed is None:
                continue
            parsed.paper_id = 'sciencedirect:'+parsed.paper_id.partition(':')[-1]
            parsed.source = 'sciencedirect'
            parsed.is_oa = int(str(item.get('openaccess') or '').lower() in ('1','true','yes'))
            # OA status alone does not entitle direct PDF reuse.
            parsed.pdf_url = parsed.download_url = ''
            if not parsed.published or parsed.published[:10] >= cutoff:
                out.append(parsed)
        return out
