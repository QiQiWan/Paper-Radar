# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Writable per-user desktop state, independent of read-only application files."""
from __future__ import annotations
import os
from pathlib import Path
import sys


def user_data_dir(*, platform: str | None = None, env=None, home: Path | None = None) -> Path:
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else Path(home)
    override = env.get('PAPER_RADAR_USER_HOME', '')
    if override:
        location=Path(override).expanduser()
        if not location.is_absolute():
            raise ValueError('PAPER_RADAR_USER_HOME must be an absolute directory')
        return location.resolve()
    if platform=='win32':
        base = Path(env.get('LOCALAPPDATA') or env.get('APPDATA') or home/'AppData'/'Local')
        return (base/'Paper-Radar').resolve()
    if platform=='darwin':
        return (home/'Library'/'Application Support'/'Paper-Radar').resolve()
    return (Path(env.get('XDG_DATA_HOME') or home/'.local'/'share')/'paper-radar').resolve()


def ensure_data_dir(path: Path) -> Path:
    path=Path(path).expanduser()
    # Reject symlink directories even if the final resolved target looks valid.
    if path.is_symlink():raise ValueError('Refusing symlinked application data directory')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name!='nt':path.chmod(0o700)
    return path.resolve()
