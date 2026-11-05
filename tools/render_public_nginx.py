#!/usr/bin/env python3
"""Render the included HTTPS reverse proxy template with a validated port/domain."""
from __future__ import annotations
import argparse
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parent.parent
DOMAIN=re.compile(r'^(?=.{4,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z][a-zA-Z0-9-]{1,62}$')


def render(domain: str,port: int) -> str:
    if not DOMAIN.fullmatch(domain):
        raise ValueError('Use a normal DNS domain such as papers.example.com')
    if type(port) is not int or not 1<=port<=65535:
        raise ValueError('Port must be an integer between 1 and 65535')
    src=(ROOT/'deploy/nginx-paper-radar.conf').read_text(encoding='utf-8')
    return src.replace('papers.example.com',domain).replace('127.0.0.1:8899',f'127.0.0.1:{port}')


def main(argv=None):
    parser=argparse.ArgumentParser(description='Render Paper-Radar HTTPS reverse proxy configuration')
    parser.add_argument('--domain',required=True)
    parser.add_argument('--port',required=True,type=int)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args(argv)
    content=render(args.domain,args.port)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    if args.out.exists():raise SystemExit('Refusing to overwrite an existing Nginx config')
    args.out.write_text(content,encoding='utf-8')
    print(f'Wrote {args.out}. Check TLS cert paths; run nginx -t before reloading.')

if __name__=='__main__':main()
