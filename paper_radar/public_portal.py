# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Opt-in public, multi-account portal with a deliberately small API surface.

Important: This is an isolated internet-facing interface, NOT a reverse proxy
of the trusted desktop webapp. Per-user workspace files are derived exclusively
from authenticated, randomly generated account IDs. Desktop-only settings,
Zotero, API credentials, raw file handlers and the auto-updater are absent.
"""
from __future__ import annotations
import argparse
import ipaddress
import csv
from datetime import datetime, timezone
from contextlib import contextmanager
from hashlib import sha256
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from threading import Lock, BoundedSemaphore
from urllib.parse import urlsplit,parse_qs
import uuid

from . import __version__
from .portal_auth import (db as auth_db, create_account, check_login, create_session,
    authenticate, delete_session, throttle, set_active,change_password,AuthError)
from .migrations import migrate
from .synthesis import compare_records,to_markdown
from .portal_jobs import PortalJobs, QueueError, JobCancelled
from . import portal_research, portal_reports

ROOT=Path(__file__).resolve().parent.parent
STATIC=Path(__file__).resolve().parent/'static'
MAX_POST=400_000
TOPIC_ID=re.compile(r'^[a-z0-9_-]{1,48}$')
UID=re.compile(r'^[0-9a-f]{32}$')


class PortalError(ValueError):pass


def utc_now():return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Portal:
    def __init__(self, data_root: Path, *, origin: str='', allow_registration=True,
                 fetcher=None, trust_proxy_ip=False, job_workers=2,
                 max_pending_jobs=100, daily_search_limit=10, embedded_workers=True):
        self.root=Path(data_root).resolve()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        if os.name!='nt':os.chmod(self.root,0o700)
        self.auth_path=self.root/'accounts.sqlite3'
        self.origin=origin.rstrip('/')
        self.allow_registration=allow_registration
        self.fetcher=fetcher
        self.embedded_workers=bool(embedded_workers)
        self.trust_proxy_ip=bool(trust_proxy_ip)
        self._arxiv_lock=Lock()
        self.pdf_lock=Lock()
        self._audit_lock=Lock()
        self.model_callable=None
        self.pdf_downloader=portal_research.fetch_arxiv_pdf
        self.job_queue=PortalJobs(self.auth_path,self.search,
            max_workers=job_workers,max_pending=max_pending_jobs,daily_limit=daily_search_limit,special_worker=self.research_job)

    def audit(self,route:str,method:str,status:int):
        """Log event types, never bearer tokens, passwords, IP or paper content."""
        if status<400 and method=='GET':return
        payload={'at':utc_now(),'route':route[:90],'method':method,'status':status}
        with self._audit_lock:
            path=self.root/'audit.jsonl'
            try:
                if path.exists() and path.stat().st_size>5_000_000:
                    os.replace(path,self.root/'audit.previous.jsonl')
                with path.open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(payload,ensure_ascii=False)+'\n')
                if os.name!='nt':os.chmod(path,0o600)
            except OSError:pass

    def workspace_path(self,uid:str) -> Path:
        if not UID.fullmatch(uid):raise PortalError('Invalid account identifier')
        base=self.root/'users'/uid
        base.mkdir(parents=True,exist_ok=True,mode=0o700)
        if os.name!='nt':os.chmod(base,0o700)
        target=base/'research.sqlite3'
        if base.is_symlink() or target.is_symlink() or not target.resolve().is_relative_to(self.root):
            raise PortalError('Unsafe workspace')
        return target

    @contextmanager
    def workspace(self,uid:str):
        p=self.workspace_path(uid)
        conn=sqlite3.connect(p,timeout=15,check_same_thread=False)
        conn.row_factory=sqlite3.Row
        migrate(conn,'portal_workspace')
        conn.execute("INSERT OR IGNORE INTO topics VALUES ('default',?,?,?)",
                     ('默认研究主题',json.dumps(['neural operator','operator learning']),utc_now()))
        conn.commit()
        try:
            with conn:yield conn
        finally:conn.close()

    def available(self, uid: str, topic_id: str) -> tuple[Path,dict]:
        if not TOPIC_ID.fullmatch(topic_id):raise PortalError('Invalid topic')
        path=self.workspace_path(uid)
        with self.workspace(uid) as db:
            row=db.execute('SELECT * FROM topics WHERE id=?',(topic_id,)).fetchone()
        if not row:raise PortalError('研究主题不存在')
        return path,dict(row)

    def search(self,uid:str,topic_id:str,should_cancel=None):
        """Bounded arXiv public metadata retrieval; no private provider creds."""
        _, topic=self.available(uid,topic_id)
        if should_cancel and should_cancel():raise JobCancelled('Task cancelled')
        keywords=json.loads(topic['keywords'])
        from .core import DEFAULTS
        from .sources.arxiv import fetch_arxiv
        cfg=dict(DEFAULTS)
        cfg.update(keywords=keywords[:6],categories=[],max_results=20,lookback_days=14)
        if self.fetcher:
            # Tests and explicitly supplied fetcher hooks remain deterministic.
            papers=self.fetcher(cfg,logger=lambda _:None)
        else:
            # The traditional arXiv API requires serial requests and spacing.
            # Persist the last call in the existing auth_attempts journal so a
            # service restart does not immediately reset the source throttle.
            from .portal_resource_locks import exclusive_file_lock
            with self._arxiv_lock, exclusive_file_lock(self.root/'.arxiv_global.lock',cancelled=should_cancel):
                for _ in range(80):
                    if should_cancel and should_cancel():raise JobCancelled('Task cancelled')
                    with auth_db(self.auth_path) as adb:
                        allowed=throttle(adb,'source:arxiv_public',limit=1,seconds=4)
                    if allowed:break
                    time.sleep(.25)
                else:
                    raise PortalError('arXiv 公共接口仍处于访问间隔，请稍后重试')
                try:
                    papers=fetch_arxiv(cfg,logger=lambda _:None)
                finally:
                    # Also gate from the *end* of the previous HTTP operation;
                    # the source adapter can retry internally after the first
                    # request and another worker must not start immediately.
                    with auth_db(self.auth_path) as adb:
                        adb.execute("DELETE FROM auth_attempts WHERE identifier='source:arxiv_public'")
                        adb.execute('INSERT INTO auth_attempts(identifier,occurred_at) VALUES (?,?)',
                                    ('source:arxiv_public',int(time.time())))
        if not isinstance(papers,list):raise PortalError('检索来源返回数据无效')
        if should_cancel and should_cancel():raise JobCancelled('Task cancelled')
        inserted=0
        with self.workspace(uid) as db:
            for paper in papers[:20]:
                if should_cancel and should_cancel():raise JobCancelled('Task cancelled')
                pid=str(paper.paper_id or '')[:150]
                title=str(paper.title or '')[:400]
                if not pid or not title:continue
                cursor=db.execute('INSERT OR IGNORE INTO papers VALUES (?,?,?,?,?,?,?)',
                    (pid,title,str(paper.abstract or '')[:10000],str(paper.published or '')[:40],
                     str(paper.url or '')[:600],str(paper.doi or '')[:200],utc_now()))
                inserted+=cursor.rowcount
                db.execute('INSERT OR IGNORE INTO topic_papers VALUES (?,?)',(topic_id,pid))
            if should_cancel and should_cancel():raise JobCancelled('Task cancelled')
            db.commit()
        return {'retrieved':len(papers),'new':inserted,'topic_id':topic_id}

    def research_job(self,uid,topic,kind,pid,options,cancel,progress,job_id=None):
        self.available(uid,topic)
        cloud=options.get('cloud_analysis',False)
        if kind=='analyze':
            progress('fulltext')
            try:
                result=portal_research.analyze(self,uid,topic,pid,cloud=cloud,cancel=cancel)
                if cloud:progress('evidence')
                return {'kind':kind,'analysis':result}
            except InterruptedError:raise JobCancelled('Cancelled')
        if kind=='synthesize':
            progress('synthesis')
            report=portal_reports.create_report(self,uid,topic,job_id=job_id)
            return {'kind':kind,'finding_count':report['finding_count'],
                    'paper_count':report['paper_count'],'report_id':report['id']}
        if kind!='research':raise PortalError('Invalid research task')
        progress('discovery');found=self.search(uid,topic,cancel)
        with self.workspace(uid) as db:
            ids=[r['id'] for r in db.execute('''SELECT p.id FROM papers p JOIN topic_papers t ON p.id=t.paper_id
                WHERE t.topic_id=? ORDER BY p.published DESC LIMIT 12''',(topic,))]
        results=[]
        for pid in [x for x in ids if portal_research.ARXIV_ID.fullmatch(x)][:2]:
            if cancel():raise JobCancelled('Cancelled')
            st=portal_research.state(self,uid,topic,pid)
            if st['state'] in ('missing','stale') and options.get('fetch_pdf',True):
                try:
                    progress('download')
                    portal_research.save_pdf(self,uid,topic,pid,self.pdf_downloader(pid),source='arxiv_public')
                except Exception as exc:
                    results.append({'paper_id':pid,'state':'unavailable','reason':type(exc).__name__});continue
            st=portal_research.state(self,uid,topic,pid)
            if st['state'] in ('saved','indexed'):
                try:
                    progress('fulltext')
                    results.append(portal_research.analyze(self,uid,topic,pid,cloud=cloud,cancel=cancel))
                    if cloud:progress('evidence')
                except InterruptedError:raise JobCancelled('Cancelled')
                except Exception as exc:results.append({'paper_id':pid,'state':'error','reason':type(exc).__name__})
        progress('synthesis');report=portal_reports.create_report(self,uid,topic,job_id=job_id)
        completed=sum(x.get('state')=='indexed' for x in results)
        partial=not results or completed!=len(results) or (cloud and any(x.get('cloud_analysis') not in ('verified','cached','no_supported_findings') for x in results))
        return {'kind':'research','state':'partial' if partial else 'complete','discovery':found,
                'analyzed':completed,'papers':results,'report_id':report['id'],
                'synthesis':{'finding_count':report['finding_count'],
                'paper_count':report['paper_count'],'comparison_count':report['comparison_count']}}

    def research_async(self,uid,topic,*,kind='research',pid='',options=None):
        self.available(uid,topic)
        if kind=='analyze':portal_research.check_member(self,uid,topic,pid)
        return self.job_queue.enqueue(uid,topic,kind=kind,paper_id=pid,options=options)

    def search_async(self,uid:str,topic_id:str):
        self.available(uid,topic_id)
        try:
            return self.job_queue.enqueue(uid,topic_id)
        except QueueError as exc:
            raise PortalError(str(exc)) from exc

    def job(self,uid:str):
        return self.job_queue.latest(uid)

    def job_history(self,uid:str,limit=10):
        return self.job_queue.history(uid,limit=limit)

    def job_detail(self,uid:str,job_id:int):
        return self.job_queue.detail(uid,job_id)

    def cancel_job(self,uid:str,job_id=None):
        return self.job_queue.cancel(uid,job_id)


def _json(data,max_bytes=MAX_POST):
    if len(data)>max_bytes:raise PortalError('Request too large')
    value=json.loads(data or b'{}')
    if not isinstance(value,dict):raise PortalError('Expected JSON object')
    return value


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(25)
    server_version='Paper-Radar-Public/'+__version__
    protocol_version='HTTP/1.0'

    def log_message(self,fmt,*args):
        # No credentials or private query strings in logs.
        pass

    def security_headers(self, *, pdf_frame=False):
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','SAMEORIGIN' if pdf_frame else 'DENY')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Permissions-Policy','camera=(),microphone=(),geolocation=()')
        if self.server.portal.origin.startswith('https://'):
            self.send_header('Strict-Transport-Security','max-age=31536000')
        self.send_header('Content-Security-Policy',
             "default-src 'self'; script-src 'self'; style-src 'self'; "
             "connect-src 'self'; img-src 'self' data:; form-action 'self'; "
             "frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors "+("'self'" if pdf_frame else "'none'"))

    def send_json(self,status:int,data:dict,*,cookie:str|None=None):
        self.server.portal.audit(urlsplit(self.path).path,self.command,status)
        raw=json.dumps(data,ensure_ascii=False,default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.security_headers()
        if cookie:self.send_header('Set-Cookie',cookie)
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_static(self,path:Path,type_:str):
        data=path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type',type_)
        self.security_headers()
        self.send_header('Content-Length',str(len(data)))
        self.end_headers();self.wfile.write(data)

    def send_user_pdf(self, uid: str, topic: str, paper_id: str):
        """Stream only a current-account PDF, validated against its stored hash."""
        portal=self.server.portal
        document=portal_research.state(portal,uid,topic,paper_id)
        if document['state'] not in ('indexed','saved'):
            return self.send_json(404,{'error':'当前账号暂无有效 PDF；请重新上传或获取开放版本'})
        path=portal_research.path_for(portal,uid,paper_id)
        size=path.stat().st_size
        if size>portal_research.MAX_PDF:
            return self.send_json(413,{'error':'PDF 文件超出限制'})
        # Some PDF viewers request byte ranges for fast page navigation.
        raw_range=self.headers.get('Range','')
        start,end=0,size-1
        if raw_range:
            import re as _re
            match=_re.fullmatch(r'bytes=(\d+)-(\d*)',raw_range)
            if not match:
                return self.send_json(416,{'error':'无效的 PDF 字节范围'})
            start=int(match.group(1))
            end=int(match.group(2)) if match.group(2) else min(size-1,start+1024*1024-1)
            if start>=size or end<start or end>=size or end-start>2*1024*1024:
                return self.send_json(416,{'error':'PDF 字节范围超出限制'})
        with path.open('rb') as stream:
            stream.seek(start)
            payload=stream.read(end-start+1)
        self.send_response(206 if raw_range else 200)
        self.send_header('Content-Type','application/pdf')
        self.security_headers(pdf_frame=True)
        self.send_header('Content-Disposition','inline; filename="paper.pdf"')
        self.send_header('Accept-Ranges','bytes')
        if raw_range:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers();self.wfile.write(payload)

    def guard_host(self):
        actual=self.headers.get('Host','')
        expected=urlsplit(self.server.portal.origin).netloc
        return actual==expected and bool(expected)

    def client_ip(self):
        # This service only accepts loopback connections. The trusted reverse
        # proxy must explicitly overwrite X-Real-IP, never append user input.
        address=self.client_address[0]
        if self.server.portal.trust_proxy_ip and address in ('127.0.0.1','::1'):
            forwarded=self.headers.get('X-Real-IP','').strip()
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        return str(address)

    def cookie_value(self):
        try:
            parsed=SimpleCookie()
            parsed.load(self.headers.get('Cookie',''))
            return parsed['PRSession'].value if 'PRSession' in parsed else ''
        except Exception:return ''

    def session(self):
        raw=self.cookie_value()
        with auth_db(self.server.portal.auth_path) as db:
            return authenticate(db,raw)

    def session_cookie(self,token:str,*,clear=False):
        secure='; Secure' if self.server.portal.origin.startswith('https://') else ''
        if clear:
            return 'PRSession=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0'+secure
        return 'PRSession='+token+'; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800'+secure

    def do_GET(self):
        try:self._handle_get()
        except (PortalError,AuthError,ValueError,KeyError,TypeError) as exc:
            self.send_json(400,{'error':str(exc)[:180]})
        except Exception:
            self.send_json(500,{'error':'服务器处理失败，请联系管理员查看服务器日志'})

    def _handle_get(self):
        if not self.guard_host():return self.send_json(403,{'error':'Host mismatch'})
        route=urlsplit(self.path).path
        if route in ('/','/portal.js','/portal.css'):
            names={'/':('portal.html','text/html; charset=utf-8'),
                '/portal.js':('portal.js','text/javascript; charset=utf-8'),
                '/portal.css':('portal.css','text/css; charset=utf-8')}
            file,type_=names[route]
            return self.send_static(STATIC/file,type_)
        if route=='/api/public/health':
            return self.send_json(200,{'name':'Paper-Radar Public','version':__version__})
        user=self.session()
        if not user:return self.send_json(401,{'error':'请先登录'})
        if route=='/api/public/me':
            return self.send_json(200,{'user':{k:user[k] for k in ('id','username','role')},'csrf':user['csrf']})
        if route=='/api/public/jobs':
            return self.send_json(200,self.server.portal.job(user['id']))
        if route=='/api/public/jobs/history':
            q=parse_qs(urlsplit(self.path).query)
            try:limit=int((q.get('limit') or ['10'])[0])
            except (ValueError,TypeError):raise PortalError('Invalid history limit')
            return self.send_json(200,{'jobs':self.server.portal.job_history(user['id'],limit)})
        if route=='/api/public/jobs/detail':
            q=parse_qs(urlsplit(self.path).query)
            try:job_id=int((q.get('id') or ['0'])[0])
            except (ValueError,TypeError):raise PortalError('任务编号不正确')
            if job_id<1:raise PortalError('任务编号不正确')
            record=self.server.portal.job_detail(user['id'],job_id)
            if not record:return self.send_json(404,{'error':'任务不存在或无权查看'})
            return self.send_json(200,record)
        if route=='/api/public/reports':
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            return self.send_json(200,{'reports':portal_reports.list_reports(self.server.portal,user['id'],topic)})
        if route=='/api/public/reports/view':
            q=parse_qs(urlsplit(self.path).query)
            report_id=(q.get('id') or [''])[0]
            record=portal_reports.read_report(self.server.portal,user['id'],report_id)
            if not record:return self.send_json(404,{'error':'报告不存在或无权查看'})
            return self.send_json(200,record)
        if route=='/api/public/topics':
            with self.server.portal.workspace(user['id']) as db:
                rows=db.execute('SELECT id,name,keywords,created_at FROM topics ORDER BY created_at').fetchall()
            return self.send_json(200,{'topics':[{'id':r['id'],'name':r['name'],
                'keywords':json.loads(r['keywords'])} for r in rows]})
        if route=='/api/public/dashboard':
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            self.server.portal.available(user['id'],topic)
            with self.server.portal.workspace(user['id']) as db:
                counts=db.execute('''SELECT COUNT(*) AS papers,
                    COALESCE(SUM(CASE WHEN f.is_starred=1 THEN 1 ELSE 0 END),0) AS starred,
                    COALESCE(SUM(CASE WHEN f.reading_status='done' THEN 1 ELSE 0 END),0) AS read
                    FROM topic_papers tp LEFT JOIN topic_paper_flags f
                    ON f.topic_id=tp.topic_id AND f.paper_id=tp.paper_id
                    WHERE tp.topic_id=?''',(topic,)).fetchone()
                reports=db.execute('SELECT COUNT(*) FROM research_reports WHERE topic_id=?',(topic,)).fetchone()[0]
                latest=db.execute('''SELECT p.id,p.title,p.published
                    FROM papers p JOIN topic_papers tp ON tp.paper_id=p.id
                    WHERE tp.topic_id=? ORDER BY p.published DESC,p.id DESC LIMIT 3''',(topic,)).fetchall()
            return self.send_json(200,{'papers':counts['papers'],'starred':counts['starred'],
                'read':counts['read'],'reports':reports,'recent':[dict(r) for r in latest]})
        if route=='/api/public/papers':
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            self.server.portal.available(user['id'],topic)
            try:
                limit=int((q.get('limit') or ['30'])[0])
                offset=int((q.get('offset') or ['0'])[0])
            except (ValueError,TypeError):raise PortalError('Invalid page parameters')
            if not 1<=limit<=100 or not 0<=offset<=5000:
                raise PortalError('Page limit or offset out of range')
            search=(q.get('q') or [''])[0].strip()
            filter_=(q.get('filter') or ['all'])[0]
            sort=(q.get('sort') or ['recent'])[0]
            if len(search)>120 or filter_ not in ('all','starred','unread','reading','done','liked') \
                    or sort not in ('recent','oldest','title'):
                raise PortalError('Invalid search or filter')
            where=['tp.topic_id=?'];params=[topic]
            if search:
                pattern='%'+search.lower().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
                where.append('''(LOWER(p.title) LIKE ? ESCAPE '\\' OR
                    LOWER(p.abstract) LIKE ? ESCAPE '\\' OR
                    LOWER(p.doi) LIKE ? ESCAPE '\\')''')
                params.extend((pattern,)*3)
            if filter_=='starred':where.append('COALESCE(f.is_starred,0)=1')
            if filter_ in ('unread','reading','done'):
                where.append("COALESCE(f.reading_status,'unread')=?");params.append(filter_)
            if filter_=='liked':where.append('COALESCE(f.feedback,0)=1')
            order={'recent':'p.published DESC,p.id DESC',
                   'oldest':'p.published ASC,p.id ASC','title':'p.title COLLATE NOCASE ASC,p.id'}[sort]
            relation='''FROM papers p JOIN topic_papers tp ON tp.paper_id=p.id
                LEFT JOIN topic_paper_flags f ON f.paper_id=p.id AND f.topic_id=tp.topic_id'''
            criterion=' WHERE '+' AND '.join(where)
            with self.server.portal.workspace(user['id']) as db:
                total=db.execute('SELECT COUNT(*) '+relation+criterion,params).fetchone()[0]
                rows=db.execute('''SELECT p.id,p.title,p.abstract,p.published,p.doi,p.url,
                    COALESCE(f.is_starred,0) AS is_starred,
                    COALESCE(f.reading_status,'unread') AS reading_status,
                    COALESCE(f.feedback,0) AS feedback '''+relation+criterion+' ORDER BY '+order+
                    ' LIMIT ? OFFSET ?',(*params,limit,offset)).fetchall()
            return self.send_json(200,{'papers':[dict(r) for r in rows],
                'total':total,'limit':limit,'offset':offset})
        if route=='/api/public/pdf/view':
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            pid=(q.get('paper_id') or [''])[0]
            return self.send_user_pdf(user['id'],topic,pid)
        if route=='/api/public/fulltext':
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            pid=(q.get('paper_id') or [''])[0]
            try:page=int((q.get('page') or ['1'])[0])
            except ValueError:raise PortalError('Invalid page')
            if not 1<=page<=40:raise PortalError('Page limit 1-40')
            return self.send_json(200,portal_research.preview(self.server.portal,user['id'],topic,pid,page))
        if route=='/api/public/research/report':
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            return self.send_json(200,portal_research.synthesis(self.server.portal,user['id'],topic))
        if route=='/api/public/synthesis':
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            _,info=self.server.portal.available(user['id'],topic)
            return self.send_json(200,portal_research.synthesis(self.server.portal,user['id'],topic))
        if route=='/api/public/admin/jobs':
            if user['role']!='admin':return self.send_json(403,{'error':'需要管理员权限'})
            return self.send_json(200,self.server.portal.job_queue.stats())
        if route=='/api/public/admin/accounts':
            if user['role']!='admin':return self.send_json(403,{'error':'需要管理员权限'})
            with auth_db(self.server.portal.auth_path) as db:
                rows=db.execute('SELECT id,username,role,is_active,created_at FROM accounts ORDER BY created_at DESC LIMIT 500').fetchall()
            return self.send_json(200,{'accounts':[dict(r) for r in rows]})
        return self.send_json(404,{'error':'No public endpoint'})

    def do_POST(self):
        if not self.guard_host():return self.send_json(403,{'error':'Host mismatch'})
        if self.headers.get('Origin','')!=self.server.portal.origin:
            return self.send_json(403,{'error':'Origin mismatch'})
        if self.headers.get('X-Portal-Action','')!='1':
            return self.send_json(403,{'error':'Missing anti-CSRF action header'})
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self.send_json(415,{'error':'JSON required'})
        try:
            if self.headers.get('Transfer-Encoding'):
                return self.send_json(400,{'error':'Chunked transfer not accepted'})
            size=int(self.headers.get('Content-Length','0'))
            if not 0<=size<=MAX_POST:return self.send_json(413,{'error':'Request too large'})
            payload=_json(self.rfile.read(size))
            route=urlsplit(self.path).path
            if route in ('/api/public/register','/api/public/login'):
                username=payload.get('username','')
                password=payload.get('password','')
                if route.endswith('/register'):
                    if not self.server.portal.allow_registration:
                        return self.send_json(403,{'error':'已关闭自主注册'})
                    with auth_db(self.server.portal.auth_path) as db:
                        key='signup:'+self.client_ip()
                        if not throttle(db,key,limit=8,seconds=1800):
                            return self.send_json(429,{'error':'注册过于频繁，请稍后重试'})
                        if db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]>=1000:
                            return self.send_json(429,{'error':'已达到当前服务账号上限'})
                        new=create_account(db,username,password)
                        token,csrf=create_session(db,new['id'])
                        with self.server.portal.workspace(new['id']):pass
                    return self.send_json(201,{'user':new,'csrf':csrf},cookie=self.session_cookie(token))
                with auth_db(self.server.portal.auth_path) as db:
                    if not throttle(db,'login_ip:'+self.client_ip(),limit=120,seconds=900):
                        return self.send_json(429,{'error':'登录流量达到上限，请稍后重试'})
                    key='login:'+sha256(str(username).casefold().encode()).hexdigest()
                    if not throttle(db,key,limit=10,seconds=900):
                        return self.send_json(429,{'error':'登录尝试过于频繁，请稍后重试'})
                    user=check_login(db,username,password)
                    if not user:return self.send_json(401,{'error':'用户名或密码不正确'})
                    token,csrf=create_session(db,user['id'])
                return self.send_json(200,{'user':user,'csrf':csrf},cookie=self.session_cookie(token))
            user=self.session()
            if not user:return self.send_json(401,{'error':'请先登录'})
            if self.headers.get('X-CSRF-Token','')!=user['csrf']:
                return self.send_json(403,{'error':'CSRF token invalid'})
            if route=='/api/public/logout':
                with auth_db(self.server.portal.auth_path) as db:delete_session(db,self.cookie_value())
                return self.send_json(200,{'ok':True},cookie=self.session_cookie('',clear=True))
            if route=='/api/public/password':
                with auth_db(self.server.portal.auth_path) as db:
                    change_password(db,user['id'],payload.get('old_password',''),payload.get('new_password',''))
                return self.send_json(200,{'ok':True},cookie=self.session_cookie('',clear=True))
            if route=='/api/public/topics':
                name=str(payload.get('name','')).strip()[:80]
                words=payload.get('keywords')
                if not name or not isinstance(words,list) or not 1<=len(words)<=6:
                    raise PortalError('请输入主题名称及 1—6 组关键词')
                keywords=[str(word).strip()[:80] for word in words if str(word).strip()]
                if not keywords:raise PortalError('至少需要一组关键词')
                uid=uuid.uuid4().hex[:16]
                with self.server.portal.workspace(user['id']) as db:
                    if db.execute('SELECT COUNT(*) FROM topics').fetchone()[0]>=30:
                        raise PortalError('主题数量上限为 30')
                    db.execute('INSERT INTO topics VALUES (?,?,?,?)',
                               (uid,name,json.dumps(keywords,ensure_ascii=False),utc_now()))
                return self.send_json(201,{'id':uid,'name':name,'keywords':keywords})
            if route=='/api/public/papers/flags':
                topic=payload.get('topic_id','default')
                pid=payload.get('paper_id')
                if not isinstance(topic,str) or not isinstance(pid,str) or not 1<=len(pid)<=200:
                    raise PortalError('Invalid paper identifier')
                self.server.portal.available(user['id'],topic)
                allowed={'is_starred','reading_status','feedback'}
                changes={k:v for k,v in payload.items() if k in allowed}
                if not changes or set(payload)-({'topic_id','paper_id'}|allowed):
                    raise PortalError('No valid reading status change')
                if 'is_starred' in changes and type(changes['is_starred']) is not bool:
                    raise PortalError('Star flag must be boolean')
                if 'reading_status' in changes and changes['reading_status'] not in ('unread','reading','done'):
                    raise PortalError('Invalid reading status')
                if 'feedback' in changes and (type(changes['feedback']) is not int or changes['feedback'] not in (-1,0,1)):
                    raise PortalError('Invalid relevance feedback')
                with self.server.portal.workspace(user['id']) as db:
                    present=db.execute('SELECT 1 FROM topic_papers WHERE topic_id=? AND paper_id=?',
                                       (topic,pid)).fetchone()
                    if not present:return self.send_json(404,{'error':'论文不存在或无权访问'})
                    before=db.execute('''SELECT is_starred,reading_status,feedback
                        FROM topic_paper_flags WHERE topic_id=? AND paper_id=?''',(topic,pid)).fetchone()
                    result={'is_starred':bool(before['is_starred']) if before else False,
                        'reading_status':before['reading_status'] if before else 'unread',
                        'feedback':before['feedback'] if before else 0}
                    result.update(changes)
                    db.execute('''INSERT INTO topic_paper_flags
                        (topic_id,paper_id,is_starred,reading_status,feedback,updated_at)
                        VALUES (?,?,?,?,?,?) ON CONFLICT(topic_id,paper_id) DO UPDATE SET
                        is_starred=excluded.is_starred,reading_status=excluded.reading_status,
                        feedback=excluded.feedback,updated_at=excluded.updated_at''',
                        (topic,pid,int(result['is_starred']),result['reading_status'],result['feedback'],utc_now()))
                return self.send_json(200,{'paper_id':pid,'topic_id':topic,**result})
            if route=='/api/public/search':
                topic=str(payload.get('topic_id','default'))
                self.server.portal.available(user['id'],topic)
                try:job=self.server.portal.search_async(user['id'],topic)
                except PortalError as exc:
                    return self.send_json(429,{'error':str(exc)})
                return self.send_json(202,{'ok':True,'state':'queued','job_id':job['id']})
            if route in ('/api/public/research/start','/api/public/research/analyze','/api/public/research/synthesize'):
                topic=str(payload.get('topic_id','default'))
                kind={'/api/public/research/start':'research','/api/public/research/analyze':'analyze',
                      '/api/public/research/synthesize':'synthesize'}[route]
                options={'cloud_analysis':payload.get('cloud_analysis',False),
                         'fetch_pdf':payload.get('fetch_pdf',True)}
                if any(type(v) is not bool for v in options.values()):raise PortalError('Invalid options')
                pid=str(payload.get('paper_id','')) if kind=='analyze' else ''
                try:job=self.server.portal.research_async(user['id'],topic,kind=kind,pid=pid,options=options)
                except (PortalError,QueueError) as exc:return self.send_json(429,{'error':str(exc)})
                return self.send_json(202,{'job_id':job['id'],'kind':kind,'state':'queued'})
            if route=='/api/public/jobs/cancel':
                job_id=payload.get('job_id')
                if job_id is not None and (type(job_id) is not int or job_id<1):
                    raise PortalError('Invalid job id')
                return self.send_json(200,{'cancel_requested':self.server.portal.cancel_job(user['id'],job_id)})
            if route=='/api/public/import':
                topic=str(payload.get('topic_id','default'))
                self.server.portal.available(user['id'],topic)
                text=payload.get('csv')
                if not isinstance(text,str) or not 0<len(text.encode('utf-8'))<250_000:
                    raise PortalError('CSV 内容为空或超出 250KB 上限')
                rows=list(csv.DictReader(StringIO(text.lstrip('\ufeff'))))
                if len(rows)>500:raise PortalError('单次最多导入 500 条证据')
                required={'论文ID','论文标题','评价指标','原文数值','PDF页码','原文引文'}
                if not rows or not required.issubset(rows[0]):
                    raise PortalError('CSV 必须包含原版 v1.14 实验证据索引列')
                count=0
                with self.server.portal.workspace(user['id']) as db:
                    for row in rows:
                        pid=str(row['论文ID'] or '').strip()[:150]
                        title=str(row['论文标题'] or '').strip()[:400]
                        quote=str(row['原文引文'] or '').strip()[:1200]
                        metric=str(row['评价指标'] or '').strip()[:120]
                        value=str(row['原文数值'] or '').strip()[:80]
                        try:page=int(row['PDF页码'])
                        except (ValueError,TypeError):continue
                        if not all((pid,title,metric,value,quote)) or not 1<=page<=9999 or len(quote)<18:
                            continue
                        db.execute('INSERT OR IGNORE INTO papers VALUES (?,?,?,?,?,?,?)',(
                            pid,title,'','',str(row.get('论文链接') or '')[:600],
                            str(row.get('DOI') or '')[:200],utc_now()))
                        db.execute('INSERT OR IGNORE INTO topic_papers VALUES (?,?)',(topic,pid))
                        fields=(pid,str(row.get('类型') or '')[:20],str(row.get('数据集') or '')[:120],
                            metric,value,str(row.get('对照基线') or '')[:120],
                            str(row.get('实验条件') or '')[:220],page,quote)
                        signature=sha256(json.dumps(fields,ensure_ascii=False).encode()).hexdigest()
                        changed=db.execute('''INSERT OR IGNORE INTO evidence(id,paper_id,kind,dataset,metric,value,baseline,setting,page,quote,provenance,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (signature,*fields,'user_import_unverified',utc_now())).rowcount
                        count+=changed
                return self.send_json(200,{'imported':count,'input_rows':len(rows),
                    'warning':'导入内容尚未由公网服务器对 PDF 原文核验'})
            if route=='/api/public/admin/account':
                if user['role']!='admin':return self.send_json(403,{'error':'需要管理员权限'})
                target=str(payload.get('id') or '')
                active=payload.get('active')
                if not UID.fullmatch(target) or type(active) is not bool:
                    raise PortalError('Invalid account change')
                with auth_db(self.server.portal.auth_path) as db:
                    set_active(db,user['id'],target,active)
                if not active:
                    self.server.portal.job_queue.cancel_account(target)
                return self.send_json(200,{'ok':True})
            return self.send_json(404,{'error':'No public endpoint'})
        except (PortalError,AuthError,ValueError,KeyError,TypeError,UnicodeError,json.JSONDecodeError) as exc:
            self.send_json(400,{'error':str(exc)[:180]})
        except Exception:
            self.send_json(500,{'error':'服务器处理失败，请联系管理员查看服务器日志'})


    def do_PUT(self):
        if not self.guard_host():return self.send_json(403,{'error':'Host mismatch'})
        if urlsplit(self.path).path!='/api/public/pdf':return self.send_json(404,{'error':'Unknown API'})
        if self.headers.get('Origin','')!=self.server.portal.origin or self.headers.get('X-Portal-Action','')!='1':
            return self.send_json(403,{'error':'Origin mismatch'})
        user=self.session()
        if not user:return self.send_json(401,{'error':'Please log in'})
        if self.headers.get('X-CSRF-Token','')!=user['csrf']:return self.send_json(403,{'error':'CSRF mismatch'})
        if self.headers.get('Content-Type','').split(';')[0]!='application/pdf':return self.send_json(415,{'error':'PDF only'})
        try:
            if self.headers.get('Transfer-Encoding'):return self.send_json(400,{'error':'Chunked uploads disabled'})
            count=int(self.headers.get('Content-Length','0'))
            if not 256<=count<=portal_research.MAX_PDF:return self.send_json(413,{'error':'Upload exceeds limit'})
            topic=self.headers.get('X-Topic-ID','default')
            pid=self.headers.get('X-Paper-ID','')
            if not pid or len(pid)>150:raise PortalError('Invalid ID')
            from .portal_auth import db as adb, throttle
            with adb(self.server.portal.auth_path) as db:
                if not throttle(db,'pdf:'+user['id'],limit=12,seconds=86400):return self.send_json(429,{'error':'Upload limit'})
            return self.send_json(201,portal_research.save_pdf(self.server.portal,user['id'],topic,pid,self.rfile.read(count)))
        except (ValueError,PortalError,OSError) as exc:return self.send_json(400,{'error':str(exc)[:160]})


class PortalHTTPServer(ThreadingHTTPServer):
    # Prevent unbounded HTTP threads when exposed through a busy reverse proxy.
    connection_slots=BoundedSemaphore(32)

    def process_request(self,request,client_address):
        if not self.connection_slots.acquire(blocking=False):
            try:request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:pass
            self.shutdown_request(request)
            return
        try:super().process_request(request,client_address)
        except Exception:
            self.connection_slots.release()
            raise

    def process_request_thread(self,request,client_address):
        try:super().process_request_thread(request,client_address)
        finally:self.connection_slots.release()

    def server_close(self):
        if self.portal.embedded_workers:
            self.portal.job_queue.stop()
        super().server_close()
        lock=getattr(self,'_single_lock',None)
        if lock:
            try:
                if os.name=='nt':
                    import msvcrt
                    lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
            finally:lock.close()


def make_server(host:str,port:int,portal:Portal):
    if not 1<=port<=65535 and port!=0:raise PortalError('Invalid port')
    if not portal.origin:
        portal.origin=f'http://127.0.0.1:{port}'
    parts=urlsplit(portal.origin)
    if parts.scheme not in ('http','https') or not parts.netloc or parts.path not in ('','/') or parts.query or parts.fragment:
        raise PortalError('Invalid public origin')
    if parts.scheme!='https' and parts.hostname not in ('127.0.0.1','localhost'):
        raise PortalError('公网服务必须通过 HTTPS 反向代理访问')
    if host not in ('127.0.0.1','::1','localhost'):
        raise PortalError('公网服务必须绑定本机回环接口，由 Nginx HTTPS 反向代理提供公网访问')
    lock=(portal.root/'.single_portal.lock').open('a+b')
    try:
        if os.name=='nt':
            import msvcrt
            if not (portal.root/'.single_portal.lock').stat().st_size:lock.write(b'0');lock.flush()
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except (OSError,BlockingIOError) as exc:
        lock.close();raise PortalError('Public service already running') from exc
    try:server=PortalHTTPServer((host,port),Handler)
    except Exception:lock.close();raise
    server._single_lock=lock
    server.daemon_threads=True
    server.portal=portal
    if port==0 and portal.origin=='http://127.0.0.1:0':
        portal.origin=f'http://127.0.0.1:{server.server_port}'
    if portal.embedded_workers:
        portal.job_queue.start()
    return server


def cli(argv=None):
    parser=argparse.ArgumentParser(description='Paper-Radar isolated public research portal')
    sub=parser.add_subparsers(dest='command')
    admin=sub.add_parser('admin-create');admin.add_argument('--username',required=True)
    run=sub.add_parser('serve');run.add_argument('--host',default='127.0.0.1');run.add_argument('--port',type=int,default=8899)
    run.add_argument('--origin',default=os.environ.get('PAPERRADAR_PUBLIC_ORIGIN',''))
    run.add_argument('--disable-registration',action='store_true')
    run.add_argument('--embedded-workers',action='store_true',
                     help='Development only. Production uses separate portal_worker service.')
    run.add_argument('--trust-proxy-ip',action='store_true',
                     help='Only behind a trusted Nginx that overwrites X-Real-IP')
    run.add_argument('--workers',type=int,default=int(os.getenv('PAPERRADAR_JOB_WORKERS','2')),
                     help='Background search workers, 1-4 (default 2)')
    run.add_argument('--max-pending',type=int,default=int(os.getenv('PAPERRADAR_MAX_PENDING_JOBS','100')))
    run.add_argument('--daily-search-limit',type=int,
                     default=int(os.getenv('PAPERRADAR_DAILY_SEARCH_LIMIT','10')))
    parser.add_argument('--data-root',type=Path,default=Path(os.environ.get('PAPERRADAR_PORTAL_DATA',ROOT/'storage'/'portal')))
    args=parser.parse_args(argv)
    if args.command=='admin-create':
        from getpass import getpass
        portal=Portal(args.data_root)
        value=getpass('设置管理员密码（至少 12 位）：')
        with auth_db(portal.auth_path) as db:
            user=create_account(db,args.username,value,role='admin')
        print('管理员已建立：'+user['username'])
        return 0
    if args.command=='serve':
        if not 1<=args.workers<=4 or not 1<=args.max_pending<=1000 or not 1<=args.daily_search_limit<=100:
            raise SystemExit('Invalid job settings: workers 1-4, max-pending 1-1000, daily-search-limit 1-100')
        portal=Portal(args.data_root,origin=args.origin,allow_registration=not args.disable_registration,
                      trust_proxy_ip=args.trust_proxy_ip or os.getenv('PAPERRADAR_TRUST_PROXY_IP')=='1',
                      job_workers=args.workers,max_pending_jobs=args.max_pending,
                      daily_search_limit=args.daily_search_limit,
                      embedded_workers=args.embedded_workers)
        with make_server(args.host,args.port,portal) as server:
            marker=ROOT/'.paper_radar_updates'/'public_service.active'
            marker.parent.mkdir(parents=True,exist_ok=True)
            try:
                fd=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            except FileExistsError:
                raise SystemExit('已有公网服务锁，检查服务是否正在运行；确认已停止后可手动清理锁文件。')
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as out:
                    out.write(json.dumps({'pid':os.getpid(),'port':server.server_port,
                        'at':utc_now()})+'\n')
                print(f'Paper-Radar Public v{__version__} serving on {args.host}:{server.server_port}',flush=True)
                print('Public origin:',portal.origin,flush=True)
                server.serve_forever(poll_interval=.5)
            finally:marker.unlink(missing_ok=True)
        return 0
    parser.print_help();return 2


if __name__=='__main__':raise SystemExit(cli())
