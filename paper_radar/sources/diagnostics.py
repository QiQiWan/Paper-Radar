# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""On-demand connectivity checks for official public bibliographic hosts.

The application never probes arbitrary addresses, leaks query terms or sends
model credentials. Checks are initiated by a user action, not page polling.
"""
from __future__ import annotations

from time import monotonic
from urllib import request, parse
from .credentials import get_credential
from urllib.error import HTTPError
from .failures import MESSAGES, code_for_exception
from .rate_limit import safe_quota_headers

# Only registered public bibliographic services; never accept arbitrary URLs.
_ENDPOINTS = {
    'arxiv': 'https://export.arxiv.org/api/query?search_query=cat%3Acs.LG&max_results=1',
    'openalex': 'https://api.openalex.org/works?per_page=1&select=id',
    'crossref': 'https://api.crossref.org/works?rows=0',
    'openreview': 'https://api2.openreview.net/notes/search?term=learning&limit=1',
}
_HOSTS = {'arxiv':'export.arxiv.org','openalex':'api.openalex.org',
          'crossref':'api.crossref.org','openreview':'api2.openreview.net'}


def probe_sources(sources=None, *, opener=None, timeout=6.0):
    allowed = tuple(_ENDPOINTS) if sources is None else tuple(
        s for s in sources if s in _ENDPOINTS)
    opener = opener or request.urlopen
    timeout = min(8., max(1., float(timeout)))
    proxy_active = bool(request.getproxies())
    checks = []
    for name in dict.fromkeys(allowed):
        began = monotonic()
        url = _ENDPOINTS[name]
        if name == 'openalex':
            key = get_credential('openalex')
            if key:
                # A private local connectivity check should use the same key
                # as the actual retrieval worker; never include it in results.
                url += '&' + parse.urlencode({'api_key': key})
        req = request.Request(url, headers={
            'User-Agent':'Paper-Radar connectivity check (interactive)',
            'Accept':'application/json, application/atom+xml',
        })
        status = None
        code = ''
        try:
            with opener(req, timeout=timeout) as response:
                status = int(response.status)
                # Read only a bounded amount; the probe is not a search run.
                response.read(1024)
                if status >= 400:
                    code = 'REMOTE_SERVER_ERROR' if status >= 500 else 'SOURCE_UNAVAILABLE'
        except HTTPError as exc:
            status = exc.code
            code = code_for_exception(exc)
        except Exception as exc:
            code = code_for_exception(exc)
        checks.append({'name':name,'host':_HOSTS[name],
            'ok': not code,'code':code or 'OK',
            'http_status':status,'seconds':round(monotonic()-began,2),
            'message':'连接正常' if not code else MESSAGES.get(code,MESSAGES['SOURCE_UNAVAILABLE'])})
    return {'checks':checks,'proxy_configured':proxy_active,
            'note':'仅检查公开文献接口；检查结果与实际关键词查询可能不同。'}


def probe_openalex_quota(*, opener=None, timeout=7.0) -> dict:
    """Explicit user-triggered provider usage check, without exposing a key.

    Authentication travels in the authorization header to the fixed official
    host, so diagnostic exports and HTTP access logs never contain the token.
    """
    token = get_credential('openalex')
    if not token:
        return {'configured':False, 'ok':False, 'code':'AUTH_REQUIRED',
                'message':'请先保存 OpenAlex 免费 API Key，再检查个人额度。'}
    opener = opener or request.urlopen
    req = request.Request('https://api.openalex.org/rate-limit', headers={
        'Authorization':'Bearer '+token,
        'Accept':'application/json','User-Agent':'Paper-Radar quota check (user-initiated)'})
    try:
        with opener(req,timeout=min(10.,max(1.,float(timeout)))) as response:
            # The header is sufficient for a safe numeric summary. Do not
            # surface arbitrary response fields or diagnostic body text.
            info=safe_quota_headers(getattr(response,'headers',None))
            response.read(32768)
            return {'configured':True,'ok':True,'code':'OK',
                    'message':'API Key 已通过额度查询接口验证。', **info}
    except Exception as exc:
        code=code_for_exception(exc)
        detail=(safe_quota_headers(exc.headers,status=exc.code)
                if isinstance(exc,HTTPError) else {})
        return {'configured':True,'ok':False,'code':code,
                'message':MESSAGES.get(code,MESSAGES['SOURCE_UNAVAILABLE']), **detail}


def main(argv=None):
    """Run a single explicit official-source connectivity smoke, no credentials."""
    import argparse
    import json
    parser=argparse.ArgumentParser(description='Official paper source connectivity check')
    parser.add_argument('--sources',nargs='*',choices=sorted(_ENDPOINTS),default=list(_ENDPOINTS))
    parser.add_argument('--timeout',type=float,default=6)
    args=parser.parse_args(argv)
    data=probe_sources(args.sources,timeout=args.timeout)
    print(json.dumps(data,ensure_ascii=False,indent=2))
    return 0 if any(r['ok'] for r in data['checks']) else 1


if __name__=='__main__':
    raise SystemExit(main())
