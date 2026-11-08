# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""On-demand bounded PDF evidence geometry and layout tables.

The caller must provide a PDF path resolved from the managed paper workspace.
Coordinates belong only to the SHA-256 checked original PDF. No OCR and no
claims about correctness of numerical table relationships.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import re

MAX_BYTES=50 * 1024 * 1024
MAX_QUOTE=700
MAX_PAGE=120


def _fitz():
    try:
        import fitz
    except ImportError as exc:
        raise ValueError('精确 PDF 高亮和表格识别需要 PyMuPDF；请安装 pymupdf 后重试。') from exc
    return fitz


def _verified(path, expected_sha256: str):
    path=Path(path)
    if not re.fullmatch(r'[0-9a-f]{64}',str(expected_sha256)):
        raise ValueError('PDF 文件指纹格式不正确')
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('PDF 不存在或超过 50 MB 安全上限')
    digest=sha256()
    with path.open('rb') as fd:
        if fd.read(5) != b'%PDF-':
            raise ValueError('文件不是 PDF')
        fd.seek(0)
        for part in iter(lambda: fd.read(128*1024),b''):
            digest.update(part)
    if digest.hexdigest() != expected_sha256:
        raise ValueError('PDF 原文件已更新，旧证据位置失效，请重新解析')
    return path


def _open(path, sha, page):
    if type(page) is not int or not 1 <= page <= MAX_PAGE:
        raise ValueError('PDF 页码无效')
    fitz=_fitz()
    path=_verified(path,sha)
    doc=fitz.open(str(path))
    if doc.needs_pass or doc.is_encrypted or page>doc.page_count:
        doc.close()
        raise ValueError('PDF 已加密或页码超出范围')
    return doc


def locate(path, sha: str, page: int, quote: str) -> dict:
    if type(quote) is not str or not 6 <= len(quote.strip()) <= MAX_QUOTE:
        raise ValueError('逐字引文长度应在 6—700 字之间')
    with _open(path,sha,page) as document:
        page_obj=document[page-1]
        # search_for is text-layer based. A zero-result response is explicit,
        # not a fabricated position from PDF viewport or fuzzy OCR.
        rectangles=page_obj.search_for(quote.strip(),quads=False)[:24]
        bounds=page_obj.rect
        return {'page':page,'sha256':sha,'matched':bool(rectangles),
                'boxes':[{'x':round(r.x0/bounds.width,5), 'y':round(r.y0/bounds.height,5),
                          'w':round(r.width/bounds.width,5),'h':round(r.height/bounds.height,5)}
                         for r in rectangles], 'page_width':round(bounds.width,2),
                'page_height':round(bounds.height,2),
                'note':'坐标来自当前 PDF 文本层；无结果时请手动打开原文核查。'}


def preview_png(path, sha: str, page: int, quote: str, *, scale: float=1.2) -> bytes:
    if type(quote) is not str or not 6 <= len(quote.strip()) <= MAX_QUOTE:
        raise ValueError('逐字引文长度应在 6—700 字之间')
    fitz=_fitz()
    with _open(path,sha,page) as document:
        pg=document[page-1]
        rects=pg.search_for(quote.strip(),quads=False)[:24]
        if not rects:
            raise ValueError('未能从当前 PDF 文本层精确定位引文，请打开原文核对')
        # Annotation is in memory only; original PDF bytes never change.
        for r in rects:
            pg.draw_rect(r,color=(0.82,0.45,0.05),fill=(1.0,0.92,0.55),fill_opacity=0.22,width=1.5,overlay=True)
        zoom=max(.75,min(1.6,scale))
        # Refuse pathological PDF MediaBox sizes *before* raster allocation.
        if pg.rect.width*pg.rect.height*zoom*zoom>5_000_000:
            raise ValueError('页面图像过大，请打开 PDF 原文核查')
        pix=pg.get_pixmap(matrix=fitz.Matrix(zoom,zoom),alpha=False)
        if pix.width*pix.height>5_000_000:
            raise ValueError('页面图像过大，请打开 PDF 原文核查')
        return pix.tobytes('png')


def page_tables(path, sha: str, page: int) -> dict:
    with _open(path,sha,page) as document:
        pg=document[page-1]
        if not hasattr(pg,'find_tables'):
            return {'page':page,'sha256':sha,'tables':[],'note':'当前 PyMuPDF 不支持表格识别；可查看原文。'}
        try:
            detected=pg.find_tables()
        except (RuntimeError,ValueError):
            detected=None
        tables=[]
        for index,table in enumerate((getattr(detected,'tables',[]) or [])[:8]):
            try:
                rows=table.extract()[:50]
            except (RuntimeError,ValueError,TypeError):
                continue
            clean=[[re.sub(r'\s+',' ',str(cell or ''))[:180] for cell in row[:18]]
                   for row in rows if isinstance(row,(list,tuple))]
            clean=[row for row in clean if sum(bool(cell) for cell in row)>1]
            if clean:
                tables.append({'id':index+1,'rows':clean,'review':'unverified'})
        return {'page':page,'sha256':sha,'tables':tables,
                'note':'识别结果是表格候选；指标、单位、基线及实验条件必须人工核对。'}
