# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Local-only browser workspace for Paper-Radar.

No CDN, remote server, or front-end dependency is required. Only localhost
can connect. Actions that change state require a same-origin CSRF token.
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import urlsplit, parse_qs, unquote
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import time

from .core import (APP_ROOT, DB_FILE, LOCK_FILE, single_instance, read_config, save_config, get_output_dir, run_cycle,
                   report_from_cache, open_database, list_library, count_library, get_recent, update_paper_flags)
from .cloud_api import PRESETS, PROVIDER_INFO, get_api_key, save_api_key, clear_api_key, test_connection, get_balance
from .provider_usage import read_usage
from .sources.credentials import credential_state, save_credential, get_credential
from . import scheduler
from .sources import available_sources
from .downloads import available_downloaders
from .downloads.manager import download_selected, resolve_saved_file
from .run_status import read_status, write_status
from .progress import TaskProgress
from .models import Paper
from .zotero import ZoteroReader, build_profile, detect_database

STATIC = Path(__file__).parent / 'static'
from . import __version__ as VERSION, __app_id__ as APP_ID

class Runtime:
    def __init__(self):
        self.lock = Lock()
        self.busy = False
        self.phase = ''
        self.progress: dict = {}
        self.started_at = ''
        self.logs: list[str] = []
        self.job = ''
        self.last_result: dict = {}
        self.scheduler_cache: dict = {}
        self.scheduler_at = 0.0
        self.schedule_pending = False
        self.schedule_lock = Lock()
        self.stats_cache = {}
        self.stats_cache_key = None
        self.stats_cache_at = 0.0
        self.last_seen = time.monotonic()

    def push(self, text: str):
        line = f'[{datetime.now():%H:%M:%S}] {text}'
        with self.lock:
            self.phase = text[:130]
            self.logs.append(line)
            self.logs = self.logs[-120:]
        # A local bounded diagnostics file, separate from the scheduled task log.
        log_path = APP_ROOT / 'logs' / 'manual.log'
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if log_path.exists() and log_path.stat().st_size > 2_000_000:
                log_path.replace(log_path.with_suffix('.log.1'))
            with log_path.open('a', encoding='utf-8') as fp:
                fp.write(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n')
        except OSError:
            pass

    def state(self):
        with self.lock:
            return {'busy': self.busy, 'phase': self.phase,
                    'logs': self.logs[-36:], 'job': self.job, 'result': self.last_result,
                    'progress': dict(self.progress), 'started_at': self.started_at}

    def schedule(self):
        """Read cached task status; Windows PowerShell lookup runs off the HTTP thread."""
        with self.schedule_lock:
            if not self.schedule_pending and time.monotonic() - self.scheduler_at > 45:
                self.schedule_pending = True
                Thread(target=self._schedule_worker, daemon=True).start()
            return dict(self.scheduler_cache) if self.scheduler_cache else {"installed": False, "state": "checking"}

    def _schedule_worker(self):
        try:
            status = scheduler.status()
        except Exception as exc:
            status = {"installed": False, "state": "error", "message": str(exc)[:180]}
        with self.schedule_lock:
            self.scheduler_cache = status
            self.scheduler_at = time.monotonic()
            self.schedule_pending = False

    def refresh_schedule(self):
        with self.schedule_lock:
            self.scheduler_at = 0
        return self.schedule()

    def metrics(self, cfg: dict) -> dict:
        """Cache dashboard counts briefly; database changes invalidate the snapshot."""
        try:
            db_version = DB_FILE.stat().st_mtime_ns
        except OSError:
            db_version = 0
        key = (db_version, tuple(cfg['keywords']), cfg['lookback_days'],
               cfg['report_limit'], cfg['sort_mode'])
        with self.lock:
            if self.stats_cache_key == key and time.monotonic() - self.stats_cache_at < 15:
                return dict(self.stats_cache)
        with open_database() as db:
            row = db.execute('SELECT COUNT(*) total, SUM(is_starred) starred, '
                             'SUM(reading_status="unread") unread FROM papers').fetchone()
            downloaded = db.execute("SELECT COUNT(*) FROM papers WHERE download_status='downloaded'").fetchone()[0]
            recent = get_recent(db, days=cfg['lookback_days'],limit=cfg['report_limit'],
                                keywords=cfg['keywords'],sort_mode=cfg['sort_mode'])
        result = {'archived':row['total'], 'starred':row['starred'] or 0,
                  'unread':row['unread'] or 0, 'matched':len(recent), 'downloaded':downloaded}
        with self.lock:
            self.stats_cache_key = key
            self.stats_cache_at = time.monotonic()
            self.stats_cache = result
        return dict(result)

    def start(self, mode: str, ids: list[str] | None = None):
        with self.lock:
            if self.busy:
                raise ValueError('当前已有任务在执行。')
            self.busy = True
            self.job = mode
            self.phase = '正在准备…'
            self.started_at = datetime.now().astimezone().isoformat(timespec='seconds')
            self.progress = {'percent':0, 'stage':'等待启动', 'detail':'正在初始化任务'}
            self.logs.clear()
            self.last_result = {}
        Thread(target=self._worker, args=(mode, ids or []), daemon=True).start()

    def _worker(self, mode, ids):
        status = {'state':'running', 'started_at':datetime.now().astimezone().isoformat(timespec='seconds'), 'mode':mode}
        write_status(status)
        def on_progress(value):
            with self.lock:
                self.progress = dict(value)
                self.phase = value.get('detail') or value.get('stage') or self.phase
            # Use the existing atomic status writer to make headless and UI
            # progress visible through the same endpoint.
            write_status({**status, 'progress':value})
        task = TaskProgress(on_progress)
        task.emit(1, '启动任务', detail='正在读取本地设置')
        try:
            if mode == 'search':
                result = run_cycle(read_config(), logger=self.push, progress=on_progress)
                output = {'retrieved':result['retrieved'], 'new':result['new'], 'selected':result['selected']}
                self.push(f"完成：检索 {result['retrieved']} 篇，新增 {result['new']} 篇，报告已更新。")
            elif mode == 'export':
                path = report_from_cache(logger=self.push, progress=on_progress)
                output = {'file':path.name}
                self.push('报告已生成。')
            elif mode == 'download':
                with single_instance(LOCK_FILE):
                    with open_database() as db:
                        results = download_selected(db, ids, read_config(), logger=self.push,
                            progress=lambda done,total,detail: task.emit(
                                5 + 94*done/max(1,total), '下载 PDF',
                                current=done,total=total,detail=detail))
                output = {'downloaded':sum(item['status']=='downloaded' for item in results),
                          'skipped':sum(item['status']=='skipped' for item in results),
                          'failed':sum(item['status'] in ('failed','unavailable') for item in results),
                          'results':results}
                self.push(f"PDF 下载完成：新增 {output['downloaded']}，已存在 {output['skipped']}，未完成 {output['failed']}。")
            elif mode == 'export_all':
                from .reports.text import write_csv
                cfg = read_config()
                with single_instance(LOCK_FILE):
                    with open_database() as db:
                        dest = get_output_dir(cfg)
                        dest.mkdir(parents=True, exist_ok=True)
                        total = db.execute('SELECT COUNT(*) FROM papers').fetchone()[0]
                        task.emit(8, '导出索引', current=0, total=total,
                                  detail=f'准备导出 {total} 条文献记录')
                        def export_rows():
                            for index, record in enumerate(db.execute(
                                'SELECT * FROM papers ORDER BY first_seen DESC, paper_id ASC'), 1):
                                if index % 50 == 0 or index == total:
                                    task.emit(8 + 90*index/max(1,total), '导出索引',
                                              current=index,total=total,detail='正在写入 CSV')
                                yield Paper(**dict(record))
                        rows = export_rows()
                        path = write_csv(dest / '全部文献索引.csv', rows)
                output = {'file': path.name, 'total': total}
                task.emit(100, '已完成', detail=f'导出 {total} 条记录')
                self.push(f'已导出 {total} 条本地文献记录。')
            elif mode == 'test_api':
                task.emit(12, '连接模型服务', detail='等待模型服务商响应；可能需要几十秒')
                reply = test_connection(read_config())
                output = {'reply':reply[:100]}
                task.emit(100, '已完成', detail='模型连接正常')
                self.push('API 连接成功。')
            else:
                raise ValueError('未知操作')
            task.emit(100, '已完成', detail='任务已完成')
            write_status({**status,'state':'success','finished_at':datetime.now().astimezone().isoformat(timespec='seconds'), 'result':output, 'progress':dict(self.progress)})
            with self.lock:
                self.last_result = output
        except Exception as exc:
            # Error messages come from source wrappers and should never contain credentials.
            message = str(exc)[:500]
            self.push('执行失败：' + message)
            write_status({**status,'state':'failed','finished_at':datetime.now().astimezone().isoformat(timespec='seconds'), 'error':message, 'progress':dict(self.progress)})
        finally:
            with self.lock:
                self.busy = False
                self.job = ''

runtime = Runtime()

class Handler(BaseHTTPRequestHandler):
    server_version = 'Paper-Radar/1.9'
    protocol_version = 'HTTP/1.0'

    def log_message(self, fmt, *args):
        # Keep the console quiet during local UI polling.
        pass

    def respond(self, status: int, data: object):
        raw = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def file_response(self, filepath: Path, content_type: str | None = None, attachment=False):
        try:
            data = filepath.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type or mimetypes.guess_type(filepath.name)[0] or 'application/octet-stream')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if attachment:
            self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{__import__('urllib.parse', fromlist=['quote']).quote(filepath.name)}")
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _host_allowed(self) -> bool:
        # Protect the localhost UI from DNS rebinding via attacker-controlled Host headers.
        raw = self.headers.get('Host', '')
        if not raw:
            return False
        try:
            host = urlsplit('//' + raw).hostname
            return host in ('127.0.0.1', 'localhost')
        except ValueError:
            return False

    def do_GET(self):
        if not self._host_allowed():
            self.respond(403, {'error':'仅允许从本机访问'})
            return
        runtime.last_seen = time.monotonic()
        parts = urlsplit(self.path)
        route = parts.path
        try:
            if route == '/':
                source = (STATIC / 'index.html').read_text(encoding='utf-8')
                page = source.replace('__LOCAL_TOKEN__', self.server.token).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Security-Policy',
                                 "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Length', str(len(page)))
                self.end_headers()
                self.wfile.write(page)
            elif route in {'/app.css', '/app.js'}:
                self.file_response(STATIC / route.lstrip('/'))
            elif route == '/api/health':
                self.respond(200, {'app':APP_ID,'version':VERSION})
            elif route == '/api/state':
                cfg = read_config()
                stats = runtime.metrics(cfg)
                try:
                    cfg['api_key_saved'] = bool(get_api_key(cfg['api_provider']))
                except RuntimeError:
                    cfg['api_key_saved'] = False
                self.respond(200, {'version':VERSION, 'config':cfg, 'stats':stats,
                     'runtime':runtime.state(), 'last_run':read_status(), 'scheduler':runtime.schedule(),
                     'api_presets':PRESETS, 'provider_info':PROVIDER_INFO, 'sources':available_sources(),
                     'source_access':credential_state(), 'downloaders':available_downloaders(),
                     'api_usage_local':read_usage()})
            elif route == '/api/provider-catalog':
                usage = read_usage()
                configured = {}
                for name in PRESETS:
                    try:
                        configured[name] = bool(get_api_key(name))
                    except RuntimeError:
                        configured[name] = False
                self.respond(200, {'configured': configured, 'local': usage,
                                   'usage_note': '仅记录本机成功调用，其他程序和控制台用量需到服务商官方页面核查。'})
            elif route == '/api/provider-usage':
                provider=(parse_qs(parts.query).get('provider') or ['DeepSeek'])[0]
                if provider not in PRESETS:
                    self.respond(400,{'error':'未知模型服务商'})
                    return
                # Remote balance is requested on demand; model usage is local-only.
                result = {'provider':provider, 'local':read_usage().get(provider,{}),
                          'info':PROVIDER_INFO.get(provider,{})}
                try:
                    result['remote']=get_balance(provider)
                except RuntimeError as exc:
                    result['remote']={'available':False,'reason':str(exc)[:150]}
                self.respond(200,result)
            elif route == '/api/papers':
                q = parse_qs(parts.query)
                try:
                    limit = max(1, min(int((q.get('limit') or ['40'])[0]), 100))
                    offset = max(0, min(int((q.get('offset') or ['0'])[0]), 500000))
                except (ValueError, OverflowError):
                    self.respond(400, {'error':'分页参数无效'})
                    return
                filters = {'query':(q.get('q') or [''])[0][:100],
                           'only_starred':(q.get('starred') or ['0'])[0] == '1',
                           'reading_status':(q.get('status') or [''])[0]}
                sort = (q.get('sort') or ['relevance'])[0]
                with open_database() as db:
                    total = count_library(db, **filters)
                    papers = list_library(db, **filters, limit=limit, offset=offset, sort=sort)
                self.respond(200, {'papers':[asdict(p) for p in papers], 'total': total,
                                   'offset': offset, 'has_more': offset+len(papers) < total})
            elif route.startswith('/api/pdf/'):
                pid = unquote(route[len('/api/pdf/'):])
                if not pid or len(pid)>150:
                    self.respond(400,{'error':'论文 ID 无效'})
                    return
                with open_database() as db:
                    row=db.execute('SELECT local_file FROM papers WHERE paper_id=?',(pid,)).fetchone()
                file=resolve_saved_file(DB_FILE, row['local_file']) if row else None
                if file is None:
                    self.respond(404,{'error':'本地 PDF 不存在'})
                    return
                self.file_response(file,content_type='application/pdf',attachment=False)
            elif route == '/api/downloads':
                q = parse_qs(parts.query)
                status = (q.get('status') or ['all'])[0]
                if status not in ('all', 'downloaded', 'failed', 'unavailable'):
                    self.respond(400, {'error':'下载筛选状态无效'})
                    return
                filter_sql = "download_status<>''"
                params = []
                if status != 'all':
                    filter_sql += ' AND download_status=?'
                    params.append(status)
                with open_database() as db:
                    rows=db.execute("SELECT paper_id,title,source,download_status,local_file,download_url,download_error,downloaded_at "
                                    "FROM papers WHERE " + filter_sql + " ORDER BY COALESCE(downloaded_at, first_seen) DESC, paper_id ASC LIMIT 500", params).fetchall()
                self.respond(200, {'items':[dict(row) for row in rows]})
            elif route == '/api/report-preview':
                name = (parse_qs(parts.query).get('name') or [''])[0]
                # Report content is external metadata. Never allow arbitrary filesystem access.
                if not name or name != Path(name).name or name.startswith('.') or not name.endswith('.md'):
                    self.respond(400, {'error':'只能预览报告目录中的 Markdown 文件'})
                    return
                filename = get_output_dir(read_config()) / name
                if not filename.is_file():
                    self.respond(404, {'error':'报告文件不存在，请先生成报告'})
                    return
                if filename.stat().st_size > 1_000_000:
                    self.respond(413, {'error':'Markdown 超过预览大小限制，请下载查看'})
                    return
                self.respond(200, {'name':name, 'content':filename.read_text(encoding='utf-8')})
            elif route == '/api/logs':
                which = (parse_qs(parts.query).get('source') or ['scheduled'])[0]
                if which not in {'scheduled', 'manual'}:
                    self.respond(400, {'error':'不支持的日志类型'})
                    return
                path = APP_ROOT / 'logs' / ('scheduled.log' if which == 'scheduled' else 'manual.log')
                try:
                    # Logs are bounded by our own writer; read the last 16 KiB only.
                    with path.open('rb') as f:
                        f.seek(0, 2)
                        f.seek(max(0, f.tell()-16384))
                        log = f.read().decode('utf-8','replace')[-8000:]
                except OSError:
                    log = ''
                self.respond(200, {which: log, 'source':which})
            elif route == '/api/reports':
                folder = get_output_dir(read_config())
                reports = sorted([p for p in folder.glob('*') if p.is_file() and p.suffix.lower() in {'.docx','.md','.csv'}],
                                  key=lambda p:p.stat().st_mtime, reverse=True)[:35] if folder.exists() else []
                self.respond(200, {'reports':[{'name':p.name,'bytes':p.stat().st_size,
                       'modified':datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d %H:%M')}
                       for p in reports]})
            elif route.startswith('/download/'):
                name = unquote(route[len('/download/'):])
                if not name or name != Path(name).name or name.startswith('.') or Path(name).suffix.lower() not in {'.docx','.md','.csv'}:
                    self.respond(400, {'error':'非法文件名'})
                    return
                self.file_response(get_output_dir(read_config()) / name, attachment=True)
            else:
                self.send_error(404)
        except Exception as exc:
            self.respond(500, {'error':str(exc)[:250]})

    def do_POST(self):
        if not self._host_allowed():
            self.respond(403, {'error':'仅允许从本机访问'})
            return
        runtime.last_seen = time.monotonic()
        origin = self.headers.get('Origin', '')
        expected = f'http://127.0.0.1:{self.server.server_port}'
        if (origin and origin != expected) or self.headers.get('X-Local-Token','') != self.server.token:
            self.respond(403, {'error':'请求未通过本地安全校验'})
            return
        try:
            content_length = int(self.headers.get('Content-Length','0'))
            if content_length > 65536 or content_length < 0:
                self.respond(413, {'error':'请求过大'})
                return
            if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                self.respond(415, {'error':'只接受 JSON 请求'})
                return
            data = json.loads(self.rfile.read(content_length) or '{}')
            route = urlsplit(self.path).path
            if route == '/api/config':
                allowed = {'keywords','categories','lookback_days','max_results','report_limit',
                           'schedule_time','ai_enabled','ai_limit','api_provider','api_base_url','api_model', 'sort_mode',
                           'sources','auto_download','download_limit','max_pdf_mb',
                           'zotero_enabled','zotero_mode','zotero_db_path','zotero_collection_key',
                           'zotero_include_children','zotero_refresh_keywords','zotero_keyword_limit'}
                cfg = read_config()
                cfg.update({k:v for k,v in data.items() if k in allowed})
                for source in ('springer','elsevier','sciencedirect'):
                    key_owner = 'elsevier' if source == 'sciencedirect' else source
                    if source in cfg.get('sources',[]) and not get_credential(key_owner):
                        raise ValueError(f'{source} 尚未配置 API Key。请展开对应来源设置并保存密钥后重试。')
                cfg = save_config(cfg)
                key = str(data.get('api_key') or '').strip()
                if key:
                    save_api_key(cfg['api_provider'],key)
                try:
                    saved = bool(get_api_key(cfg['api_provider']))
                except RuntimeError:
                    saved = False
                self.respond(200, {'ok':True,'api_key_saved':saved})
            elif route == '/api/source-credential':
                name=str(data.get('source') or '')
                if name not in {'springer','elsevier'}:
                    raise ValueError('暂不支持该来源的密钥设置')
                field=str(data.get('field') or 'key')
                if field not in ({'key','insttoken'} if name=='elsevier' else {'key'}):
                    raise ValueError('来源凭据字段无效')
                value=data.get('value')
                if not isinstance(value,str):
                    raise ValueError('凭据格式不正确')
                save_credential(name,field,value)
                self.respond(200,{'ok':True,'state':credential_state().get(name,{})})
            elif route == '/api/key/remove':
                clear_api_key(read_config()['api_provider'])
                self.respond(200, {'ok':True})
            elif route == '/api/job':
                mode = str(data.get('mode','search'))
                if mode not in {'search','export','export_all','test_api','download'}:
                    raise ValueError('不支持的任务')
                ids = data.get('ids', []) if mode=='download' else []
                if mode=='download':
                    if not isinstance(ids,list) or len(ids)>20 or not ids or any(not isinstance(x,str) or len(x)>150 for x in ids):
                        raise ValueError('请选择 1 至 20 篇论文下载')
                runtime.start(mode,ids)
                self.respond(202, {'ok':True,'mode':mode})
            elif route == '/api/zotero/collections':
                mode = str(data.get('mode','auto'))
                path = str(data.get('db_path') or '').strip()
                if len(path)>2000:raise ValueError('数据库路径太长。')
                reader = ZoteroReader(mode,path)
                collections = reader.collections()
                self.respond(200, {'collections':[asdict(c) for c in collections],
                    'backend':reader.backend, 'database_path':reader.resolved_path,
                    'count':len(collections)})
            elif route == '/api/zotero/preview':
                mode = str(data.get('mode','auto'))
                path = str(data.get('db_path') or '').strip()
                collection = str(data.get('collection_key') or '').strip()
                include_children = bool(data.get('include_children',True))
                n = max(1,min(15,int(data.get('keyword_limit',8))))
                if len(path)>2000 or len(collection)>64:
                    raise ValueError('Zotero 参数无效')
                profile=build_profile(mode,path,collection,include_children,n)
                self.respond(200, {'collection':profile.collection_name,
                   'count':len(profile.references), 'keywords':profile.keywords,
                   'backend':profile.source,
                   'references':[{'title':r.title, 'tags':r.tags[:6], 'date':r.date,
                     'has_abstract':bool(r.abstract)} for r in profile.references[:12]]})
            elif route == '/api/zotero/apply':
                mode = str(data.get('mode','auto'))
                path = str(data.get('db_path') or '').strip()
                collection = str(data.get('collection_key') or '').strip()
                children = bool(data.get('include_children',True))
                keywords = data.get('keywords') or []
                if isinstance(keywords,str):
                    import re
                    keywords = re.split(r'[,，;；\n]',keywords)
                if not isinstance(keywords,list):
                    raise ValueError('关键词格式不正确。')
                keywords=[str(k).strip()[:100] for k in keywords if str(k).strip()][:15]
                if not keywords:
                    raise ValueError('请至少保留一个检索关键词。')
                # Check the collection before committing persistent settings.
                reader=ZoteroReader(mode,path)
                if collection not in {c.key for c in reader.collections()}:
                    raise ValueError('收藏夹不存在，请重新连接。')
                cfg=read_config()
                cfg.update({'zotero_enabled':True, 'zotero_mode':mode,
                    'zotero_db_path':path, 'zotero_collection_key':collection,
                    'zotero_include_children':children,
                    'zotero_refresh_keywords':bool(data.get('refresh_keywords',False)),
                    'zotero_keyword_limit':max(1,min(15,int(data.get('keyword_limit',8)))),
                    'keywords':keywords})
                save_config(cfg)
                self.respond(200,{'ok':True,'keywords':keywords})
            elif route == '/api/zotero/disable':
                cfg=read_config()
                cfg['zotero_enabled']=False
                save_config(cfg)
                self.respond(200,{'ok':True})
            elif route == '/api/zotero/pick-database':
                # Native local-file chooser; no browser file upload or copy of zotero.sqlite.
                try:
                    import tkinter as tk
                    from tkinter import filedialog
                    root=tk.Tk();root.withdraw()
                    try:
                        chosen=filedialog.askopenfilename(title='选择 Zotero 数据库',
                            filetypes=[('Zotero 数据库','zotero.sqlite'),('SQLite 数据库','*.sqlite')])
                    finally:root.destroy()
                except (ImportError,RuntimeError,Exception) as exc:
                    raise ValueError('本机文件选择窗口无法打开，请手动粘贴 Zotero 数据目录路径。') from exc
                self.respond(200,{'path':str(chosen)})
            elif route == '/api/paper':
                pid = str(data.get('id','')).strip()
                if not pid or len(pid)>100:
                    raise ValueError('论文 ID 无效')
                with open_database() as db:
                    updated = update_paper_flags(db, pid, starred=data.get('starred'),status=data.get('status'))
                self.respond(200 if updated else 404, {'ok':updated})
            elif route == '/api/schedule':
                action = str(data.get('action','status'))
                if action == 'install':
                    result = scheduler.install(read_config()['schedule_time'])
                elif action == 'remove':
                    result = scheduler.remove()
                elif action == 'run':
                    result = scheduler.run_now()
                else:
                    result = scheduler.status()
                runtime.refresh_schedule()
                self.respond(200, result)
            elif route == '/api/folder':
                folder = get_output_dir(read_config())
                folder.mkdir(parents=True, exist_ok=True)
                if os.name == 'nt':
                    os.startfile(str(folder))
                elif sys.platform == 'darwin':
                    subprocess.Popen(['open',str(folder)])
                else:
                    subprocess.Popen(['xdg-open',str(folder)])
                self.respond(200, {'ok':True})
            elif route == '/api/shutdown':
                if runtime.state()['busy']:
                    raise ValueError('任务正在运行，请完成后再退出工作台。')
                self.respond(200, {'ok':True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.respond(404, {'error':'页面不存在'})
        except ValueError as exc:
            self.respond(400, {'error':str(exc)[:350]})
        except Exception as exc:
            self.respond(500, {'error':str(exc)[:350]})


def run_server(port: int = 8765):
    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    httpd.daemon_threads = True
    httpd.token = secrets.token_urlsafe(24)
    def idle_watch():
        while True:
            time.sleep(15)
            if time.monotonic()-runtime.last_seen > 600 and not runtime.state()['busy']:
                httpd.shutdown()
                return
    Thread(target=idle_watch,daemon=True).start()
    print(f'Paper-Radar running at http://127.0.0.1:{port}', flush=True)
    try:
        httpd.serve_forever(poll_interval=.3)
    finally:
        httpd.server_close()

