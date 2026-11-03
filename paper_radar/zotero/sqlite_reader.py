# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Read-only adapter for Zotero's private SQLite schema.

Zotero recommends its Local API. This adapter is an offline fallback; it NEVER
modifies the SQLite file or opens the database in write mode.
"""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3
from urllib.parse import quote

from .models import Collection, Reference


class ZoteroDatabaseError(RuntimeError):
    pass


def normalize_db_path(raw: str) -> Path:
    if not raw or len(raw) > 2000:
        raise ZoteroDatabaseError('请选择 Zotero 数据目录或 zotero.sqlite 文件。')
    p = Path(raw).expanduser()
    if p.is_dir():
        p = p / 'zotero.sqlite'
    if p.name.lower() != 'zotero.sqlite' or not p.is_file():
        raise ZoteroDatabaseError('未找到 zotero.sqlite，请选择 Zotero 的数据目录。')
    return p.resolve()


@contextmanager
def read_only_database(raw: str):
    p = normalize_db_path(raw)
    # SQLite URI ro=read-only; PRAGMA query_only provides a second write guard.
    uri = p.as_uri() + '?mode=ro'
    try:
        con = sqlite3.connect(uri, uri=True, timeout=3)
    except sqlite3.Error as exc:
        raise ZoteroDatabaseError(f'无法读取 Zotero 数据库：{exc}') from exc
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA query_only = ON')
        present = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {'collections','collectionItems','items','itemData','itemDataValues','fields'}
        if not required.issubset(present):
            raise ZoteroDatabaseError('当前数据库缺少 Zotero 文献表，请确认选择的是 Zotero 数据目录。')
        yield con, present
    except sqlite3.Error as exc:
        raise ZoteroDatabaseError(f'无法解析 Zotero 数据库：{exc}') from exc
    finally:
        con.close()


def _collection_rows(conn: sqlite3.Connection, tables: set[str]):
    deleted = 'AND dc.collectionID IS NULL' if 'deletedCollections' in tables else ''
    join = 'LEFT JOIN deletedCollections dc ON dc.collectionID=c.collectionID' if deleted else ''
    return conn.execute(f'''SELECT c.collectionID,c.collectionName,c.parentCollectionID,c.key,c.libraryID
          FROM collections c {join} WHERE 1=1 {deleted} ORDER BY c.libraryID,c.collectionID''').fetchall()


def list_collections(db_path: str) -> list[Collection]:
    with read_only_database(db_path) as (conn, tables):
        rows = _collection_rows(conn, tables)
    ids = {int(row['collectionID']):str(row['key']) for row in rows}
    names = {int(row['collectionID']):str(row['collectionName']) for row in rows}
    parents = {int(row['collectionID']):row['parentCollectionID'] for row in rows}
    results = []
    for row in rows:
        cid = int(row['collectionID'])
        hierarchy = [names[cid]]
        parent = parents.get(cid)
        visited = {cid}
        while parent is not None and parent not in visited and parent in names:
            visited.add(parent)
            hierarchy.insert(0, names[parent])
            parent = parents.get(parent)
        results.append(Collection(str(row['key']),str(row['collectionName']),
                                  ids.get(row['parentCollectionID'], '') if row['parentCollectionID'] is not None else '',
                                  str(row['libraryID']), ' / '.join(hierarchy)))
    return results


def _collection_ids(conn, tables, key: str, include_children: bool) -> list[int]:
    rows = _collection_rows(conn, tables)
    root = next((r for r in rows if r['key'] == key), None)
    if root is None:
        raise ZoteroDatabaseError('所选收藏夹不存在，请刷新文件夹列表。')
    ids = [int(root['collectionID'])]
    if include_children:
        changed = True
        while changed:
            changed = False
            for row in rows:
                if row['parentCollectionID'] in ids and int(row['collectionID']) not in ids:
                    ids.append(int(row['collectionID']))
                    changed = True
    return ids


def get_references(db_path: str, collection_key: str, include_children=True, limit=800) -> tuple[list[Reference],int]:
    if not collection_key or len(collection_key) > 64:
        raise ZoteroDatabaseError('请选择一个 Zotero 收藏夹。')
    limit = max(1, min(int(limit), 1500))
    with read_only_database(db_path) as (conn, tables):
        ids = _collection_ids(conn, tables, collection_key, include_children)
        placeholders = ','.join('?' for _ in ids)
        # Ignore notes and attachments using itemTypes, if the table is present.
        type_join = 'LEFT JOIN itemTypes ty ON ty.itemTypeID=i.itemTypeID' if 'itemTypes' in tables else ''
        type_filter = "AND (ty.typeName IS NULL OR ty.typeName NOT IN ('note','attachment','annotation'))" if type_join else ''
        deleted_join = 'LEFT JOIN deletedItems di ON di.itemID=i.itemID' if 'deletedItems' in tables else ''
        deleted_filter = 'AND di.itemID IS NULL' if deleted_join else ''
        sql = f'''SELECT DISTINCT i.itemID,i.key
             FROM collectionItems ci JOIN items i ON i.itemID=ci.itemID
             {type_join} {deleted_join}
             WHERE ci.collectionID IN ({placeholders}) {type_filter} {deleted_filter}
             ORDER BY i.itemID DESC LIMIT ?'''
        rows = conn.execute(sql, (*ids, limit)).fetchall()
        item_ids = [int(r['itemID']) for r in rows]
        if not item_ids:
            return [], 0
        ip = ','.join('?' for _ in item_ids)
        fields: dict[int,dict[str,str]] = {i:{} for i in item_ids}
        for r in conn.execute(f'''SELECT d.itemID,f.fieldName,v.value FROM itemData d
               JOIN fields f ON f.fieldID=d.fieldID JOIN itemDataValues v ON v.valueID=d.valueID
               WHERE d.itemID IN ({ip})''', item_ids):
            if r['fieldName']:
                val = r['value']
                fields[int(r['itemID'])][str(r['fieldName'])] = str(val or '')
        tagmap: dict[int,list[str]] = {i:[] for i in item_ids}
        if {'itemTags','tags'}.issubset(tables):
            for r in conn.execute(f'''SELECT it.itemID,t.name FROM itemTags it
                   JOIN tags t ON t.tagID=it.tagID WHERE it.itemID IN ({ip})''', item_ids):
                if r['name']:tagmap[int(r['itemID'])].append(str(r['name']))
        authors: dict[int,list[str]] = {i:[] for i in item_ids}
        if {'itemCreators','creators'}.issubset(tables):
            for r in conn.execute(f'''SELECT ic.itemID,c.firstName,c.lastName
                   FROM itemCreators ic JOIN creators c ON c.creatorID=ic.creatorID
                   WHERE ic.itemID IN ({ip}) ORDER BY ic.itemID,ic.orderIndex''', item_ids):
                name = ' '.join(x for x in (r['firstName'],r['lastName']) if x)
                if name:authors[int(r['itemID'])].append(name)
        results = []
        for row in rows:
            i = int(row['itemID'])
            data = fields[i]
            title = data.get('title','').strip()
            if not title:continue
            results.append(Reference(str(row['key']),title,data.get('abstractNote',''),
                      tagmap[i],', '.join(authors[i]),data.get('date',''),data.get('DOI','')))
        return results, len(rows)
