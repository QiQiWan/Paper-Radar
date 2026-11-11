# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Fail-safe local configuration I/O for synced folders and Windows cloud files.

A file that exists can nevertheless be unreadable (Windows OneDrive placeholders,
sharing violations, invalid Win32 handles).  Degraded reads never alter it, and
writes refuse to replace a missing/corrupt/unreadable primary after a failed read.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from threading import RLock
import time

_IO_LOCK = RLock()
_STATES: dict[str, dict] = {}
_MAX_ATTEMPTS = 3
_RETRY_INTERVAL = 0.12


def backup_path(path: Path) -> Path:
    path = Path(path)
    return path.with_name(path.name + '.bak')


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _safe_error(exc: BaseException) -> str:
    # Avoid leaking a JSON document, tokens, paths to other workspaces, etc.
    if isinstance(exc, OSError):
        return f'{type(exc).__name__} (errno={exc.errno}, winerror={getattr(exc, "winerror", None)})'
    if isinstance(exc, json.JSONDecodeError):
        return f'JSON 格式错误（第 {exc.lineno} 行）'
    if isinstance(exc, UnicodeError):
        return 'UTF-8 编码错误'
    return type(exc).__name__


def _read_payload(path: Path) -> dict:
    # No exists() -> read_text() TOCTOU. FileNotFoundError is handled separately.
    with Path(path).open('r', encoding='utf-8-sig') as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError('配置文件顶层必须为 JSON 对象。')
    return data


def _read_with_retry(path: Path) -> dict:
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return _read_payload(path)
        except OSError:
            if attempt == _MAX_ATTEMPTS - 1:
                raise
            time.sleep(_RETRY_INTERVAL)


def _record(path: Path, state: str, detail: str = '') -> None:
    key = _key(path)
    entry = {'state': state, 'read_only': state != 'ok', 'message': detail}
    previous = _STATES.get(key)
    _STATES[key] = entry
    if state != 'ok' and previous != entry:
        print('[Paper-Radar] 配置读取异常：' + detail + '\n'
              f'  文件：{path}\n'
              '  设置文件已保留，请检查目录权限或查看运行日志。',
              file=sys.stderr, flush=True)


def config_health(path: Path) -> dict:
    """Public diagnostics only; no raw config values or model credentials."""
    with _IO_LOCK:
        return dict(_STATES.get(_key(path), {'state':'ok','read_only':False,'message':''}))


def _present(path: Path) -> bool:
    # Unlike Path.exists(), lexists detects dangling symlinks and other odd files.
    try:
        return os.path.lexists(os.fspath(path))
    except OSError:
        # Uncertain filesystem state is never interpreted as a fresh install.
        return True


def _safe_degraded_settings(value: dict) -> dict:
    # A fallback must never restore update/LLM permission implicitly. Existing
    # settings are still visible, but actions are blocked until recovery.
    return {**value, 'update_check_enabled': False,
            'auto_update_on_startup': False, 'ai_enabled': False,
            'ai_rerank_enabled': False, 'llm_data_consent': False}


def load_config(path: Path, defaults: dict, validator):
    path = Path(path)
    with _IO_LOCK:
        if not _present(path):
            backup = backup_path(path)
            if not _present(backup):
                # A mature installation can transiently lose the config while
                # OneDrive reconciles files. Do not recreate defaults over it.
                old_library = path.parent/'data'/'papers.sqlite3'
                if path.name == 'settings.json' and _present(old_library):
                    _record(path, 'defaults', '已有论文库但设置文件暂时不存在；请先从 OneDrive 或历史备份恢复设置。')
                    return _safe_degraded_settings(validator(dict(defaults)))
                _record(path, 'ok')
                return validator(dict(defaults))
            # A synced primary may briefly disappear during filesystem updates.
            # Do not mistake an existing backup for a first-time installation.
            try:
                data = _read_with_retry(backup)
                value = validator({**defaults, **{k:v for k,v in data.items() if k in defaults}})
                _record(path, 'backup', '主配置暂时不存在，已使用上次有效备份；保存设置和新任务已暂停。')
                return _safe_degraded_settings(value)
            except (OSError, ValueError, UnicodeError, TypeError):
                _record(path, 'defaults', '主配置文件不存在且备份不可读；当前临时默认配置不能保存。')
                return _safe_degraded_settings(validator(dict(defaults)))
        try:
            primary = _read_with_retry(path)
            value = validator({**defaults, **{k:v for k,v in primary.items() if k in defaults}})
            _record(path, 'ok')
            return value
        except (OSError, ValueError, UnicodeError, TypeError) as exc:
            primary_error = _safe_error(exc)
        backup = backup_path(path)
        if _present(backup):
            try:
                data = _read_with_retry(backup)
                value = validator({**defaults, **{k:v for k,v in data.items() if k in defaults}})
                _record(path, 'backup', f'主配置文件无法读取（{primary_error}），暂用上次有效配置；保存设置已暂停，请先修复 settings.json。')
                return _safe_degraded_settings(value)
            except (OSError, ValueError, UnicodeError, TypeError):
                pass
        _record(path, 'defaults', f'主配置文件无法读取（{primary_error}），未找到有效备份；当前为临时默认配置，保存设置和新任务已暂停。')
        return _safe_degraded_settings(validator(dict(defaults)))


def assert_primary_healthy(path: Path) -> None:
    """Prevent writes/jobs using a fallback workspace after a bad config load."""
    path = Path(path)
    with _IO_LOCK:
        if config_health(path)['read_only']:
            raise ValueError('配置文件读取异常：当前工作台处于只读恢复模式。请检查配置文件是否可读，修正后重启。')
        if _present(path):
            try:
                _read_with_retry(path)
            except (OSError, ValueError, UnicodeError, TypeError) as exc:
                _record(path, 'defaults', f'配置文件再次变得不可读（{_safe_error(exc)}）；写入已暂停。')
                raise ValueError('原设置文件无法读取，已阻止覆盖。请先修复 settings.json；研究数据仍保留。') from None


def save_config_json(path: Path, settings: dict) -> None:
    """Atomic write + last known good backup; never replace a damaged primary."""
    path = Path(path)
    serialized = json.dumps(settings, ensure_ascii=False, indent=2)
    with _IO_LOCK:
        assert_primary_healthy(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError('settings.json 是符号链接，已拒绝覆盖；请先修复路径。')
        primary_tmp = path.with_name(path.name + '.tmp')
        backup = backup_path(path)
        backup_tmp = backup.with_name(backup.name + '.tmp')
        try:
            primary_tmp.write_text(serialized, encoding='utf-8')
            os.replace(primary_tmp, path)
            # The backup contains a fully validated *new* snapshot, not the
            # previous stale settings. A failed backup never invalidates primary.
            try:
                backup_tmp.write_text(serialized, encoding='utf-8')
                os.replace(backup_tmp, backup)
            except OSError as exc:
                # Primary has already been committed. Do not report a failed
                # save or overwrite it again just because backup failed.
                print('[Paper-Radar] 已保存设置，但安全备份创建失败：'+_safe_error(exc),
                      file=sys.stderr, flush=True)
            _record(path, 'ok')
        finally:
            for temp in (primary_tmp, backup_tmp):
                try:
                    if temp.exists(): temp.unlink()
                except OSError:
                    pass


def initialize_fresh_workspace(path: Path, defaults: dict, validator) -> bool:
    """Persist defaults only on a provably empty first installation.

    Starting the HTTP dashboard creates the SQLite database. Without a
    persisted settings.json, a later refresh sees that database and treats
    the configuration as temporarily lost. This guards clean installation
    without weakening the existing protection for pre-existing research data.
    """
    path=Path(path)
    root=path.parent
    with _IO_LOCK:
        if _present(path) or _present(backup_path(path)):
            return False
        # The desktop process may have generated its own supervision key
        # before opening the server. It does not represent user research data.
        for name in ('data','downloads','reports','logs','.paper_radar_recovery'):
            folder=root/name
            if _present(folder):
                if not folder.is_dir() or folder.is_symlink():return False
                try:
                    existing=[item.name for item in folder.iterdir()]
                except OSError:
                    return False
                if name=='data':
                    existing=[x for x in existing if x!='.desktop_supervision.key']
                if name=='logs' and existing == ['retrieval']:
                    # Diagnostics can be created before the first HTTP start.
                    # A directory containing only our generated metadata logs
                    # does not prove a previous research workspace existed.
                    traces=folder/'retrieval'
                    if traces.is_symlink() or not traces.is_dir():return False
                    try:
                        from .sources.retrieval_trace import RUN_ID
                        entries=list(traces.iterdir())
                        if all((not x.is_symlink()) and x.is_file()
                               and x.suffix=='.jsonl' and RUN_ID.fullmatch(x.stem)
                               for x in entries):
                            existing=[]
                    except OSError:
                        return False
                if existing:return False
        # Both source and data directories are clean. Write before the first
        # automatic SQLite initialization, with the normal atomic safe writer.
        from .core import save_config
        save_config(validator(dict(defaults)),path)
        return True
