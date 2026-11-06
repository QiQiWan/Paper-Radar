# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Offline template checks and loopback smoke for a safe public deployment.

This tool does not claim to execute a real systemd unit, TLS termination,
firewall rules, or production account provisioning. Those require host checks.
"""
from __future__ import annotations
import argparse
import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

ROOT=Path(__file__).resolve().parent.parent


def inspect_templates(root: Path = ROOT) -> list[str]:
    root=Path(root)
    pub=(root/'deploy/paperradar-public.service').read_text(encoding='utf-8')
    worker=(root/'deploy/paperradar-worker@.service').read_text(encoding='utf-8')
    nginx=(root/'deploy/nginx-paper-radar.conf').read_text(encoding='utf-8')
    code=(root/'paper_radar/public_portal.py').read_text(encoding='utf-8')
    problems=[]
    requirements=[
        ('public', 'ProtectSystem=strict', pub),
        ('public', 'ReadWritePaths=/var/lib/paperradar', pub),
        ('public', 'StateDirectory=paperradar', pub),
        ('public', '--trust-proxy-ip', pub),
        ('public', '--disable-registration', pub),
        ('public', '--host 127.0.0.1', pub),
        ('public', '--data-root /var/lib/paperradar/portal', pub),
        ('worker', 'ProtectSystem=strict', worker),
        ('worker', 'ReadWritePaths=/var/lib/paperradar', worker),
        ('worker', 'StateDirectory=paperradar', worker),
        ('runtime', "runtime=portal.root/'runtime'", code),
    ]
    for label,needle,haystack in requirements:
        if needle not in haystack:
            problems.append(f'{label}: missing {needle}')
    if nginx.count('proxy_set_header X-Real-IP $remote_addr;')!=2:
        problems.append('nginx: the PDF and normal locations must overwrite X-Real-IP')
    if nginx.count('proxy_pass http://127.0.0.1:8899;')!=2:
        problems.append('nginx: all routes must use the loopback backend')
    if 'location = /api/public/pdf {' not in nginx or 'client_max_body_size 34m;' not in nginx:
        problems.append('nginx: isolated PDF request limit not found')
    if 'ssl_protocols TLSv1.2 TLSv1.3;' not in nginx:
        problems.append('nginx: TLS protocol restriction missing')
    if "marker=ROOT/'.paper_radar_updates'" in code:
        problems.append('runtime: immutable source tree is still used for service marker')
    return problems


def smoke() -> dict:
    """Exercises the same Python HTTP server and privacy-gated registration.

    Uses fake IPs at the HTTP layer to exercise trusted-proxy processing.
    It is NOT a substitute for a real Nginx TLS, firewall or systemd test.
    """
    from paper_radar.public_portal import Portal,make_server
    with TemporaryDirectory(prefix='paper-radar-public-smoke-') as td:
        portal=Portal(Path(td)/'portal',allow_registration=False,
                      trust_proxy_ip=True,embedded_workers=False)
        server=make_server('127.0.0.1',0,portal)
        thread=Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            def req(method,path,data=None):
                headers={'Host':f'127.0.0.1:{server.server_port}','X-Real-IP':'198.51.100.12'}
                body=None
                if method=='POST':
                    headers.update({'Origin':portal.origin,'X-Portal-Action':'1',
                                    'Content-Type':'application/json'})
                    body=json.dumps(data or {}).encode('utf-8')
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=4)
                try:
                    conn.request(method,path,headers=headers,body=body)
                    response=conn.getresponse()
                    return response.status,json.loads(response.read())
                finally:conn.close()
            health_status, health=req('GET','/api/public/health')
            signup_status, _=req('POST','/api/public/register',
                                 {'username':'Unauthorized','password':'test long password 12345'})
            assert health_status==200 and health['registration_enabled'] is False,health
            assert signup_status==403,signup_status
            assert health['version']=='1.26.0',health
            return {'health':health_status,'registration_blocked':signup_status,
                    'version':health['version']}
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Paper-Radar v1.26.0 public deployment checks')
    parser.add_argument('--smoke',action='store_true',help='run real loopback HTTP smoke (no external network)')
    args=parser.parse_args(argv)
    problems=inspect_templates()
    if problems:
        for issue in problems: print('FAIL:',issue)
        return 2
    print('PASS: deployment template invariants')
    if args.smoke:
        try: print('PASS: HTTP smoke',json.dumps(smoke()))
        except Exception as exc:
            print('FAIL: loopback HTTP smoke:',type(exc).__name__)
            return 3
    print('NOT TESTED: actual nginx -t, TLS, systemd, firewall, multi-user load, Windows')
    return 0


if __name__=='__main__':raise SystemExit(main())
