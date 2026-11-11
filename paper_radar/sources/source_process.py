# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Fixed subprocess entrypoint for an approved registered source."""
from __future__ import annotations
from dataclasses import asdict
from contextlib import redirect_stdout
import os
from datetime import datetime
import json
import sys
from urllib import request
from .transport_trace import observe_urllib


def _binary_pipe(direction: str):
    """Return child stdin/stdout even in PyInstaller --windowed builds.

    Windowed Windows executables can expose sys.stdin/sys.stdout as None
    although the parent provided anonymous PIPE handles. Prefer existing
    Python binary streams, then CRT descriptors, then a duplicated Win32
    standard handle. Never print a response through a locale text stream.
    """
    if direction not in ('in', 'out'):
        raise ValueError('Unknown pipe direction')
    number = 0 if direction == 'in' else 1
    stream = getattr(sys.stdin if direction == 'in' else sys.stdout, 'buffer', None)
    if stream is not None:
        return stream
    mode = 'rb' if direction == 'in' else 'wb'
    try:
        return os.fdopen(os.dup(number), mode, buffering=0)
    except OSError:
        if os.name != 'nt':
            raise
    # Pythonw and frozen GUI executables can lack CRT descriptors 0/1.
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
        wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
        wintypes.BOOL, wintypes.DWORD]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    process = kernel.GetCurrentProcess()
    original = kernel.GetStdHandle(-10 if direction == 'in' else -11)
    if not original or original == ctypes.c_void_p(-1).value:
        raise OSError('Windows child standard pipe handle is unavailable')
    copied = wintypes.HANDLE()
    if not kernel.DuplicateHandle(process, original, process, ctypes.byref(copied),
                                  0, False, 2):  # DUPLICATE_SAME_ACCESS
        raise OSError('Windows child pipe duplication failed')
    flags = os.O_BINARY | (os.O_RDONLY if direction == 'in' else os.O_WRONLY)
    try:
        fd = msvcrt.open_osfhandle(copied.value, flags)
    except Exception:
        kernel.CloseHandle(copied)
        raise
    return os.fdopen(fd, mode, buffering=0)


def main():
    """Return exactly one UTF-8 JSON envelope on the binary stdout pipe.

    Windows Python can use cp936/cp1252 for piped stdout. Paper titles,
    abstracts, and localized errors must never pass through that text codec.
    Adapter prints are discarded so a debugging print cannot corrupt the IPC.
    """
    protocol_input = _binary_pipe('in')
    protocol_output = _binary_pipe('out')
    transport = []
    with open(os.devnull, 'w', encoding='utf-8') as noise_sink:
        with redirect_stdout(noise_sink):
            try:
                message = json.loads(protocol_input.read(32769).decode('utf-8'))
                from .base import _REGISTRY
                from . import available_sources  # initialize the approved registry
                name = message['name']
                if name not in _REGISTRY:
                    raise ValueError('未知检索来源')
                config = message['config']
                if not isinstance(config, dict):
                    raise ValueError('检索参数格式无效')
                config['_source_wall_timeout_seconds'] = float(message.get('wall_timeout_seconds') or 0)
                now = datetime.fromisoformat(message['now']) if message.get('now') else None
                with observe_urllib(transport):
                    papers = _REGISTRY[name]().fetch(config, logger=lambda _: None, now=now)
                raw = {'ok': True, 'papers': [asdict(p) for p in papers[:500]],
                       'partial': bool(getattr(papers, 'partial', False)),
                       'warning_code': str(getattr(papers, 'warning_code', ''))[:64],
                       'stats': getattr(papers, 'stats', {})}
            except Exception as exc:
                # Never forward raw errors (publisher URLs can contain API keys).
                from .failures import code_for_exception, MESSAGES
                code = code_for_exception(exc)
                raw = {'ok': False, 'error_code': code, 'error': MESSAGES[code]}
    raw['protocol_version'] = 2
    raw['transport_trace'] = transport[:80]
    raw['proxy_configured'] = any(k.lower() in ('http', 'https', 'all')
                                  for k in request.getproxies())
    # Both the code-page-independent byte write and ASCII JSON escaping are
    # intentional. Never put raw JSON into sys.stdout.write on Windows.
    payload = json.dumps(raw, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    protocol_output.write(payload)
    protocol_output.flush()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
