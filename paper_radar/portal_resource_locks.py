# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Cross-process host-local serialization for externally rate-limited sources."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import os
import time

@contextmanager
def exclusive_file_lock(path:Path, *, cancelled=None, timeout=120):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.is_symlink():raise RuntimeError('Unsafe source lock')
    deadline=time.monotonic()+timeout
    with path.open('a+b') as f:
        if os.name=='nt':
            import msvcrt
            if path.stat().st_size==0:f.write(b'0');f.flush()
            while True:
                try:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:
                    if cancelled and cancelled():raise InterruptedError('Cancelled waiting for source lock')
                    if time.monotonic()>=deadline:raise TimeoutError('Source lock timeout')
                    time.sleep(.2)
            try:yield
            finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            while True:
                try:fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if cancelled and cancelled():raise InterruptedError('Cancelled waiting for source lock')
                    if time.monotonic()>=deadline:raise TimeoutError('Source lock timeout')
                    time.sleep(.2)
            try:yield
            finally:fcntl.flock(f.fileno(),fcntl.LOCK_UN)
