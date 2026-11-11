"""v1.23 bounded PDF parser with optional layout tables and local-only OCR.

Runs in a subprocess with an OS timeout and address/CPU budget. Page text is
always preserved as a document-local quote source; table cells are separate
layout *candidates*, not automatically validated scientific relationships.
The optional PyMuPDF dependency and local Tesseract binary are never required
for text-only installations. OCR is opt-in: PAPERRADAR_PUBLIC_PDF_OCR=1.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import sys

MAX_PAGES = 240
MAX_PAGE_CHARS = 7200
MAX_TOTAL_CHARS = 1_800_000
MAX_LAYOUT_ROWS = 480
MAX_LAYOUT_TABLES = 60
TABLE_HINT = re.compile(r'\b(?:table|tab\.?\s*\d+|results|benchmark)\b|表\s*\d+|实验结果',re.I)


def _limit_process():
    if sys.platform.startswith('linux'):
        import resource
        for kind, limits in ((resource.RLIMIT_AS,(1024*1024*1024,1024*1024*1024)),
                             (resource.RLIMIT_CPU,(75,80))):
            try:resource.setrlimit(kind,limits)
            except (OSError,ValueError):pass


def _text(page) -> str:
    return str(page.extract_text() or '').replace('\x00','').strip()[:MAX_PAGE_CHARS]


def _rows_from_page(page, page_index, table_start, available_rows):
    if available_rows <= 0:
        return []
    output=[]
    try:
        finder=page.find_tables()
        tables=finder.tables[:min(4,MAX_LAYOUT_TABLES-table_start)]
    except (AttributeError,RuntimeError,ValueError):
        return []
    for local_idx,table in enumerate(tables):
        try:rows=table.extract()
        except (AttributeError,RuntimeError,ValueError):continue
        if not isinstance(rows,list) or not rows:continue
        for row_index,items in enumerate(rows[:min(35,available_rows-len(output))]):
            if not isinstance(items,(tuple,list)):continue
            cells=[str(x or '').strip().replace('\x00','')[:180] for x in items[:18]]
            if sum(bool(x) for x in cells)<2:continue
            output.append({'page':page_index,'table_id':table_start+local_idx,
                           'row_index':row_index,'cells':cells,
                           'method':'pymupdf_table_candidate'})
            if len(output)>=available_rows:break
    return output


def _ocr_page(page):
    """Local Tesseract, capped to eight sparse pages. Disabled by default."""
    try:
        pix=page.get_pixmap(matrix=page.parent.Matrix(1.3,1.3),alpha=False)
    except AttributeError:
        import fitz
        pix=page.get_pixmap(matrix=fitz.Matrix(1.3,1.3),alpha=False)
    if pix.width*pix.height>3_500_000:
        return ''
    raw=pix.tobytes('png')
    try:
        task=subprocess.run(['tesseract','stdin','stdout','-l',os.getenv('PAPERRADAR_PUBLIC_OCR_LANG','eng')],
             input=raw,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=7,check=False)
        if task.returncode:return ''
        return task.stdout.decode('utf-8',errors='replace').replace('\x00','').strip()[:MAX_PAGE_CHARS]
    except (OSError,subprocess.TimeoutExpired):
        return ''


def parse(path:Path):
    # Called in-process by tests and diagnostic utilities; resource limits
    # are applied at worker subprocess entrypoints, never to the caller.
    from pypdf import PdfReader
    doc=PdfReader(str(path),strict=False)
    if doc.is_encrypted:raise ValueError('Encrypted PDF is not supported')
    count=len(doc.pages)
    if count>MAX_PAGES:raise ValueError(f'PDF page count exceeds {MAX_PAGES}')
    if count<1:raise ValueError('PDF has no pages')
    try:
        import fitz
        layout_doc=fitz.open(str(path))
        if layout_doc.is_encrypted or layout_doc.page_count!=count:
            layout_doc.close();layout_doc=None
    except (ImportError,OSError,RuntimeError,ValueError):
        layout_doc=None
    pages=[];table_rows=[];total=0;table_count=0;ocr_pages=0
    ocr_enabled=os.getenv('PAPERRADAR_PUBLIC_PDF_OCR','0')=='1'
    try:
        for index,page in enumerate(doc.pages,1):
            body=_text(page)
            layout_page=None
            if layout_doc is not None:
                try:layout_page=layout_doc.load_page(index-1)
                except (RuntimeError,ValueError):pass
            if len(body)<16 and layout_page is not None:
                try:body=str(layout_page.get_text(sort=True) or '').strip()[:MAX_PAGE_CHARS]
                except (RuntimeError,ValueError):pass
            if len(body)<16 and ocr_enabled and ocr_pages<8 and layout_page is not None:
                body=_ocr_page(layout_page) or body
                ocr_pages+=1
            remain=MAX_TOTAL_CHARS-total
            if remain<=0:
                raise ValueError('PDF text exceeds bounded parser budget')
            body=body[:min(MAX_PAGE_CHARS,remain)]
            total+=len(body)
            pages.append({'page':index,'text':body})
            if (layout_page is not None and (TABLE_HINT.search(body) or
                 len(re.findall(r'(?<!\w)[-+]?\d+(?:\.\d+)?(?!\w)',body))>=4) and
                table_count<MAX_LAYOUT_TABLES and len(table_rows)<MAX_LAYOUT_ROWS):
                found=_rows_from_page(layout_page,index,table_count,
                                      MAX_LAYOUT_ROWS-len(table_rows))
                if found:
                    table_count+=len({row['table_id'] for row in found})
                    table_rows.extend(found)
    finally:
        if layout_doc is not None:layout_doc.close()
    return {'total_pages':count,'pages':pages,'table_rows':table_rows,
            'diagnostics':{'text_pages':sum(bool(p['text']) for p in pages),
                           'table_candidate_rows':len(table_rows),
                           'layout_backend':'pymupdf_optional' if 'fitz' in sys.modules else 'pypdf_only',
                           'ocr_enabled':ocr_enabled,'ocr_attempted_pages':ocr_pages}}


if __name__=='__main__':
    _limit_process()
    # Third-party PDF packages occasionally print setup messages on stdout.
    # Reserve the worker's stdout exclusively for one machine-readable JSON.
    wire=sys.stdout
    sys.stdout=sys.stderr
    try:
        result=parse(Path(sys.argv[1]))
    except Exception as exc:
        wire.write(json.dumps({'error':type(exc).__name__},ensure_ascii=True)+'\n')
        sys.exit(2)
    else:
        wire.write(json.dumps(result,ensure_ascii=True)+'\n')

