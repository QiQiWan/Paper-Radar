# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Resolve the writable research home independently of the installation.

For source installations there is no implicit migration: moving live SQLite
files behind the user's back risks lost WAL transactions and stale OneDrive
placeholders. The optional pointer is created only by the verified migration
utility. Environment override is useful for managed deployments and tests.
"""
from __future__ import annotations
import os
from pathlib import Path
import sys

INSTALL_ROOT = Path(__file__).resolve().parent.parent
POINTER_FILE = '.paper_radar_home'


def resolve_data_root(install_root: Path = INSTALL_ROOT, *, env=None,
                      frozen: bool | None = None, create: bool = True) -> Path:
    env = os.environ if env is None else env
    frozen = bool(getattr(sys, 'frozen', False)) if frozen is None else frozen
    install_root = Path(install_root).resolve()
    if frozen:
        from .desktop_paths import user_data_dir
        root = user_data_dir(env=env)
    else:
        override = str(env.get('PAPER_RADAR_USER_HOME') or '').strip()
        pointer = install_root / POINTER_FILE
        if override:
            root = Path(override).expanduser()
        elif pointer.is_file() and not pointer.is_symlink():
            raw = pointer.read_text(encoding='utf-8').strip()
            if not raw or '\x00' in raw or '\n' in raw or '\r' in raw:
                raise ValueError('数据目录指向文件无效，请运行存储诊断工具。')
            root = Path(raw).expanduser()
        else:
            # Existing source installations keep their original data paths.
            # A new Windows/macOS installation stores SQLite and configuration
            # in the per-user local application directory, not a synced code folder.
            legacy_files = (install_root / 'settings.json',
                            install_root / 'settings.json.bak',
                            install_root / 'data' / 'papers.sqlite3')
            if any(os.path.lexists(os.fspath(path)) for path in legacy_files):
                root = install_root
            elif sys.platform in ('win32', 'darwin'):
                from .desktop_paths import user_data_dir
                root = user_data_dir(platform=sys.platform, env=env)
            else:
                root = install_root
        if not root.is_absolute():
            raise ValueError('研究数据目录必须使用绝对路径。')
    if root.is_symlink():
        raise ValueError('研究数据目录不能是符号链接。')
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root.resolve()
