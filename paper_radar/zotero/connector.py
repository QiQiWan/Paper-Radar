# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Select Zotero local API or read-only SQLite, with predictable fallback."""
from __future__ import annotations
from pathlib import Path
import os
import sys

from .models import Collection, Reference
from . import local_api, sqlite_reader


class ZoteroConnectionError(RuntimeError):
    pass


def detect_database() -> str:
    home=Path.home()
    options=[home/'Zotero'/'zotero.sqlite']
    if sys.platform=='win32':
        user=Path(os.environ.get('USERPROFILE',str(home)))
        options.extend([user/'Zotero'/'zotero.sqlite',
                        Path(os.environ.get('APPDATA',str(user/'AppData'/'Roaming')))/'Zotero'/'zotero.sqlite'])
    elif sys.platform=='darwin':
        options.append(home/'Library'/'Application Support'/'Zotero'/'Profiles'/'zotero.sqlite')
    else:
        options.append(home/'.zotero'/'zotero.sqlite')
    for p in options:
        if p.is_file():return str(p)
    return ''


class ZoteroReader:
    def __init__(self, mode='auto', database_path=''):
        if mode not in {'auto','api','sqlite'}:
            raise ZoteroConnectionError('请选择有效的 Zotero 连接方式。')
        self.mode=mode
        self.database_path=str(database_path or '').strip()
        self.backend=''
        self.resolved_path=''

    def _select(self):
        if self.mode=='sqlite' or (self.mode=='auto' and self.database_path):
            path=self.database_path or detect_database()
            if not path:raise ZoteroConnectionError('请填写 Zotero 数据库路径。')
            return 'sqlite',path
        return 'api',''

    def _read(self, kind, *args, **kwargs):
        mode,path=self._select()
        if mode=='sqlite':
            self.backend='sqlite';self.resolved_path=str(sqlite_reader.normalize_db_path(path))
            return getattr(sqlite_reader,kind)(self.resolved_path,*args,**kwargs)
        try:
            value=getattr(local_api,kind)(*args,**kwargs)
            self.backend='api';self.resolved_path=''
            return value
        except local_api.LocalAPIError as exc:
            if self.mode=='api':raise ZoteroConnectionError(str(exc)) from exc
            fallback=detect_database()
            if not fallback:
                raise ZoteroConnectionError(str(exc)) from exc
            try:
                self.backend='sqlite';self.resolved_path=fallback
                return getattr(sqlite_reader,kind)(fallback,*args,**kwargs)
            except sqlite_reader.ZoteroDatabaseError as fallback_error:
                raise ZoteroConnectionError(str(exc)+'；数据库读取也失败：'+str(fallback_error)) from fallback_error

    def collections(self)->list[Collection]:
        return self._read('list_collections')

    def references(self,key,include_children=True,limit=800)->tuple[list[Reference],int]:
        return self._read('get_references',key,include_children,limit)
