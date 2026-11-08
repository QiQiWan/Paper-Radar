# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Stable, user-actionable storage diagnostics without leaking raw SQL/errors."""
from __future__ import annotations
from pathlib import Path
import os
import sqlite3
from .migrations import MigrationError


def is_storage_exception(exc: BaseException) -> bool:
    if isinstance(exc, (sqlite3.DatabaseError, MigrationError)):
        return True
    if isinstance(exc, (OSError, PermissionError)):
        return True
    return False


def storage_failure(exc: BaseException, location: Path) -> dict:
    raw = str(exc).casefold()
    code = 'DATABASE_UNAVAILABLE'
    if isinstance(exc, MigrationError):
        code = 'SQLITE_SCHEMA_INCOMPATIBLE'
        reason = '数据库版本与当前程序不兼容。请检查安装版本，勿使用旧版覆盖或降级数据库。'
    elif 'i/o' in raw or 'disk' in raw or str(getattr(exc, 'sqlite_errorname', '')).startswith('SQLITE_IOERR'):
        code = 'SQLITE_IO_ERROR'
        reason = '论文库读写失败。请检查同步目录、磁盘空间及文件占用。'
    elif 'locked' in raw or 'busy' in raw:
        code = 'SQLITE_BUSY'
        reason = '论文库正被其他进程占用。请关闭重复运行的程序后重试。'
    elif 'readonly' in raw or 'read-only' in raw or isinstance(exc, PermissionError):
        code = 'SQLITE_READONLY'
        reason = '论文库无法写入。请检查文件权限或只读属性。'
    elif 'malformed' in raw or 'corrupt' in raw or 'not a database' in raw:
        code = 'SQLITE_CORRUPT'
        reason = '论文库文件可能损坏。先备份原件，再执行存储检查。'
    else:
        reason = '论文库暂时不可用。请运行存储诊断并查看本地日志。'
    root = Path(location)
    return {'ok':False,'code':code,'message':reason,
            'onedrive': 'onedrive' in str(root).casefold(),
            'action':'请检查本机数据目录的可写权限和剩余空间，关闭重复启动的程序后重试。'}


def healthy(location: Path) -> dict:
    return {'ok':True,'code':'OK','message':'论文库连接正常',
            'onedrive':'onedrive' in str(location).casefold()}
