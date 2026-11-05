"""Read-only HTTP safety checks. No credentials or user data are sent."""
from __future__ import annotations
import argparse
import json
from urllib import request, error, parse
from datetime import datetime, timezone


def inspect(origin: str):
    target=parse.urlsplit(origin)
    if target.scheme != 'https' and not (target.scheme == 'http' and target.hostname in ('127.0.0.1','localhost')):
        raise ValueError('Require HTTPS or loopback HTTP')
    if not target.netloc or target.username or target.password or target.path not in ('','/') or target.query or target.fragment:
        raise ValueError('Expected bare service origin')
    checks=[]
    def probe(label,route,expected,*,method='GET',headers=None,payload=None,verify=None):
        req=request.Request(origin.rstrip('/')+route,method=method,headers=headers or {},data=payload)
        try:
            with request.urlopen(req,timeout=10) as r:
                status=r.status;h=dict(r.headers);text=r.read(8192).decode('utf-8','replace')
        except error.HTTPError as exc:
            status=exc.code;h=dict(exc.headers);text=exc.read(8192).decode('utf-8','replace')
        except Exception as exc:
            checks.append({'name':label,'passed':False,'error':type(exc).__name__});return
        checks.append({'name':label,'passed':status==expected and (verify(h,text) if verify else True),'status':status})
    probe('health','/api/public/health',200,verify=lambda h,t:'Paper-Radar Public' in t)
    probe('security_headers','/api/public/health',200,verify=lambda h,t:
        h.get('X-Frame-Options')=='DENY' and h.get('X-Content-Type-Options')=='nosniff' and
        h.get('Cache-Control')=='no-store' and bool(h.get('Content-Security-Policy')) and
        (target.scheme!='https' or bool(h.get('Strict-Transport-Security'))))
    probe('authenticated_profile','/api/public/me',401)
    probe('admin_protection','/api/public/admin/accounts',401)
    probe('desktop_isolation','/api/state',401)
    probe('cross_origin_write','/api/public/login',403,method='POST',payload=b'{}',headers={
        'Origin':'https://invalid.example','X-Portal-Action':'1','Content-Type':'application/json'})
    probe('script_access','/portal.js',200,verify=lambda h,t:'portal' in t.lower() or 'async function api' in t)
    return {'checked_at':datetime.now(timezone.utc).isoformat(), 'origin':origin,'checks':checks,
            'passed':sum(item['passed'] for item in checks),'total':len(checks),
            'ok':all(item['passed'] for item in checks),
            'unverified':['external TLS/Nginx deployment','authenticated browser workflows',
                          'Windows automatic updater','live arXiv and cloud model quotas','load and disaster recovery']}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--origin',required=True)
    parser.add_argument('--output',default='');args=parser.parse_args()
    result=inspect(args.origin)
    output=json.dumps(result,ensure_ascii=False,indent=2)
    print(output)
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(output+'\n',encoding='utf-8')
    raise SystemExit(0 if result['ok'] else 1)
