# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Deterministic, no-network BibTeX/RIS exports for Zotero and reference managers.

Text comes from untrusted metadata. Escape BibTeX control characters and RIS
line prefixes; never execute spreadsheet formulas or use metadata as paths.
"""
from __future__ import annotations
import re
import unicodedata
from collections import Counter

MAX_RECORDS = 2000


def _record(item) -> dict:
    if isinstance(item, dict):
        return item
    return {field: getattr(item, field, '') for field in
            ('paper_id', 'id', 'title', 'authors', 'doi', 'published', 'url', 'venue', 'abstract', 'source')}


def _plain(value: object, limit=2000) -> str:
    value = unicodedata.normalize('NFKC', str(value or ''))
    value = value.replace('\x00', '').replace('\r\n','\n').replace('\r','\n')
    return re.sub(r'\s+', ' ', value).strip()[:limit]


def _bib(value) -> str:
    text = _plain(value)
    return ''.join('\\' + c if c in '\\%#$&_{}' else c for c in text)


def _key(item: dict) -> str:
    author = _plain(item.get('authors') or '').split(',')[0].split()[-1:] or ['Paper']
    author = re.sub(r'[^a-zA-Z0-9]+','',author[0])[:18] or 'Paper'
    year = re.search(r'\b(?:19|20)\d{2}\b',_plain(item.get('published') or ''))
    title = re.sub(r'[^a-zA-Z0-9]+','',_plain(item.get('title') or '')[:30])[:18] or 'Study'
    return author + (year.group() if year else 'n.d.') + title


def _references(records):
    if not isinstance(records, (list, tuple)) or len(records)>MAX_RECORDS:
        raise ValueError('一次最多导出 2000 篇论文')
    return [_record(row) for row in records if _plain(_record(row).get('title'))]


def bibtex(records) -> bytes:
    items = _references(records)
    seen = Counter()
    chunks = []
    for paper in items:
        key = _key(paper)
        seen[key] += 1
        if seen[key] > 1:
            key += str(seen[key])
        attrs = [('title', paper.get('title')),('author', _plain(paper.get('authors')).replace(', ', ' and ')),
                 ('year', _plain(paper.get('published'))[:4]),('journal',paper.get('venue')),
                 ('doi',paper.get('doi')),('url',paper.get('url'))]
        lines = [f'@article{{{key},']
        lines.extend(f'  {name} = {{{_bib(value)}}},' for name,value in attrs if _plain(value))
        lines.append('}')
        chunks.append('\n'.join(lines))
    return ('\n\n'.join(chunks) + ('\n' if chunks else '')).encode('utf-8')


def ris(records) -> bytes:
    chunks=[]
    for paper in _references(records):
        kind = 'CONF' if str(paper.get('source'))=='openreview' else 'JOUR'
        lines=['TY  - '+kind,'TI  - '+_plain(paper.get('title'))]
        for person in _plain(paper.get('authors')).split(','):
            if person.strip():
                lines.append('AU  - '+person.strip()[:160])
        year=_plain(paper.get('published'))[:10]
        if re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?',year):
            lines.append('PY  - '+year)
        for tag,field in (('JO','venue'),('DO','doi'),('UR','url'),('AB','abstract')):
            value=_plain(paper.get(field),max(160,6000 if tag=='AB' else 2000))
            if value:
                lines.append(tag+'  - '+value)
        lines.append('ER  - ')
        chunks.append('\n'.join(lines))
    return ('\n\n'.join(chunks)+('\n' if chunks else '')).encode('utf-8')


def export(records, kind='bib') -> tuple[bytes, str, str]:
    if kind=='bib':
        return bibtex(records),'application/x-bibtex; charset=utf-8','paper-radar-references.bib'
    if kind=='ris':
        return ris(records),'application/x-research-info-systems; charset=utf-8','paper-radar-references.ris'
    raise ValueError('仅支持 BibTeX (.bib) 或 RIS (.ris)')
