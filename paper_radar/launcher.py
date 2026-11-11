# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Open the local browser workspace without leaving a console window running."""
from __future__ import annotations
from pathlib import Path
import json
import os
import subprocess
import sys
import time
from urllib.request import urlopen
from . import __app_id__
import webbrowser

BASE_PORT = 8765


def health(port: int) -> bool:
    try:
        with urlopen(f'http://127.0.0.1:{port}/api/health',timeout=.6) as response:
            data=json.loads(response.read().decode('utf-8'))
            return data.get('app') == __app_id__
    except Exception:
        return False


def main():
    root=Path(__file__).resolve().parent.parent
    from .core import APP_ROOT
    # A local port can be used by another account. Only attach to an
    # installation carrying our private per-user service fingerprint.
    from .desktop_identity import read_or_create_secret, instance_fingerprint
    from .desktop_manager import read_health
    owner=instance_fingerprint(read_or_create_secret(APP_ROOT/'data'))
    selected=None
    for port in range(BASE_PORT,BASE_PORT+12):
        online=read_health(port)
        if online and online.get('desktop_instance')==owner:
            from . import __version__
            if online.get('version') != __version__:
                raise RuntimeError('正在运行的 Paper-Radar 版本与当前程序不同。请安全关闭旧版服务后重新启动；已保存的数据不会被删除。')
            webbrowser.open(f'http://127.0.0.1:{port}')
            print('已打开当前用户的 Paper-Radar 工作台')
            return
        if online:  # Foreign-account Paper-Radar: never attach or reuse port.
            continue
        import socket
        with socket.socket() as test:
            test.settimeout(.3)
            if test.connect_ex(('127.0.0.1',port))!=0:
                selected=port
                break
    if selected is None:
        raise RuntimeError('本地可用端口不足。请先关闭占用 8765—8776 端口的程序。')
    log_path=APP_ROOT/'logs'/'web_server.log'
    log_path.parent.mkdir(parents=True,exist_ok=True)
    exe=Path(sys.executable)
    if os.name=='nt':
        pythonw=exe.with_name('pythonw.exe')
        if pythonw.exists():exe=pythonw
    with log_path.open('a',encoding='utf-8') as log:
        subprocess.Popen([str(exe),str(root/'main.py'),'--serve','--port',str(selected)],
                         cwd=str(root), stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                         close_fds=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0,
                         start_new_session=os.name!='nt')
    for _ in range(30):
        online=read_health(selected)
        if online and online.get('desktop_instance')==owner:
            webbrowser.open(f'http://127.0.0.1:{selected}')
            print('Paper-Radar 已打开：'+f'http://127.0.0.1:{selected}')
            return
        time.sleep(.25)
    raise RuntimeError('本地页面未启动。请查看 logs/web_server.log。')
