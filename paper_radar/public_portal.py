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
import logging
import traceback
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
from .migrations import migrate, REGISTRIES
from .synthesis import compare_records,to_markdown
from .portal_jobs import PortalJobs, QueueError, JobCancelled
from . import portal_research, portal_reports
from .portal_metadata import merge_arxiv_paper
from .portal_discovery import (topic_sources, set_topic_sources, validate_sources,
                               discover, ALLOWED_SOURCES)

ROOT=Path(__file__).resolve().parent.parent
STATIC=Path(__file__).resolve().parent/'static'
MAX_POST=400_000
TOPIC_ID=re.compile(r'^[a-z0-9_-]{1,48}$')
UID=re.compile(r'^[0-9a-f]{32}$')


class PortalError(ValueError):
    def __init__(self,message,source_results=None):
        super().__init__(message)
        self.source_results=source_results



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
        # Migration is done once per workspace/file identity in each process.
        # Different Worker processes still verify checksums on first access.
        self._workspace_init_lock=Lock()
        self._workspace_initialized={}
        self.source_timeout_seconds=max(2,min(90,int(os.getenv('PAPERRADAR_PUBLIC_SOURCE_TIMEOUT','40'))))
        self.model_callable=None
        self.pdf_downloader=portal_research.fetch_arxiv_pdf
        self.job_queue=PortalJobs(self.auth_path,self.search,
            max_workers=job_workers,max_pending=max_pending_jobs,daily_limit=daily_search_limit,special_worker=self.research_job)

    def audit(self,route:str,method:str,status:int,*,request_id:str=''):
        """Log event types, never bearer tokens, passwords, IP or paper content."""
        if status<400 and method=='GET':return
        payload={'at':utc_now(),'route':route[:90],'method':method,'status':status}
        if status>=500 and request_id:
            payload['request_id']=request_id[:32]
            payload['error_code']='E_INTERNAL'
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
        # Avoid BEGIN IMMEDIATE on every report/task poll. Lock first-open
        # initialization in-process; SQLite migrations still serialize across
        # processes and reject schema history tampering on first access.
        with self._workspace_init_lock:
            st=p.stat()
            identity=(st.st_dev,st.st_ino,len(REGISTRIES['portal_workspace']),
                      REGISTRIES['portal_workspace'][-1].checksum)
            if self._workspace_initialized.get(uid)!=identity:
                migrate(conn,'portal_workspace')
                conn.execute("INSERT OR IGNORE INTO topics VALUES ('default',?,?,?)",
                             ('默认研究主题',json.dumps(['neural operator','operator learning']),utc_now()))
                conn.commit()
                self._workspace_initialized[uid]=identity
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
        from .following import plan_windows,watched_arxiv_ids
        cfg=dict(DEFAULTS)
        cfg.update(keywords=keywords[:6],categories=[],max_results=20,lookback_days=14,
                   active_topic_id=topic_id)
        with self.workspace(uid) as db:
            sources=topic_sources(db,topic_id)
            cfg['sources']=sources
            cfg['_following_windows']=plan_windows(db,cfg)
            if 'arxiv' in cfg['_following_windows']:
                cfg['_watch_arxiv_ids']=watched_arxiv_ids(db,topic_id)
        if self.fetcher:
            # Tests and explicitly supplied fetcher hooks remain deterministic.
            started=time.monotonic()
            papers=self.fetcher(cfg,logger=lambda _:None)
            source_results=[{'name':'arxiv','ok':True,'partial':False,
                             'count':len(papers) if isinstance(papers,list) else 0,
                             'timed_out':False,'seconds':round(time.monotonic()-started,2),
                             'mode':'injected_test_fetcher',
                             'queries':[{'ok':True,'count':len(papers),
                                         'saturated':len(papers)>=20}]}]
        else:
            # Each approved source is isolated, bounded, and diagnosed. One
            # unavailable provider does not discard other completed results.
            try:
                papers,source_results=discover(self,cfg,sources,should_cancel)
            except PortalError as exc:
                if exc.source_results and cfg.get('_following_windows'):
                    from .following import record_cycle
                    with self.workspace(uid) as db:
                        record_cycle(db,cfg,[],[],exc.source_results)
                raise
        if not isinstance(papers,list):raise PortalError('检索来源返回数据无效')
        if should_cancel and should_cancel():raise JobCancelled('Task cancelled')
        inserted=updated=stale=0
        with self.workspace(uid) as db:
            imported_before={row[0] for row in db.execute(
                'SELECT paper_id FROM topic_papers WHERE topic_id=?',(topic_id,))}
            for paper in papers[:40]:
                if should_cancel and should_cancel():raise JobCancelled('Task cancelled')
                pid=str(paper.paper_id or '')[:150]
                if not pid or not str(paper.title or '').strip():continue
                doi=str(getattr(paper,'doi','') or '').strip().lower()
                duplicate=db.execute("SELECT id FROM papers WHERE LOWER(doi)=? AND doi<>'' LIMIT 1",
                                     (doi,)).fetchone() if doi else None
                if duplicate and duplicate['id']!=pid:
                    # Preserve the original record and user reading flags.
                    pid=duplicate['id']
                else:
                    outcome=merge_arxiv_paper(db,paper,utc_now())
                    inserted+=outcome=='inserted'
                    updated+=outcome=='updated'
                    stale+=outcome=='stale'
                db.execute('INSERT OR IGNORE INTO topic_papers VALUES (?,?)',(topic_id,pid))
            if should_cancel and should_cancel():raise JobCancelled('Task cancelled')
            from .following import record_cycle
            if cfg.get('_following_windows'):
                current_ids={row[0] for row in db.execute(
                    'SELECT paper_id FROM topic_papers WHERE topic_id=?',(topic_id,))}
                # A new event is emitted only for a first-time topic import;
                # already-present papers are tracked for later revisions.
                previous_ids=current_ids-set(imported_before)
                follow=record_cycle(db,cfg,papers,papers,source_results,
                    new_to_topic=previous_ids)
            else:follow={'enabled':False,'events':0,'pending_sources':0}
            db.commit()
        # A periodic briefing is optional and runs after successful source
        # persistence. It never calls a cloud model or reclassifies an event.
        brief={'state':'disabled'}
        try:
            from .research_continuity import automatic_due
            from .evidence_chain import EvidenceChain
            with self.workspace(uid) as db:
                chain=EvidenceChain(db,topic_id,backend='portal',portal=self,uid=uid)
                with db:brief=automatic_due(db,topic_id,chain,backend='portal')
        except (OSError,ValueError,RuntimeError,sqlite3.DatabaseError):
            brief={'state':'error'}
        return {'retrieved':len(papers),'new':inserted,'updated':updated,'stale_skipped':stale,
                'research_period_brief':{'state':brief.get('state')},'following':follow,
                'topic_id':topic_id,'source_results':source_results,
                'state':'partial' if follow.get('pending_sources') or any(not row['ok'] or row.get('degraded') for row in source_results) else 'complete'}

    def _fetch_public_arxiv(self,cfg,checkpoint):
        """Run official arXiv query in an isolated process with a hard deadline."""
        from .sources.supervisor import fetch_isolated, SourceTimeout
        from .source_cache import SourceCache, SourceResponse
        cache=SourceCache(self.root)
        cached=cache.get('arxiv',cfg)
        if cached is not None:
            return cached,[{'name':'arxiv','ok':True,'partial':False,'count':len(cached),
                'timed_out':False,'seconds':0.0,'mode':'cache','cache_state':'hit',
                'cache_age_seconds':cached.cache_age_seconds}]
        # The source serial lock is already acquired by the caller. Limit the
        # rate gate wait as well as the actual source sub-process lifetime.
        for _ in range(80):
            checkpoint()
            with auth_db(self.auth_path) as adb:
                allowed=throttle(adb,'source:arxiv_public',limit=1,seconds=4)
            if allowed:break
            time.sleep(.25)
        else:raise PortalError('arXiv 公共接口仍处于访问间隔，请稍后重试')
        started=time.monotonic()
        try:
            papers=fetch_isolated('arxiv',cfg,timeout_seconds=self.source_timeout_seconds,
                                  checkpoint=checkpoint,public_metadata=True)
        except JobCancelled:
            raise
        except (SourceTimeout,RuntimeError,ValueError,OSError) as exc:
            fallback=cache.get('arxiv',cfg,allow_stale=True)
            if fallback is not None and fallback.cache_state=='stale':
                return fallback,[{'name':'arxiv','ok':True,'partial':True,
                    'count':len(fallback),'mode':'cache','cache_state':'stale',
                    'cache_age_seconds':fallback.cache_age_seconds}]
            from .sources.failures import SourceQueryError
            if isinstance(exc,SourceQueryError):
                raise  # retain the exact safe network/rate-limit category
            if isinstance(exc,SourceTimeout):
                raise PortalError(f'arXiv 来源连接超时（最多 {self.source_timeout_seconds} 秒），已停止请求；请稍后重试') from exc
            raise PortalError('arXiv 来源不可用或拒绝访问，已结束本次检索；请检查网络及来源状态') from exc
        finally:
            # The next worker starts >= 4 s after the last request finishes.
            with auth_db(self.auth_path) as adb:
                adb.execute("DELETE FROM auth_attempts WHERE identifier='source:arxiv_public'")
                adb.execute('INSERT INTO auth_attempts(identifier,occurred_at) VALUES (?,?)',
                            ('source:arxiv_public',int(time.time())))
        if not getattr(papers,'partial',False): cache.put('arxiv',cfg,papers)
        papers=SourceResponse(papers,cache_state='miss',
            partial=getattr(papers,'partial',False),
            warning_code=getattr(papers,'warning_code',''))
        return papers,[{'name':'arxiv','ok':True,'partial':papers.partial,'count':len(papers),
                        'timed_out':False,'seconds':round(time.monotonic()-started,2),
                        'mode':'isolated_process','cache_state':'miss'}]

    def _fetch_public_source(self,name:str,cfg:dict,*,should_cancel=None):
        """Run a fixed public adapter with its own cross-worker rate gate."""
        if name not in ('openalex','crossref','openreview'):
            raise PortalError('来源未授权')
        from .sources.supervisor import fetch_isolated
        from .portal_resource_locks import exclusive_file_lock
        from .source_cache import SourceCache, SourceResponse
        cache=SourceCache(self.root)
        def checkpoint():
            if should_cancel and should_cancel():
                raise JobCancelled('文献检索已取消')
        try:
            with exclusive_file_lock(self.root/('.source_'+name+'.lock'),
                                     cancelled=should_cancel,timeout=35):
                checkpoint()
                cached=cache.get(name,cfg)
                if cached is not None:return cached
                for _ in range(100):
                    checkpoint()
                    with auth_db(self.auth_path) as adb:
                        allowed=throttle(adb,'source:'+name+'_public',limit=1,seconds=3)
                    if allowed:break
                    time.sleep(.15)
                else:raise PortalError('来源请求过于频繁')
                try:
                    try:
                        papers=fetch_isolated(name,cfg,timeout_seconds=self.source_timeout_seconds,
                                              checkpoint=checkpoint,public_metadata=True)
                    except (RuntimeError,OSError,TimeoutError):
                        # Stale results are marked degraded, never fresh.
                        fallback=cache.get(name,cfg,allow_stale=True)
                        if fallback is not None and fallback.cache_state=='stale':
                            return fallback
                        raise
                    if not getattr(papers,'partial',False): cache.put(name,cfg,papers)
                    return SourceResponse(papers,cache_state='miss',
                        partial=getattr(papers,'partial',False),
                        warning_code=getattr(papers,'warning_code',''))
                finally:
                    with auth_db(self.auth_path) as adb:
                        adb.execute('DELETE FROM auth_attempts WHERE identifier=?',
                                    ('source:'+name+'_public',))
                        adb.execute('INSERT INTO auth_attempts(identifier,occurred_at) VALUES (?,?)',
                                    ('source:'+name+'_public',int(time.time())))
        except InterruptedError as exc:
            raise JobCancelled('文献检索已取消') from exc

    def research_job(self,uid,topic,kind,pid,options,cancel,progress,job_id=None):
        self.available(uid,topic)
        cloud=options.get('cloud_analysis',False)
        if kind=='analyze':
            progress('fulltext')
            try:
                result=portal_research.analyze(self,uid,topic,pid,cloud=cloud,cancel=cancel)
                if cloud:progress('evidence')
                return {'kind':kind,'state':'partial' if result.get('analysis_state')=='partial' else 'complete',
                        'analysis':result}
            except InterruptedError:raise JobCancelled('Cancelled')
        if kind=='synthesize':
            progress('synthesis')
            report=portal_reports.create_report(self,uid,topic,job_id=job_id)
            return {'kind':kind,'finding_count':report['finding_count'],
                    'paper_count':report['paper_count'],'library_paper_count':report.get('library_paper_count',0),'report_id':report['id']}
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
        partial=(found.get('state')=='partial' or not results or completed!=len(results)
                 or (cloud and any(x.get('cloud_analysis') not in ('verified','cached','no_supported_findings') for x in results)))
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
        self.server.portal.audit(urlsplit(self.path).path,self.command,status,
                                 request_id=str(data.get('request_id','')) if status>=500 else '')
        raw=json.dumps(data,ensure_ascii=False,default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.security_headers()
        if cookie:self.send_header('Set-Cookie',cookie)
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def internal_error(self,exc:Exception):
        # Request identifiers connect the response to a *redacted* audit entry.
        # Do not log exception text, arguments, cookies, IP addresses or PDFs.
        request_id=secrets.token_hex(8)
        frames=traceback.extract_tb(exc.__traceback__)
        where=(Path(frames[-1].filename).name+':'+str(frames[-1].lineno)) if frames else 'unknown'
        logging.getLogger('paper_radar.public').error(
            'E_INTERNAL request_id=%s method=%s route=%r class=%s location=%s',
            request_id,self.command,urlsplit(self.path).path[:90],type(exc).__name__,where)
        return self.send_json(500,{'error':'服务器处理失败，请提供错误编号联系管理员',
                                   'request_id':request_id,'error_code':'E_INTERNAL'})

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
            # Fail closed: treating a missing trusted header as loopback would
            # merge all users into one rate-limit bucket in production.
            forwarded=self.headers.get('X-Real-IP','').strip()
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError as exc:
                raise PortalError('反向代理未提供有效的客户端地址，请检查 Nginx X-Real-IP 配置') from exc
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

    def finish(self):
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            for stream in (getattr(self,'wfile',None),getattr(self,'rfile',None)):
                try:
                    if stream:stream.close()
                except OSError:pass

    def do_GET(self):
        try:self._handle_get()
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):return
        except (PortalError,AuthError,ValueError,KeyError,TypeError) as exc:
            self.send_json(400,{'error':str(exc)[:180]})
        except Exception as exc:
            self.internal_error(exc)

    def _handle_get(self):
        if not self.guard_host():return self.send_json(403,{'error':'Host mismatch'})
        route=urlsplit(self.path).path
        if route in ('/','/workspace','/library','/tasks','/following','/inquiry','/reports','/settings','/account','/portal.js','/portal.css','/portal_polish.css','/portal_ux_v147.css','/ux_v149.css','/reading_links.js','/math_render.js','/trace_workbench.js','/inquiry_workbench.js','/research_program.js','/research_program.css','/ui_state.js'):
            names={name:('portal.html','text/html; charset=utf-8') for name in ('/','/workspace','/library','/tasks','/following','/inquiry','/reports','/settings','/account')}
            names.update({'/portal.js':('portal.js','text/javascript; charset=utf-8'),
                '/portal.css':('portal.css','text/css; charset=utf-8'),
                '/portal_polish.css':('portal_polish.css','text/css; charset=utf-8'),
                '/portal_ux_v147.css':('portal_ux_v147.css','text/css; charset=utf-8'),
                '/ux_v149.css':('ux_v149.css','text/css; charset=utf-8'),
                '/reading_links.js':('reading_links.js','text/javascript; charset=utf-8'),
                '/math_render.js':('math_render.js','text/javascript; charset=utf-8'),
                '/trace_workbench.js':('trace_workbench.js','text/javascript; charset=utf-8'),
                '/inquiry_workbench.js':('inquiry_workbench.js','text/javascript; charset=utf-8'),
                '/research_program.js':('research_program.js','text/javascript; charset=utf-8'),
                '/research_program.css':('research_program.css','text/css; charset=utf-8'),
                '/ui_state.js':('ui_state.js','text/javascript; charset=utf-8')})
            file,type_=names[route]
            return self.send_static(STATIC/file,type_)
        if route=='/guide.css':
            return self.send_static(STATIC/'guide.css','text/css; charset=utf-8')
        if route.startswith('/guide/assets/'):
            from .help_pages import guide_asset
            asset=guide_asset(route[len('/guide/assets/'):])
            if not asset:return self.send_json(404,{'error':'插图不存在'})
            return self.send_static(asset,'image/svg+xml')
        if route.startswith('/guide/'):
            from .help_pages import render_guide
            body=render_guide(route[len('/guide/'):],public=True)
            if body is None:
                return self.send_json(404,{'error':'文档不存在'})
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.security_headers()
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
            return
        if route=='/api/public/health':
            return self.send_json(200,{'name':'Paper-Radar Public','version':__version__,
                                       'registration_enabled':self.server.portal.allow_registration})
        user=self.session()
        if not user:return self.send_json(401,{'error':'请先登录'})
        if route=='/api/public/me':
            return self.send_json(200,{'user':{k:user[k] for k in ('id','username','role')},'csrf':user['csrf']})
        if route=='/api/public/following':
            from .following import status
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            self.server.portal.available(user['id'],topic)
            with self.server.portal.workspace(user['id']) as db:
                return self.send_json(200,status(db,topic,limit=60))
        if route=='/api/public/quotas':
            return self.send_json(200,{'quotas':self.server.portal.job_queue.quota_status(user['id'])})
        if route=='/api/public/model/preflight':
            from .model_governance import estimate
            q=parse_qs(urlsplit(self.path).query)
            action=(q.get('action') or ['experiments'])[0]
            if action not in ('experiments','fulltext'):
                raise PortalError('不支持的公网模型评估任务')
            configured=(self.server.portal.model_callable is not None or all(os.getenv(k,'').strip()
                for k in ('PAPERRADAR_PUBLIC_LLM_KEY','PAPERRADAR_PUBLIC_LLM_MODEL','PAPERRADAR_PUBLIC_LLM_BASE')))
            cfg={'llm_input_usd_per_million':os.getenv('PAPERRADAR_PUBLIC_LLM_INPUT_USD_PER_MILLION',''),
                 'llm_output_usd_per_million':os.getenv('PAPERRADAR_PUBLIC_LLM_OUTPUT_USD_PER_MILLION','')}
            return self.send_json(200,{'available':bool(configured),'estimate':estimate(cfg,action),
                'destination_host':(urlsplit(os.getenv('PAPERRADAR_PUBLIC_LLM_BASE','')).hostname or '') if configured else '',
                'notice':'检查不发送论文内容。付费云分析需要提交任务时主动确认；费用按管理员填写的单价估算，未知单价会明确标记。'})
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
        if route in ('/api/public/inquiry','/api/public/inquiry/view'):
            from .research_intelligence import list_portal,read_portal
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            if route.endswith('/view'):
                return self.send_json(200,read_portal(self.server.portal,user['id'],topic,
                           (q.get('id') or [''])[0]))
            return self.send_json(200,list_portal(self.server.portal,user['id'],topic))
        if route in ('/api/public/research-synthesis','/api/public/research-synthesis/view'):
            from . import research_synthesis_agent as synthesis44
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            if route.endswith('/view'):
                return self.send_json(200,synthesis44.read_portal(self.server.portal,user['id'],topic,
                                    (q.get('id') or [''])[0]))
            return self.send_json(200,synthesis44.list_portal(self.server.portal,user['id'],topic))
        if route in ('/api/public/research-memory','/api/public/research-briefs','/api/public/research-briefs/view'):
            from . import research_continuity as continuity45
            from .evidence_chain import EvidenceChain
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            self.server.portal.available(user['id'],topic)
            with self.server.portal.workspace(user['id']) as db:
                chain=EvidenceChain(db,topic,backend='portal',portal=self.server.portal,uid=user['id'])
                if route=='/api/public/research-memory':
                    result=continuity45.read_memory(db,topic,chain)
                elif route.endswith('/view'):
                    result=continuity45.read_brief(db,topic,(q.get('id') or [''])[0],chain)
                else:result=continuity45.list_briefs(db,topic)
            return self.send_json(200,result)
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
                rows=db.execute('''SELECT t.id,t.name,t.keywords,t.created_at,s.sources_json
                    FROM topics t LEFT JOIN topic_sources s ON s.topic_id=t.id
                    ORDER BY CASE WHEN t.id='default' THEN 0 ELSE 1 END,t.created_at,t.rowid''').fetchall()
            return self.send_json(200,{'topics':[{'id':r['id'],'name':r['name'],
                'keywords':json.loads(r['keywords']),
                'sources':validate_sources(json.loads(r['sources_json'])) if r['sources_json'] else ['arxiv']}
                for r in rows], 'supported_sources':list(ALLOWED_SOURCES)})
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
                inbox=db.execute('''SELECT COUNT(*) FROM topic_papers tp
                    LEFT JOIN topic_paper_flags f ON f.topic_id=tp.topic_id AND f.paper_id=tp.paper_id
                    LEFT JOIN paper_notes n ON n.topic_id=tp.topic_id AND n.paper_id=tp.paper_id
                    WHERE tp.topic_id=? AND COALESCE(f.is_starred,0)=0
                      AND COALESCE(f.feedback,0)=0
                      AND COALESCE(f.reading_status,'unread')='unread'
                      AND COALESCE(n.next_action,'none')='none' AND COALESCE(n.note,'')='' ''',(topic,)).fetchone()[0]
                actions=db.execute('''SELECT COUNT(*) FROM paper_notes n
                    JOIN topic_papers tp ON tp.topic_id=n.topic_id AND tp.paper_id=n.paper_id
                    WHERE n.topic_id=? AND n.next_action<>'none' ''',(topic,)).fetchone()[0]
                todo=db.execute('''SELECT COUNT(*) FROM topic_papers tp
                    LEFT JOIN topic_paper_flags f ON f.topic_id=tp.topic_id AND f.paper_id=tp.paper_id
                    WHERE tp.topic_id=? AND COALESCE(f.is_starred,0)=1
                    AND COALESCE(f.reading_status,'unread')<>'done' ''',(topic,)).fetchone()[0]
                pending=db.execute('''SELECT COUNT(DISTINCT v.id) FROM verified_evidence v
                    JOIN topic_papers tp ON tp.paper_id=v.paper_id AND tp.topic_id=?
                    JOIN pdf_documents d ON d.paper_id=v.paper_id AND d.sha256=v.sha256
                    LEFT JOIN evidence_reviews r ON r.evidence_id=v.id AND r.sha256=v.sha256
                    WHERE r.decision IS NULL OR r.decision='needs_review' ''',(topic,)).fetchone()[0]
                pending_papers=db.execute('''SELECT p.id,p.title,MIN(v.page) AS first_page,
                    COUNT(*) AS pending_count FROM verified_evidence v
                    JOIN topic_papers tp ON tp.paper_id=v.paper_id AND tp.topic_id=?
                    JOIN papers p ON p.id=v.paper_id
                    JOIN pdf_documents d ON d.paper_id=v.paper_id AND d.sha256=v.sha256
                    LEFT JOIN evidence_reviews r ON r.evidence_id=v.id AND r.sha256=v.sha256
                    WHERE r.decision IS NULL OR r.decision='needs_review'
                    GROUP BY p.id,p.title ORDER BY pending_count DESC,p.id LIMIT 5''',(topic,)).fetchall()
                latest=db.execute('''SELECT p.id,p.title,p.published
                    FROM papers p JOIN topic_papers tp ON p.id=tp.paper_id
                    WHERE tp.topic_id=? ORDER BY p.published DESC,p.id DESC LIMIT 3''',(topic,)).fetchall()
            return self.send_json(200,{'papers':counts['papers'],'starred':counts['starred'],
                'read':counts['read'],'reports':reports,'to_read':todo,
                'inbox':inbox,'next_actions':actions,'pending_review':pending,
                'pending_papers':[dict(r) for r in pending_papers],
                'recent':[dict(r) for r in latest]})
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
            paper_id=(q.get('paper_id') or [''])[0]
            if len(paper_id)>150 or len(search)>120 or filter_ not in ('all','inbox','skipped','actionable','starred','to_read','unread','reading','done','liked') \
                    or sort not in ('recent','oldest','title'):
                raise PortalError('Invalid search or filter')
            where=['tp.topic_id=?'];params=[topic]
            if paper_id:
                where.append('p.id=?');params.append(paper_id)
            if search:
                pattern='%'+search.lower().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
                where.append('''(LOWER(p.title) LIKE ? ESCAPE '\\' OR
                    LOWER(p.abstract) LIKE ? ESCAPE '\\' OR
                    LOWER(p.doi) LIKE ? ESCAPE '\\')''')
                params.extend((pattern,)*3)
            if filter_ in ('starred','to_read'):where.append('COALESCE(f.is_starred,0)=1')
            if filter_=='to_read':where.append("COALESCE(f.reading_status,'unread')<>'done'")
            if filter_ in ('unread','reading','done'):
                where.append("COALESCE(f.reading_status,'unread')=?");params.append(filter_)
            if filter_=='liked':where.append('COALESCE(f.feedback,0)=1')
            if filter_=='inbox':
                where.extend(("COALESCE(f.is_starred,0)=0", "COALESCE(f.feedback,0)=0",
                              "COALESCE(f.reading_status,'unread')='unread'",
                              "COALESCE(n.next_action,'none')='none'","COALESCE(n.note,'')=''"))
            if filter_=='skipped':where.append('COALESCE(f.feedback,0)=-1')
            if filter_=='actionable':where.append("COALESCE(n.next_action,'none')<>'none'")
            order={'recent':'p.published DESC,p.id DESC',
                   'oldest':'p.published ASC,p.id ASC','title':'p.title COLLATE NOCASE ASC,p.id'}[sort]
            relation='''FROM papers p JOIN topic_papers tp ON tp.paper_id=p.id
                LEFT JOIN topic_paper_flags f ON f.paper_id=p.id AND f.topic_id=tp.topic_id
                LEFT JOIN paper_notes n ON n.paper_id=p.id AND n.topic_id=tp.topic_id'''
            criterion=' WHERE '+' AND '.join(where)
            with self.server.portal.workspace(user['id']) as db:
                total=db.execute('SELECT COUNT(*) '+relation+criterion,params).fetchone()[0]
                rows=db.execute('''SELECT p.id,p.title,p.abstract,p.published,p.doi,p.url,
                    COALESCE(f.is_starred,0) AS is_starred,
                    COALESCE(f.reading_status,'unread') AS reading_status,
                    COALESCE(f.feedback,0) AS feedback,
                    COALESCE(n.note,'') AS note,
                    COALESCE(n.next_action,'none') AS next_action,
                    COALESCE(n.revision,0) AS note_revision,
                    COALESCE(n.updated_at,'') AS note_updated_at '''+relation+criterion+' ORDER BY '+order+
                    ' LIMIT ? OFFSET ?',(*params,limit,offset)).fetchall()
            return self.send_json(200,{'papers':[dict(r) for r in rows],
                'total':total,'limit':limit,'offset':offset})
        if route=='/api/public/papers/citations':
            from .citation_formats import export as export_citations
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            scope=(q.get('scope') or ['starred'])[0]
            fmt=(q.get('format') or ['bib'])[0]
            if scope not in ('starred','all') or fmt not in ('bib','ris'):
                raise PortalError('Invalid citation export options')
            self.server.portal.available(user['id'],topic)
            with self.server.portal.workspace(user['id']) as db:
                rows=db.execute("""SELECT p.id AS paper_id,p.title,p.published,p.doi,p.url
                    FROM papers p JOIN topic_papers tp ON tp.paper_id=p.id
                    LEFT JOIN topic_paper_flags f ON f.paper_id=p.id AND f.topic_id=tp.topic_id
                    WHERE tp.topic_id=? AND (?='all' OR COALESCE(f.is_starred,0)=1)
                    ORDER BY p.published DESC,p.id LIMIT 2001""",(topic,scope)).fetchall()
            if len(rows)>2000:raise PortalError('Export exceeds 2000 papers')
            raw,mime,filename=export_citations([dict(r) for r in rows],fmt)
            self.send_response(200)
            self.send_header('Content-Type',mime)
            self.security_headers()
            self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
            return
        if route=='/api/public/reading/export':
            from .portal_triage import export_reading
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            scope=(q.get('scope') or ['starred'])[0]
            format_=(q.get('format') or ['md'])[0]
            raw,content_type,filename=export_reading(self.server.portal,user['id'],topic,
                scope=scope,format_=format_)
            self.send_response(200)
            self.send_header('Content-Type',content_type)
            self.security_headers()
            self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
            return
        if route=='/api/public/evidence/export':
            from .portal_export import export_evidence
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            raw=export_evidence(self.server.portal,user['id'],topic)
            self.send_response(200)
            self.send_header('Content-Type','text/csv; charset=utf-8')
            self.security_headers()
            self.send_header('Content-Disposition','attachment; filename="paper-radar-evidence.csv"')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
            return
        if route in ('/api/public/pdf/locate','/api/public/pdf/tables','/api/public/pdf/highlight'):
            from .pdf_geometry import locate, page_tables, preview_png
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            pid=(q.get('paper_id') or [''])[0]
            quote=(q.get('quote') or [''])[0]
            if not 1<=len(pid)<=150 or len(quote)>700:
                raise PortalError('论文或原文引文长度无效')
            try:page=int((q.get('page') or ['0'])[0])
            except (ValueError,TypeError):raise PortalError('PDF 页码无效')
            from .evidence_chain import MAX_EVIDENCE_PAGES
            if not 1<=page<=MAX_EVIDENCE_PAGES:raise PortalError(f'PDF 页码需在 1—{MAX_EVIDENCE_PAGES} 范围内')
            portal=self.server.portal
            # Topic membership and current document fingerprint checked before
            # touching PDF bytes. Never accept an arbitrary client path.
            state=portal_research.state(portal,user['id'],topic,pid)
            if state.get('state') not in ('saved','indexed'):
                raise PortalError('当前账号的 PDF 不可用或指纹已经改变')
            if state.get('pages') and page>int(state['pages']):
                raise PortalError('PDF 页码超出范围')
            with auth_db(portal.auth_path) as auth:
                if not throttle(auth,'pdf_geometry:'+user['id'],limit=40,seconds=60):
                    return self.send_json(429,{'error':'PDF 定位请求过于频繁，请稍后重试'})
            path=portal_research.path_for(portal,user['id'],pid)
            if route.endswith('/highlight'):
                raw=preview_png(path,state['sha256'],page,quote)
                self.send_response(200)
                self.send_header('Content-Type','image/png')
                self.security_headers()
                self.send_header('Cache-Control','no-store')
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers();self.wfile.write(raw)
            elif route.endswith('/tables'):
                return self.send_json(200,page_tables(path,state['sha256'],page))
            else:
                return self.send_json(200,locate(path,state['sha256'],page,quote))
            return
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
            from .evidence_chain import MAX_EVIDENCE_PAGES
            if not 1<=page<=MAX_EVIDENCE_PAGES:raise PortalError(f'Page limit 1-{MAX_EVIDENCE_PAGES}')
            return self.send_json(200,portal_research.preview(self.server.portal,user['id'],topic,pid,page))
        if route in ('/api/public/trace','/api/public/trace/export','/api/public/trace/history'):
            from .evidence_chain import portal_chain
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            paper_id=(q.get('paper_id') or [''])[0]
            if route.endswith('/history'):
                claim_id=(q.get('claim_id') or [''])[0]
                return self.send_json(200,portal_chain(self.server.portal,user['id'],topic,
                    'history',claim_id))
            if route.endswith('/export'):
                raw=portal_chain(self.server.portal,user['id'],topic,'export_markdown').encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type','text/markdown; charset=utf-8')
                self.security_headers()
                self.send_header('Content-Disposition','attachment; filename="paper-radar-trace.md"')
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers();self.wfile.write(raw)
                return
            return self.send_json(200,portal_chain(self.server.portal,user['id'],topic,'read',paper_id=paper_id))
        if route=='/api/public/reviews/history':
            from .portal_reviews import review_history
            q=parse_qs(urlsplit(self.path).query)
            topic=(q.get('topic_id') or ['default'])[0]
            pid=(q.get('paper_id') or [''])[0]
            try:limit=int((q.get('limit') or ['100'])[0])
            except (ValueError,TypeError):raise PortalError('Invalid review history limit')
            return self.send_json(200,review_history(self.server.portal,user['id'],topic,pid,limit))
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
            if route=='/api/public/pdf/question':
                from .paper_questions import ask_public
                if set(payload)!={'topic_id','paper_id','question'} or any(not isinstance(v,str) for v in payload.values()):
                    raise PortalError('论文问答参数不正确')
                return self.send_json(200,ask_public(self.server.portal,user['id'],
                    payload['topic_id'],payload['paper_id'],payload['question']))
            if route=='/api/public/inquiry/run':
                from .research_intelligence import run_portal
                if set(payload) != {'topic_id','question'}:
                    raise PortalError('研究问题提交字段不完整')
                return self.send_json(201,run_portal(self.server.portal,user['id'],
                                  payload['topic_id'],payload['question']))
            if route=='/api/public/research-synthesis/run':
                from .research_synthesis_agent import run_portal
                if set(payload)!={'topic_id','question'}:
                    raise PortalError('研究综合请求字段无效')
                return self.send_json(201,run_portal(self.server.portal,user['id'],
                                    payload['topic_id'],payload['question']))
            if route in ('/api/public/research-memory/save','/api/public/research-memory/retire',
                         '/api/public/research-memory/feedback','/api/public/research-briefs/preferences'):
                from . import research_continuity as continuity45
                from .evidence_chain import EvidenceChain
                topic=payload.get('topic_id','default')
                self.server.portal.available(user['id'],topic)
                with self.server.portal.workspace(user['id']) as db:
                    chain=EvidenceChain(db,topic,backend='portal',portal=self.server.portal,uid=user['id'])
                    with db:
                        if route.endswith('/save'):
                            result=continuity45.save_memory(db,topic,chain,kind=payload.get('kind'),
                                body=payload.get('body'),claim_ids=payload.get('claim_ids',[]),
                                expected_revision=payload.get('expected_revision',0),item_id=payload.get('item_id',''))
                        elif route.endswith('/retire'):
                            result=continuity45.retire_memory(db,topic,payload.get('item_id'),payload.get('expected_revision'))
                        elif route.endswith('/feedback'):
                            result=continuity45.feedback(db,topic,payload.get('paper_id'),payload.get('decision'))
                        else:result=continuity45.update_preferences(db,topic,
                                enabled=payload.get('enabled'),cadence=payload.get('cadence'))
                return self.send_json(200,result)
            if route=='/api/public/research-briefs/run':
                from .research_continuity import run_portal
                if set(payload)!={'topic_id','cadence'}:raise PortalError('研究简报请求字段无效')
                return self.send_json(201,run_portal(self.server.portal,user['id'],
                    payload['topic_id'],cadence=payload['cadence']))
            if route in ('/api/public/following/preferences','/api/public/following/seen'):
                from .following import update_preference,mark_seen,status
                topic=payload.get('topic_id','default')
                if not isinstance(topic,str):raise PortalError('研究主题无效')
                self.server.portal.available(user['id'],topic)
                with self.server.portal.workspace(user['id']) as db:
                    if route.endswith('/preferences'):
                        value=update_preference(db,topic,payload.get('preferences',{}))
                    else:
                        value={'seen':mark_seen(db,topic,payload.get('event_ids',[]))}
                    current=status(db,topic,limit=60)
                return self.send_json(200,{'result':value,'following':current})
            if route=='/api/public/password':
                with auth_db(self.server.portal.auth_path) as db:
                    change_password(db,user['id'],payload.get('old_password',''),payload.get('new_password',''))
                return self.send_json(200,{'ok':True},cookie=self.session_cookie('',clear=True))
            if route=='/api/public/topics':
                name=str(payload.get('name','')).strip()[:80]
                words=payload.get('keywords')
                if not name or not isinstance(words,list) or not 1<=len(words)<=6:
                    raise PortalError('请输入主题名称及 1—6 组关键词')
                if any(type(word) is not str or not 1<=len(word.strip())<=80 for word in words):
                    raise PortalError('每组关键词必须是 1—80 个字符')
                keywords=[word.strip() for word in words]
                sources=validate_sources(payload.get('sources',['arxiv']))
                uid=uuid.uuid4().hex[:16]
                with self.server.portal.workspace(user['id']) as db:
                    if db.execute('SELECT COUNT(*) FROM topics').fetchone()[0]>=30:
                        raise PortalError('主题数量上限为 30')
                    db.execute('INSERT INTO topics(id,name,keywords,created_at) VALUES (?,?,?,?)',
                               (uid,name,json.dumps(keywords,ensure_ascii=False),utc_now()))
                    set_topic_sources(db,uid,sources)
                return self.send_json(201,{'id':uid,'name':name,'keywords':keywords,'sources':sources})
            if route=='/api/public/topics/update':
                topic=payload.get('topic_id')
                if not isinstance(topic,str) or not TOPIC_ID.fullmatch(topic):
                    raise PortalError('研究主题不存在')
                name=payload.get('name')
                words=payload.get('keywords')
                if not isinstance(name,str) or not 1<=len(name.strip())<=80:
                    raise PortalError('主题名称应为 1—80 个字符')
                if not isinstance(words,list) or not 1<=len(words)<=6 or any(
                    type(word) is not str or not 1<=len(word.strip())<=80 for word in words):
                    raise PortalError('请输入 1—6 组关键词，每组不超过 80 个字符')
                keywords=[word.strip() for word in words]
                sources=validate_sources(payload.get('sources',['arxiv']))
                with self.server.portal.workspace(user['id']) as db:
                    result=db.execute('UPDATE topics SET name=?,keywords=? WHERE id=?',
                        (name.strip(),json.dumps(keywords,ensure_ascii=False),topic))
                    if not result.rowcount:raise PortalError('研究主题不存在')
                    set_topic_sources(db,topic,sources)
                return self.send_json(200,{'id':topic,'name':name.strip(),
                    'keywords':keywords,'sources':sources})
            if route=='/api/public/papers/abstract/refresh':
                from .abstract_recovery import refresh_portal_abstract
                if set(payload)!={'topic_id','paper_id'}:
                    raise PortalError('请指定论文和研究主题')
                topic=payload['topic_id'];pid=payload['paper_id']
                self.server.portal.available(user['id'],topic)
                with self.server.portal.workspace(user['id']) as db:
                    if not db.execute('SELECT 1 FROM topic_papers WHERE topic_id=? AND paper_id=?',
                                      (topic,pid)).fetchone():
                        raise PortalError('论文不存在或无权访问')
                    result=refresh_portal_abstract(db,pid)
                return self.send_json(200,result)
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
            if route=='/api/public/papers/bulk':
                from .portal_triage import bulk_flags
                if set(payload)!={'topic_id','paper_ids','action'}:
                    raise PortalError('请提供研究主题、论文 ID 列表和批量操作')
                result=bulk_flags(self.server.portal,user['id'],payload['topic_id'],
                                  payload['paper_ids'],payload['action'])
                return self.send_json(200,result)
            if route=='/api/public/papers/note':
                from .portal_triage import save_note,NoteConflict
                required={'topic_id','paper_id','note','next_action','expected_revision'}
                if set(payload)!=required:
                    raise PortalError('阅读笔记缺少必要字段')
                try:
                    result=save_note(self.server.portal,user['id'],payload['topic_id'],
                        payload['paper_id'],payload['note'],payload['next_action'],
                        payload['expected_revision'])
                except NoteConflict as exc:
                    return self.send_json(409,{'error':str(exc),'code':'NOTE_CONFLICT'})
                return self.send_json(200,result)
            if route in ('/api/public/trace/anchor','/api/public/trace/claim',
                         '/api/public/trace/confirm'):
                from .evidence_chain import portal_chain, RevisionConflict
                topic=payload.get('topic_id','')
                if route.endswith('/anchor'):
                    if set(payload)!= {'topic_id','paper_id','page','quote','experiment_id','expected_sha256'}:
                        raise PortalError('原文定位字段不完整')
                    operation='add_anchor'
                    params=(payload['paper_id'],payload['page'],payload['quote'])
                    options={'experiment_id':payload['experiment_id'],
                             'expected_sha256':payload['expected_sha256']}
                elif route.endswith('/claim'):
                    if set(payload)!= {'topic_id','body','kind','anchor_ids','expected_revision','claim_id'}:
                        raise PortalError('研究结论字段不完整')
                    operation='save_claim'
                    params=(payload['body'],payload['kind'],payload['anchor_ids'],payload['expected_revision'])
                    options={'claim_id':payload['claim_id']}
                else:
                    if set(payload)!= {'topic_id','claim_id','expected_revision','review_note','decision'}:
                        raise PortalError('结论复核字段不完整')
                    operation='confirm'
                    params=(payload['claim_id'],payload['expected_revision'],payload['review_note'])
                    options={'decision':payload['decision']}
                try:
                    result=portal_chain(self.server.portal,user['id'],topic,operation,*params,**options)
                except RevisionConflict as exc:
                    return self.send_json(409,{'error':str(exc),'code':'CLAIM_REVISION_CONFLICT'})
                return self.send_json(200,result)
            if route=='/api/public/evidence/review':
                from .portal_reviews import review_evidence
                result=review_evidence(self.server.portal,user['id'],
                    payload.get('topic_id','default'),payload.get('paper_id'),
                    payload.get('evidence_id'),payload.get('decision'),
                    payload.get('conditions',{}),payload.get('note',''))
                return self.send_json(200,result)
            if route=='/api/public/comparisons/review':
                from .portal_reviews import review_pair
                result=review_pair(self.server.portal,user['id'],
                    payload.get('topic_id','default'),payload.get('evidence_a_id'),
                    payload.get('evidence_b_id'),payload.get('decision'),payload.get('note',''))
                return self.send_json(200,result)
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
                if options['cloud_analysis'] and payload.get('confirm_cloud_data_transfer') is not True:
                    raise PortalError('请确认发送所选论文片段至管理员配置的第三方模型后重试。')
                pid=str(payload.get('paper_id','')) if kind=='analyze' else ''
                try:job=self.server.portal.research_async(user['id'],topic,kind=kind,pid=pid,options=options)
                except (PortalError,QueueError) as exc:return self.send_json(429,{'error':str(exc)})
                return self.send_json(202,{'job_id':job['id'],'kind':kind,'state':'queued'})
            if route=='/api/public/jobs/retry':
                jid=payload.get('job_id')
                if type(jid) is not int or jid<1:raise PortalError('任务编号不正确')
                try:job=self.server.portal.job_queue.retry_safe(user['id'],jid)
                except QueueError as exc:return self.send_json(429,{'error':str(exc)})
                return self.send_json(202,{'job_id':job['id'],'retry_of':jid,'state':'queued'})
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
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):return
        except (PortalError,AuthError,ValueError,KeyError,TypeError,UnicodeError,json.JSONDecodeError) as exc:
            self.send_json(400,{'error':str(exc)[:180]})
        except Exception as exc:
            self.internal_error(exc)


    def do_PUT(self):
        try:
            if not self.guard_host():return self.send_json(403,{'error':'Host mismatch'})
            if urlsplit(self.path).path!='/api/public/pdf':return self.send_json(404,{'error':'Unknown API'})
            if self.headers.get('Origin','')!=self.server.portal.origin or self.headers.get('X-Portal-Action','')!='1':
                return self.send_json(403,{'error':'Origin mismatch'})
            user=self.session()
            if not user:return self.send_json(401,{'error':'Please log in'})
            if self.headers.get('X-CSRF-Token','')!=user['csrf']:return self.send_json(403,{'error':'CSRF mismatch'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/pdf':return self.send_json(415,{'error':'PDF only'})
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
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):return
        except (ValueError,PortalError,OSError) as exc:return self.send_json(400,{'error':str(exc)[:160]})
        except Exception as exc:return self.internal_error(exc)


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
    member=sub.add_parser('user-create');member.add_argument('--username',required=True)
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
    if args.command in ('admin-create','user-create'):
        from getpass import getpass
        portal=Portal(args.data_root)
        value=getpass('设置账号密码（至少 12 位）：')
        with auth_db(portal.auth_path) as db:
            user=create_account(db,args.username,value,
                                role='admin' if args.command=='admin-create' else 'user')
        print('账号已建立：'+user['username']+'（'+user['role']+'）')
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
            # The source/install tree is read-only under ProtectSystem=strict.
            # make_server already owns the process-wide exclusive lock, so a
            # stale diagnostic marker cannot block a clean service restart.
            runtime=portal.root/'runtime'
            runtime.mkdir(mode=0o700,exist_ok=True)
            marker=runtime/'public_service.active'
            temp=runtime/('.active-'+secrets.token_hex(8)+'.tmp')
            try:
                with temp.open('x',encoding='utf-8') as out:
                    out.write(json.dumps({'pid':os.getpid(),'port':server.server_port,
                        'at':utc_now()})+'\n')
                if os.name!='nt':os.chmod(temp,0o600)
                os.replace(temp,marker)
                print(f'Paper-Radar Public v{__version__} serving on {args.host}:{server.server_port}',flush=True)
                print('Public origin:',portal.origin,flush=True)
                server.serve_forever(poll_interval=.5)
            finally:
                temp.unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
        return 0
    parser.print_help();return 2


if __name__=='__main__':raise SystemExit(cli())
