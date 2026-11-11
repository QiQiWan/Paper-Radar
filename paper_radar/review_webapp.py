# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Local-only blind PDF annotation and adjudication workbench.

Run `python -m paper_radar.review_webapp init` once, then `... serve`.
The service never binds a public interface and never calls a model API.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime,timedelta,timezone
import hmac
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import secrets
import sqlite3
import threading
from pathlib import Path
from urllib.parse import parse_qs,urlsplit

from . import review_store as store

ROOT=Path(__file__).resolve().parent.parent
STATIC=Path(__file__).resolve().parent/'static'
ALLOWED_ASSETS={'/review.css':'review.css','/review.js':'review.js'}


class ReviewServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True

    def __init__(self,address,*,dataset,db_file,pdf_dir=None):
        super().__init__(address,ReviewHandler)
        self.dataset=Path(dataset).resolve()
        self.db_file=Path(db_file).resolve()
        self.pdf_dir=Path(pdf_dir).resolve() if pdf_dir else None
        self.tokens={}
        self.sessions_lock=threading.Lock()
        with closing(store.connect(self.db_file)) as db:
            store.verify_frozen_corpus(db,self.dataset)
            if db.execute('SELECT COUNT(*) FROM reviewers').fetchone()[0]!=4:
                raise RuntimeError('Run `python -m paper_radar.review_webapp init` first')

    def authenticate(self,token):
        if not token:return None
        key=store._sha(token)
        with self.sessions_lock:
            item=self.tokens.get(key)
            if item and item['expires']>datetime.now(timezone.utc):
                return item['role']
            self.tokens.pop(key,None)
        return None

    def login(self,role):
        token=secrets.token_urlsafe(36)
        with self.sessions_lock:
            self.tokens[store._sha(token)]={'role':role,
                'expires':datetime.now(timezone.utc)+timedelta(hours=8)}
        return token


class ReviewHandler(BaseHTTPRequestHandler):
    server_version='PaperRadarBlindReview/1.46'

    def log_message(self,format,*args):
        # Do not record session cookies or PDF URLs.
        return

    def reply(self,status,payload,*,cookies=None):
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        if cookies:self.send_header('Set-Cookie',cookies)
        self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)

    def _host_ok(self):
        expected={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        return self.headers.get('Host','') in expected

    def _role(self):
        jar=SimpleCookie()
        try:jar.load(self.headers.get('Cookie',''))
        except Exception:return None
        token=jar.get('pr_blind_review')
        return self.server.authenticate(token.value if token else '')

    def _static(self,name,mime):
        data=(STATIC/name).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type',mime)
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)

    def do_GET(self):
        if not self._host_ok():return self.reply(403,{'error':'Local access only'})
        url=urlsplit(self.path);route=url.path
        if route=='/':return self._static('review.html','text/html; charset=utf-8')
        if route in ALLOWED_ASSETS:
            return self._static(ALLOWED_ASSETS[route],
                                'text/css; charset=utf-8' if route.endswith('.css') else
                                'application/javascript; charset=utf-8')
        role=self._role()
        if role is None:return self.reply(401,{'error':'Please log in to the blind review workbench'})
        query=parse_qs(url.query)
        try:
            with closing(store.connect(self.server.db_file)) as db:
                if route=='/api/me':return self.reply(200,{'role':role,'blind':role in ('A','B')})
                if route=='/api/progress':
                    info=store.progress(db)
                    return self.reply(200,info if role in ('adjudicator','auditor') else
                        {'tasks':info['tasks'],'my_submitted':info[f'reviewer_{role}'],'counts':info['counts']})
                if route=='/api/tasks':
                    return self.reply(200,{'tasks':store.list_tasks(db,role,kind=(query.get('kind') or [None])[0])})
                if route=='/api/task':
                    return self.reply(200,store.view_task(db,role,(query.get('id') or [''])[0]))
            if route=='/api/pdf':
                pid=(query.get('paper_id') or [''])[0]
                path,_=store.verified_pdf_path(self.server.dataset,pid,pdf_dir=self.server.pdf_dir)
                blob=path.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type','application/pdf')
                self.send_header('Content-Disposition','inline; filename="research-paper.pdf"')
                self.send_header('Cache-Control','private, no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Content-Length',str(len(blob)))
                self.end_headers();self.wfile.write(blob)
                return
            return self.reply(404,{'error':'Route not found'})
        except (OSError,ValueError,RuntimeError) as exc:
            return self.reply(422,{'error':str(exc)[:300]})

    def do_POST(self):
        if not self._host_ok():return self.reply(403,{'error':'Local access only'})
        origin=self.headers.get('Origin','')
        if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}',
                                      f'http://localhost:{self.server.server_port}'}:
            return self.reply(403,{'error':'Cross-origin actions denied'})
        if self.headers.get('X-PaperRadar-Review')!='1':
            return self.reply(403,{'error':'Review CSRF guard missing'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=10000 or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                return self.reply(413,{'error':'Invalid JSON request'})
            data=json.loads(self.rfile.read(size))
            if type(data) is not dict:return self.reply(400,{'error':'Expected an object'})
            route=urlsplit(self.path).path
            if route=='/api/login':
                user=data.get('username','')
                with closing(store.connect(self.server.db_file)) as db:
                    verified=store.verify_password(db,user,data.get('password',''))
                if not verified:
                    return self.reply(401,{'error':'Invalid reviewer identity or password'})
                token=self.server.login(user)
                return self.reply(200,{'role':user},
                    cookies=f'pr_blind_review={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800')
            role=self._role()
            if role is None:return self.reply(401,{'error':'Login expired'})
            if route=='/api/logout':
                return self.reply(200,{'ok':True},
                    cookies='pr_blind_review=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            with closing(store.connect(self.server.db_file)) as db:
                if route=='/api/submit':
                    result=store.submit(db,role,data.get('task_id',''),data.get('label'),
                        dataset=self.server.dataset,pdf_dir=self.server.pdf_dir)
                    return self.reply(201,result)
                if route=='/api/adjudicate':
                    result=store.adjudicate(db,role,data.get('task_id',''),data.get('label'),
                        dataset=self.server.dataset,pdf_dir=self.server.pdf_dir)
                    return self.reply(201,result)
            return self.reply(404,{'error':'Route not found'})
        except (ValueError,PermissionError,RuntimeError,OSError,sqlite3.Error) as exc:
            return self.reply(422,{'error':str(exc)[:500]})


def main(argv=None):
    parser=argparse.ArgumentParser(description='Paper-Radar RealPDF30 blind annotation workbench')
    sub=parser.add_subparsers(dest='action',required=True)
    for name in ('init','serve','status','export','export-gold'):
        command=sub.add_parser(name)
        command.add_argument('--dataset',type=Path,default=ROOT/'benchmarks/real_pdf30')
        command.add_argument('--db',type=Path,default=ROOT/'.paper_radar_home/realpdf30_blind.sqlite')
        if name=='serve':
            command.add_argument('--port',type=int,default=8778)
            command.add_argument('--pdf-dir',type=Path,default=None)
        if name=='export':command.add_argument('--out',type=Path,default=ROOT/'review_export.json')
        if name=='export-gold':command.add_argument('--out',type=Path,default=ROOT/'review_gold_export')
        if name in ('export','export-gold'):command.add_argument('--pdf-dir',type=Path,default=None)
    args=parser.parse_args(argv)
    if args.action=='init':
        print(json.dumps(store.initialize(args.db,args.dataset),ensure_ascii=False,indent=2))
        return 0
    if args.action=='serve':
        if not 1024<=args.port<=65535:parser.error('Port must be 1024-65535')
        app=ReviewServer(('127.0.0.1',args.port),dataset=args.dataset,
                         db_file=args.db,pdf_dir=args.pdf_dir)
        print(f'Local blind review: http://127.0.0.1:{args.port}/',flush=True)
        try:app.serve_forever(poll_interval=.25)
        except KeyboardInterrupt:pass
        finally:app.server_close()
        return 0
    with closing(store.connect(args.db)) as db:
        store.verify_frozen_corpus(db,args.dataset)
        result=(store.progress(db) if args.action=='status' else
            store.export_gold_csv(db,args.dataset,args.out,pdf_dir=args.pdf_dir) if args.action=='export-gold' else
            store.export_audit(db,args.out,dataset=args.dataset,pdf_dir=args.pdf_dir))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
