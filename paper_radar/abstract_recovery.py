# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Conservative on-demand backfill of missing publisher/repository abstracts.

Only fixed public API hosts are requested, with bounded response bytes/time.
Missing content is never reconstructed by an LLM or labeled a source abstract.
"""
from __future__ import annotations

import json
import re
from urllib import request, parse, error
from xml.etree import ElementTree as ET

from .core import normalize_doi
from .sources.arxiv import parse_atom
from .sources.crossref import parse_item
from .sources.openalex import parse_work

_MAX_BYTES = 1_000_000
_ARXIV = re.compile(r'^(?:\d{4}\.\d{4,5}|[A-Za-z.\-]+/\d{7})(?:v\d+)?$')

class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('文献接口跳转到了未审核的地址')

_OPENER = request.build_opener(_NoRedirect())


def _json_from(url: str) -> dict:
    req=request.Request(url, headers={'User-Agent': 'Paper-Radar (metadata abstract recovery)', 'Accept':'application/json'})
    with _OPENER.open(req, timeout=8) as resp:
        payload = resp.read(_MAX_BYTES + 1)
    if len(payload)>_MAX_BYTES:
        raise ValueError('来源返回的元数据超过限制')
    obj=json.loads(payload.decode('utf-8'))
    if not isinstance(obj,dict):
        raise ValueError('来源元数据格式无效')
    return obj


def _atom_from(arxiv_id: str):
    req=request.Request('https://export.arxiv.org/api/query?' + parse.urlencode({'id_list':arxiv_id,'max_results':1}),
                        headers={'User-Agent': 'Paper-Radar (abstract recovery)', 'Accept':'application/atom+xml'})
    with _OPENER.open(req, timeout=8) as resp:
        raw=resp.read(_MAX_BYTES+1)
    if len(raw)>_MAX_BYTES:
        raise ValueError('arXiv 元数据超过限制')
    result=parse_atom(raw)
    return next((x for x in result if x.paper_id==re.sub(r'v\d+$','',arxiv_id)),None)


def _candidate(row: dict):
    pid=str(row.get('paper_id') or '')
    arxiv_id=pid.removeprefix('arxiv:')
    if _ARXIV.fullmatch(arxiv_id):
        item=_atom_from(arxiv_id)
        if item and item.abstract:
            return item.abstract, 'arXiv'
    openalex_id=pid.removeprefix('openalex:')
    if re.fullmatch(r'W[0-9]{4,20}',openalex_id):
        obj=_json_from('https://api.openalex.org/works/'+openalex_id)
        item=parse_work(obj)
        if item and str(obj.get('id') or '').endswith('/'+openalex_id) and item.abstract:
            return item.abstract,'OpenAlex'
    doi=normalize_doi(row.get('doi') or (pid.removeprefix('crossref:') if pid.startswith('crossref:') else ''))
    if not doi or len(doi)>160 or not doi.startswith('10.') or not re.fullmatch(r'10\.\d{4,9}/[^\s?#]+',doi):
        return '', ''
    # DOI is always treated as a path segment on fixed API hosts.
    encoded=parse.quote(doi, safe='')
    try:
        obj=_json_from('https://api.crossref.org/works/'+encoded)
        item=parse_item(obj.get('message') or {})
        if item and normalize_doi(item.doi)==doi and item.abstract:
            return item.abstract,'Crossref'
    except (OSError, ValueError, error.HTTPError, json.JSONDecodeError):
        pass
    # OpenAlex can include an abstract even where Crossref deposits only title.
    obj=_json_from('https://api.openalex.org/works/https://doi.org/'+encoded)
    item=parse_work(obj)
    if item and normalize_doi(item.doi)==doi and item.abstract:
        return item.abstract,'OpenAlex'
    return '', ''


def refresh_missing_abstract(db, paper_id: str) -> dict:
    if type(paper_id) is not str or not 1 <= len(paper_id) <= 150:
        raise ValueError('论文编号无效')
    row=db.execute('SELECT paper_id,title,abstract,doi,source FROM papers WHERE paper_id=?',(paper_id,)).fetchone()
    if row is None:
        raise ValueError('论文不存在')
    if str(row['abstract'] or '').strip():
        return {'status':'already_present','paper_id':paper_id,'abstract':row['abstract'],'source':'stored'}
    try:
        abstract,source=_candidate(dict(row))
    except (OSError, ValueError, error.HTTPError, ET.ParseError, json.JSONDecodeError) as exc:
        return {'status':'unavailable','paper_id':paper_id,'reason':'公开元数据暂时无法获取或不提供摘要', 'code':type(exc).__name__}
    abstract=' '.join(abstract.split())[:12000]
    if not abstract:
        return {'status':'unavailable','paper_id':paper_id,'reason':'公开元数据来源尚未提供摘要'}
    # Update only when still blank. Preserve existing human notes, fulltexts and flags.
    db.execute("UPDATE papers SET abstract=?,summary='',summary_fingerprint='',summary_model='' WHERE paper_id=? AND trim(coalesce(abstract,''))=''",(abstract,paper_id))
    return {'status':'updated','paper_id':paper_id,'abstract':abstract,'source':source}


def refresh_portal_abstract(db, paper_id: str) -> dict:
    if type(paper_id) is not str or not 1 <= len(paper_id) <= 150:
        raise ValueError('论文编号无效')
    row=db.execute('SELECT id AS paper_id,title,abstract,doi FROM papers WHERE id=?',(paper_id,)).fetchone()
    if row is None:
        raise ValueError('论文不存在')
    if str(row['abstract'] or '').strip():
        return {'status':'already_present','paper_id':paper_id,'abstract':row['abstract']}
    try:
        abstract,source=_candidate(dict(row))
    except (OSError, ValueError, error.HTTPError, ET.ParseError, json.JSONDecodeError) as exc:
        return {'status':'unavailable','paper_id':paper_id,'reason':'公开元数据暂时无法获取或不提供摘要','code':type(exc).__name__}
    abstract=' '.join(abstract.split())[:12000]
    if not abstract:
        return {'status':'unavailable','paper_id':paper_id,'reason':'公开元数据来源尚未提供摘要'}
    db.execute("UPDATE papers SET abstract=? WHERE id=? AND trim(coalesce(abstract,''))=''", (abstract,paper_id))
    return {'status':'updated','paper_id':paper_id,'abstract':abstract,'source':source}
