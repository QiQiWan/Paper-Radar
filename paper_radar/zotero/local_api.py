# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Zotero desktop's read-only Local API, bound to the fixed loopback address."""
from __future__ import annotations
import json
from urllib import request, error, parse

from .models import Collection, Reference

BASE = 'http://127.0.0.1:23119/api/users/0'
HEADERS = {'Zotero-API-Version':'3','Accept':'application/json',
           'User-Agent':'Paper-Radar/1.6 (local Zotero reader)'}


class LocalAPIError(RuntimeError):
    pass


def _fetch(path: str, params: dict | None = None, opener=None):
    if not path.startswith('/') or '://' in path or '..' in path:
        raise LocalAPIError('Zotero API 路径无效')
    uri = BASE + path
    if params:
        uri += '?' + parse.urlencode(params)
    req = request.Request(uri, headers=HEADERS)
    try:
        # This URL is a fixed localhost endpoint; no credential needed for GET.
        with (opener or request.urlopen)(req, timeout=4) as resp:
            payload = json.load(resp)
    except error.HTTPError as exc:
        if exc.code == 403:
            raise LocalAPIError('请在 Zotero 设置 > 高级中允许本机其他程序访问 Zotero。') from exc
        raise LocalAPIError(f'Zotero 本地接口返回 HTTP {exc.code}') from exc
    except (error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        raise LocalAPIError('无法连接 Zotero。请打开 Zotero 并开启本地 API，或选择本地数据库文件。') from exc
    if not isinstance(payload, list):
        raise LocalAPIError('Zotero 本地接口返回的数据格式不正确。')
    return payload


def list_collections() -> list[Collection]:
    rows = _fetch('/collections')
    values = {}
    for item in rows:
        if not isinstance(item,dict):continue
        data = item.get('data') or {}
        key = str(item.get('key') or data.get('key') or '')
        if not key:continue
        values[key] = (str(data.get('name') or ''),str(data.get('parentCollection') or ''))
    result=[]
    for key,(name,parent) in values.items():
        segments=[name];seen={key};p=parent
        while p and p in values and p not in seen:
            seen.add(p)
            segments.insert(0,values[p][0])
            p=values[p][1]
        result.append(Collection(key,name,parent,'user',' / '.join(segments)))
    return sorted(result,key=lambda x:x.path.casefold())


def _collection_tree(collections, selected, include_children):
    mapping={c.key:c for c in collections}
    if selected not in mapping:
        raise LocalAPIError('所选收藏夹不存在，请重新选择。')
    keys=[selected]
    if include_children:
        changed=True
        while changed:
            changed=False
            for c in collections:
                if c.parent_key in keys and c.key not in keys:
                    keys.append(c.key);changed=True
    return keys


def get_references(collection_key: str, include_children=True, limit=800) -> tuple[list[Reference],int]:
    if not collection_key or len(collection_key)>64:
        raise LocalAPIError('请选择一个 Zotero 收藏夹。')
    collections=list_collections()
    keys = _collection_tree(collections,collection_key,include_children)
    limit=max(1,min(int(limit),1500))
    papers={}
    total=0
    for key in keys:
        for offset in range(0,limit,100):
            rows=_fetch('/collections/'+parse.quote(key,safe='')+'/items',
                        {'start':offset,'limit':min(100,limit-offset)})
            if not rows:break
            total+=len(rows)
            for item in rows:
                if not isinstance(item,dict):continue
                data=item.get('data') or {}
                if data.get('itemType') in ('attachment','note','annotation'):continue
                ident=str(item.get('key') or data.get('key') or '')
                title=str(data.get('title') or '').strip()
                if not ident or not title:continue
                tag_list=[str(tag['tag']) for tag in (data.get('tags') or [])
                          if isinstance(tag,dict) and tag.get('tag')]
                creators=[]
                for c in data.get('creators') or []:
                    if not isinstance(c,dict):continue
                    person=c.get('name') or ' '.join(str(c.get(k) or '') for k in ('firstName','lastName')).strip()
                    if person:creators.append(str(person))
                papers[ident]=Reference(ident,title,str(data.get('abstractNote') or ''),tag_list,
                                        ', '.join(creators),str(data.get('date') or ''),str(data.get('DOI') or ''))
            if len(rows)<min(100,limit-offset):break
        if len(papers)>=limit:break
    return list(papers.values())[:limit],total
