# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Private, per-user authorization for local desktop service supervision.

The identity is scoped to a writable user profile, never bundled into an
installer or exposed in /api/health. The health response carries only a
one-way, non-authorizing fingerprint; shutdown requires the original secret.
"""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import secrets
import time


def read_or_create_secret(data_dir: Path) -> str:
    root=Path(data_dir)
    if root.is_symlink():
        raise ValueError('Refusing symlinked desktop data directory')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    name=root/'.desktop_supervision.key'
    if name.is_symlink():
        raise ValueError('Refusing symlinked desktop supervision key')
    try:
        flags=os.O_CREAT|os.O_EXCL|os.O_WRONLY
        flags |= getattr(os,'O_NOFOLLOW',0)
        fd=os.open(str(name),flags,0o600)
    except FileExistsError:
        pass
    else:
        try:
            with os.fdopen(fd,'w',encoding='ascii') as stream:
                stream.write(secrets.token_urlsafe(40))
        except BaseException:
            name.unlink(missing_ok=True)
            raise
    # A second double-click can race the first process between O_EXCL and
    # writing the secret. Wait briefly for the first writer to finish.
    key=''
    for _ in range(25):
        if name.is_symlink():raise ValueError('Refusing symlinked desktop supervision key')
        try:key=name.read_text(encoding='ascii').strip()
        except FileNotFoundError:key=''
        if 40<=len(key)<=256:break
        time.sleep(.02)
    if not 40<=len(key)<=256:
        raise ValueError('Invalid desktop supervision key')
    if os.name!='nt':
        mode=name.stat().st_mode & 0o777
        if mode & 0o077:
            name.chmod(0o600)
    return key


def instance_fingerprint(secret: str) -> str:
    return sha256(('paper-radar-desktop-v1:'+secret).encode('ascii')).hexdigest()[:24]
