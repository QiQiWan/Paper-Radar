# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Bounded, HTTPS-only PDF download manager with local persistence.

The UI accepts paper IDs, never arbitrary URLs. URLs come from vetted source
adapters; each redirect still receives public-address validation.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from urllib import request, error, parse
import hashlib
import ipaddress
import os
import re
import socket
import tempfile
import time
from ..models import Paper
from . import resolve

MAX_REDIRECTS = 4


def assert_public_https(url: str) -> None:
    target = parse.urlsplit(url)
    host = target.hostname
    if target.scheme != 'https' or not host or target.username or target.password or target.port not in (None, 443):
        raise ValueError('仅支持公开 HTTPS 下载地址')
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise RuntimeError('无法解析下载域名') from exc
    if not infos or any(not ipaddress.ip_address(info[4][0]).is_global for info in infos):
        raise ValueError('下载目标不是公网地址')

class CheckedRedirects(request.HTTPRedirectHandler):
    def __init__(self):
        self.count=0
    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        self.count+=1
        if self.count>MAX_REDIRECTS:
            raise ValueError('下载跳转次数过多')
        assert_public_https(newurl)
        return super().redirect_request(req, fp, code, msg, hdrs, newurl)


def stream_pdf(url: str, target: Path, max_bytes: int, opener=None) -> tuple[int,str]:
    """Write atomically; allow only PDF magic. opener is injectable for tests."""
    assert_public_https(url)
    opener = opener or request.build_opener(CheckedRedirects()).open
    req = request.Request(url, headers={
        'User-Agent':'Paper-Radar/1.6 (private local research PDF download)',
        'Accept':'application/pdf'})
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix+'.part')
    size=0
    hasher=hashlib.sha256()
    try:
        with opener(req, timeout=45) as resp, part.open('wb') as file:
            # Some hosts return a PDF under application/octet-stream; use magic bytes.
            first=resp.read(65536)
            if not first.startswith(b'%PDF-'):
                raise ValueError('服务器返回的内容不是 PDF 文件')
            chunks=[first]
            while chunks:
                chunk=chunks.pop()
                size+=len(chunk)
                if size>max_bytes:
                    raise ValueError('PDF 超过大小限制')
                file.write(chunk)
                hasher.update(chunk)
                data=resp.read(65536)
                if data: chunks.append(data)
        if size<10:
            raise ValueError('PDF 内容不完整')
        part.replace(target)
        return size,hasher.hexdigest()
    finally:
        part.unlink(missing_ok=True)


def safe_filename(paper: Paper) -> str:
    # Paper IDs from APIs can contain arbitrary punctuation. Keep filenames stable.
    ident = re.sub(r'[^a-zA-Z0-9_-]+','_', paper.paper_id)[:55]
    title = re.sub(r'[\\/:*?"<>|\x00-\x1f]+',' ',paper.title).strip(' .')[:75] or 'Paper'
    title = re.sub(r'\s+', '_', title)
    return f'{ident}_{title}.pdf'


def download_root(db_file: Path) -> Path:
    # Every database has its own sibling PDFs, avoiding cross-project leakage.
    return db_file.parent.parent/'downloads'


def resolve_saved_file(db_file: Path, relative: str) -> Path | None:
    if not relative:
        return None
    base=download_root(db_file).resolve()
    candidate=(base/relative).resolve()
    if not candidate.is_relative_to(base) or candidate.suffix.lower()!='.pdf':
        return None
    return candidate if candidate.is_file() else None


def download_selected(conn, ids: list[str], config: dict, logger=print, *,
                      db_path: Path | None=None, transfer=stream_pdf, progress=None) -> list[dict]:
    """Download stored papers by IDs. Results preserve per-paper failures."""
    from ..core import DB_FILE
    db_file = Path(db_path or DB_FILE)
    root = download_root(db_file)
    max_bytes=int(config.get('max_pdf_mb',30))*1024*1024
    results=[]
    unique = list(dict.fromkeys(ids))[:20]
    if progress:
        progress(0, len(unique), '正在检查已选论文')
    for index, pid in enumerate(unique,1):
        if not isinstance(pid, str) or len(pid)>150:
            results.append({'id':str(pid)[:150], 'status':'failed', 'error':'论文 ID 无效'})
            if progress: progress(index, len(unique), '论文 ID 无效')
            continue
        row=conn.execute('SELECT * FROM papers WHERE paper_id=?',(pid,)).fetchone()
        if row is None:
            results.append({'id':pid,'status':'failed','error':'论文不存在'})
            if progress: progress(index, len(unique), '论文不存在')
            continue
        paper=Paper(**dict(row))
        file_found=resolve_saved_file(db_file,paper.local_file)
        if file_found:
            results.append({'id':pid,'status':'skipped','file':paper.local_file})
            if progress: progress(index, len(unique), '已下载，跳过')
            continue
        engine=resolve(paper)
        if engine is None:
            error_message='没有可直接下载的开放获取 PDF；可访问论文页面查看全文。'
            conn.execute('UPDATE papers SET download_status=?,download_error=? WHERE paper_id=?',
                         ('unavailable',error_message,pid))
            conn.commit()
            results.append({'id':pid,'status':'unavailable','error':error_message})
            if progress: progress(index, len(unique), '暂无公开 PDF')
            continue
        engine_name,url=engine
        local_name=safe_filename(paper)
        output=root/local_name
        logger(f'下载 PDF {index}/{len(unique)}：{paper.title[:45]}')
        try:
            size,sha=transfer(url,output,max_bytes)
            conn.execute('''UPDATE papers SET local_file=?,download_status='downloaded',
                download_error='',downloaded_at=? WHERE paper_id=?''',
                (local_name,datetime.now().isoformat(timespec='seconds'),pid))
            conn.commit()
            results.append({'id':pid,'status':'downloaded','bytes':size,'sha256':sha,
                            'engine':engine_name,'file':local_name})
        except (OSError,ValueError,RuntimeError,error.HTTPError,error.URLError) as exc:
            output.unlink(missing_ok=True)
            msg=str(exc)[:180]
            conn.execute('UPDATE papers SET download_status=?,download_error=? WHERE paper_id=?',
                         ('failed',msg,pid))
            conn.commit()
            logger(f'下载失败：{msg}')
            results.append({'id':pid,'status':'failed','error':msg})
        if progress: progress(index, len(unique), paper.title[:80])
        if index<len(unique):
            # Be conservative with academic repositories, particularly arXiv.
            time.sleep(3.1 if engine_name=='arxiv_pdf' else 1.0)
    return results
