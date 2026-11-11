# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Verified PDF evidence location, table-cell candidates and formula context.

A PDF's *text presence* is distinct from its exact geometric location and from
scientific validity of a numeric claim. The response exposes these separately.
No OCR, no arbitrary downloaded URLs, no PDF modification, no automatic gold.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import unicodedata

MAX_BYTES = 50 * 1024 * 1024
MAX_QUOTE = 700
MAX_PAGE = 240
MAX_BOXES = 24
MAX_TABLES = 8


def _fitz():
    try:
        import fitz
    except ImportError as exc:
        raise ValueError('PDF 精确定位需要 PyMuPDF；请安装 pymupdf 后重试。') from exc
    return fitz


def _verified(path, expected_sha256: str):
    path = Path(path)
    if not re.fullmatch(r'[0-9a-f]{64}', str(expected_sha256)):
        raise ValueError('PDF 文件指纹格式不正确')
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('PDF 不存在或超过 50 MB 安全上限')
    digest = sha256()
    with path.open('rb') as fd:
        if fd.read(5) != b'%PDF-':
            raise ValueError('文件不是 PDF')
        fd.seek(0)
        for part in iter(lambda: fd.read(128 * 1024), b''):
            digest.update(part)
    if digest.hexdigest() != expected_sha256:
        raise ValueError('PDF 原文件已更新，旧证据位置失效，请重新解析')
    return path


def _open(path, sha, page):
    if type(page) is not int or not 1 <= page <= MAX_PAGE:
        raise ValueError('PDF 页码无效')
    fitz = _fitz()
    path = _verified(path, sha)
    doc = fitz.open(str(path))
    if doc.needs_pass or doc.is_encrypted or page > doc.page_count:
        doc.close()
        raise ValueError('PDF 已加密或页码超出范围')
    return doc


def _norm(text: str) -> str:
    """Whitespace-insensitive while preserving punctuation and token order."""
    return ''.join(c for c in unicodedata.normalize('NFKC', str(text)).casefold()
                   if not c.isspace())


def _rectdict(rect, page_rect) -> dict:
    if rect.width <= 0 or rect.height <= 0:
        return {}
    return {'x':round(max(0, rect.x0/page_rect.width), 5),
            'y':round(max(0, rect.y0/page_rect.height), 5),
            'w':round(min(1, rect.width/page_rect.width), 5),
            'h':round(min(1, rect.height/page_rect.height), 5)}


def _matched_word_rects(page, quote: str):
    """Align exact text against layout-sorted word sequence with coordinates.

    get_text('text',sort=True) and get_text('words',sort=True) agree on most,
    but not every nested table. Never invent coordinates when they disagree.
    """
    word_entries = page.get_text('words', sort=True)
    concatenated = []
    spans = []
    n = 0
    for word in word_entries:
        form = _norm(word[4])
        if not form:
            continue
        spans.append((n, n+len(form), word))
        concatenated.append(form)
        n += len(form)
    source = _norm(quote)
    corpus = ''.join(concatenated)
    start = corpus.find(source)
    if start < 0 or not source:
        return [], 'text_only', 0
    end = start + len(source)
    positions = [s[2] for s in spans if s[1] > start and s[0] < end]
    # Build a set of line-aware boxes, rather than highlighting the huge page
    # bounding rectangle of snippets crossing two columns or a table.
    rects=[]
    fitz = _fitz()
    for word in positions:
        candidate = fitz.Rect(word[:4])
        if rects and abs(rects[-1].y0 - candidate.y0) <= 2.4 and 0 <= candidate.x0-rects[-1].x1 <= 18:
            rects[-1] = rects[-1] | candidate
        else:
            rects.append(candidate)
    return rects[:MAX_BOXES], ('geometry_truncated' if len(rects)>MAX_BOXES else 'word_geometry_exact'), len(rects)


def _overlap_fraction(rect, other):
    intersection = rect & other
    if intersection.is_empty or rect.get_area() <= 0:
        return 0.0
    return intersection.get_area()/rect.get_area()


def _table_candidates(page, *, include_cells=False) -> list[dict]:
    """Conservative ruled-table detector; text-only guessed grids are excluded."""
    if not hasattr(page, 'find_tables'):
        return []
    # Dense vector graphics (e.g. plotted trajectories with 27k drawing paths)
    # can make table detection unbounded; decline automatic table inference.
    if len(page.get_drawings()) > 500:
        return []
    try:
        found=page.find_tables(strategy='lines_strict')
    except (RuntimeError, ValueError, TypeError):
        return []
    tables=[]
    fitz=_fitz()
    for index, table in enumerate((getattr(found, 'tables', []) or [])[:MAX_TABLES]):
        if table.row_count < 2 or table.col_count < 2:
            continue
        bbox=fitz.Rect(table.bbox)
        rows=[]
        try:
            raw=table.extract()[:50]
        except (RuntimeError,ValueError,TypeError):
            continue
        for row in raw:
            if isinstance(row,(tuple,list)):
                rows.append([re.sub(r'\s+', ' ', str(x or ''))[:180] for x in row[:18]])
        cells=[]
        if include_cells:
            for ri, trow in enumerate(getattr(table,'rows',[])[:50]):
                for ci, cell in enumerate(getattr(trow,'cells',[])[:18]):
                    if cell is None:
                        continue
                    cr=fitz.Rect(cell)
                    if cr.is_empty: continue
                    cells.append({'row':ri,'column':ci,'bbox':_rectdict(cr,page.rect),
                                  'text':rows[ri][ci] if ri<len(rows) and ci<len(rows[ri]) else ''})
        tables.append({'id':index+1,'bbox':_rectdict(bbox,page.rect),'row_count':table.row_count,
                       'column_count':table.col_count,'rows':rows,'cells':cells,
                       'confidence':'ruled_grid_candidate','review':'unverified'})
    return tables


def _table_for_anchor(page, boxes, metric: str, value: str):
    """A detected cell is geometric evidence only, never a proven data relation."""
    fitz=_fitz()
    tables=_table_candidates(page,include_cells=True)
    if not tables:
        return None
    for table in tables:
        box=table['bbox']
        tb=fitz.Rect(box['x']*page.rect.width,box['y']*page.rect.height,
                     (box['x']+box['w'])*page.rect.width,(box['y']+box['h'])*page.rect.height)
        if not any(_overlap_fraction(r,tb)>0.15 for r in boxes):
            continue
        hit=[]
        for cell in table['cells']:
            bb=cell['bbox']
            rr=fitz.Rect(bb['x']*page.rect.width,bb['y']*page.rect.height,
                         (bb['x']+bb['w'])*page.rect.width,(bb['y']+bb['h'])*page.rect.height)
            if any(_overlap_fraction(r,rr)>0.15 for r in boxes):
                hit.append({'row':cell['row'],'column':cell['column'],'text':cell['text'][:120],
                            'bbox':cell['bbox']})
        matched=[]
        for label,target in [('metric',metric),('value',value)]:
            if not target.strip(): continue
            options=[{'row':c['row'],'column':c['column'],'text':c['text'][:120]}
                     for c in table['cells'] if _norm(target) in _norm(c['text'])]
            # One metric often occurs in multiple rows/cells; cannot infer a
            # relation from adjacent tokens in flattened table text.
            matched.append({'field':label,'matches':options[:8],
                            'ambiguous':len(options)!=1})
        return {'table_id':table['id'],'rows':table['row_count'],'columns':table['column_count'],
                'table_bbox':box,'intersecting_cells':hit[:16], 'field_cell_candidates':matched,
                'status':'geometric_candidate_only','scientific_relation_verified':False}
    return None


_MATH = re.compile(r'(?:[∫∑∂∇∆Δ∞≠≤≥≈→⊗λθσµ]|\b(?:PDE|ODE|loss|gradient|equation)\b|(?:\b[xyzuv]_[0-9a-z]+\b)|(?:\(\d{1,3}\)))',re.I)


def _formula_near_anchor(page, context: str, boxes):
    """Report nearby math markers and local lines without transcribing formulae."""
    if not _MATH.search(context):
        return None
    candidate_lines=[]
    for block in page.get_text('dict',sort=True).get('blocks',[]):
        if 'lines' not in block:continue
        for line in block['lines']:
            raw=''.join(span.get('text','') for span in line.get('spans',[])).strip()
            if not raw or not _MATH.search(raw):continue
            r=_fitz().Rect(line['bbox'])
            if boxes and not any(abs(r.y0-br.y0)<70 for br in boxes):
                continue
            candidate_lines.append({'bbox':_rectdict(r,page.rect),'text':raw[:240]})
    return {'status':'formula_context_candidate', 'equation_verified':False,
            'nearby_lines':candidate_lines[:8]}


def _locate_page(page, quote: str, *, metric: str='', value: str='', structural: bool=True) -> dict:
    source=quote.strip()
    # Searching a long, cross-column quote through search_for can be very slow.
    # Route longer snippets directly to the known layout-sorted text index.
    rects=list(page.search_for(source,quads=False))[:MAX_BOXES] if len(source)<68 else []
    strategy='pymupdf_search_for' if rects else ''
    total_rects=len(rects)
    sorted_text=''
    text_match=bool(rects)
    offset=-1
    if not rects:
        sorted_text=page.get_text('text',sort=True)
        normalized_source=_norm(source)
        normalized_full=_norm(sorted_text)
        offset=normalized_full.find(normalized_source)
        text_match=bool(normalized_source and offset>=0)
        if text_match:
            rects,strategy,total_rects=_matched_word_rects(page,source)
        else:
            strategy='unlocated'
    if not sorted_text:
        sorted_text=page.get_text('text',sort=True)
    if offset<0:
        offset=_norm(sorted_text).find(_norm(source))
    # Use literal context from the page; never return a fabricated excerpt.
    preview=sorted_text[:400] if offset<0 else ''
    if offset>=0:
        # Offset is in whitespace-free text. Map it back to original characters.
        positions=[]
        for i,char in enumerate(sorted_text):
            if not char.isspace():
                for _ in unicodedata.normalize('NFKC',char).casefold(): positions.append(i)
        if positions and offset<len(positions):
            a=max(0,positions[offset]-220)
            b=min(len(sorted_text), positions[min(len(positions)-1,offset+len(_norm(source))-1)]+221)
            preview=sorted_text[a:b]
    geometry_complete=bool(rects) and strategy!='geometry_truncated'
    result={'text_matched':text_match,'matched':geometry_complete,'match_strategy':strategy,
            'geometry_status':'exact_or_word_aligned' if geometry_complete else
                              ('text_matched_geometry_unresolved' if text_match else 'unlocated'),
            'boxes':[_rectdict(box,page.rect) for box in rects],
            'total_line_segments':total_rects,'context':preview[:750],
            'page_width':round(page.rect.width,2),'page_height':round(page.rect.height,2),
            'needs_human_review':True,
            'scientific_relation_verified':False}
    if structural and text_match:
        is_table_near=bool(re.search(r'\btable\s*[0-9ivx]+\b', preview, re.I))
        result['table_context']=_table_for_anchor(page,rects,metric[:120],value[:80]) if rects and is_table_near else None
        result['formula_context']=_formula_near_anchor(page,preview,rects)
    return result


def locate(path, sha: str, page: int, quote: str, *, metric: str='', value: str='',
           structural: bool=True) -> dict:
    if type(quote) is not str or not 6 <= len(quote.strip()) <= MAX_QUOTE:
        raise ValueError('逐字引文长度应在 6—700 字之间')
    with _open(path,sha,page) as document:
        result=_locate_page(document[page-1],quote,metric=metric,value=value,structural=structural)
    return {'page':page,'sha256':sha,**result,
            'note':'布局匹配证明原文可定位，不证明数值属于指定实验；表格关系与公式语义需人工核查。'}


def preview_png(path, sha: str, page: int, quote: str, *, scale: float=1.2) -> bytes:
    if type(quote) is not str or not 6 <= len(quote.strip()) <= MAX_QUOTE:
        raise ValueError('逐字引文长度应在 6—700 字之间')
    fitz=_fitz()
    with _open(path,sha,page) as document:
        pg=document[page-1]
        info=_locate_page(pg,quote,structural=False)
        if not info['matched'] or not info['boxes']:
            raise ValueError('未能从当前 PDF 文本层精确定位引文，请打开原文核对')
        for box in info['boxes']:
            r=fitz.Rect(box['x']*pg.rect.width,box['y']*pg.rect.height,
                        (box['x']+box['w'])*pg.rect.width,(box['y']+box['h'])*pg.rect.height)
            pg.draw_rect(r,color=(.82,.45,.05),fill=(1.,.92,.55),fill_opacity=.22,width=1.5,overlay=True)
        zoom=max(.75,min(1.6,scale))
        if pg.rect.width*pg.rect.height*zoom*zoom>5_000_000:
            raise ValueError('页面图像过大，请打开 PDF 原文核查')
        pix=pg.get_pixmap(matrix=fitz.Matrix(zoom,zoom),alpha=False)
        if pix.width*pix.height>5_000_000:
            raise ValueError('页面图像过大，请打开 PDF 原文核查')
        return pix.tobytes('png')


def page_tables(path, sha: str, page: int) -> dict:
    with _open(path,sha,page) as document:
        tables=_table_candidates(document[page-1],include_cells=True)
    return {'page':page,'sha256':sha,'tables':tables,
            'note':'识别结果仅为受规则线约束的表格候选。行列归属、指标单位和实验条件仍需人工核验。'}
