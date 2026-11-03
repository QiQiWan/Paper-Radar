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
    selected=None
    for port in range(BASE_PORT,BASE_PORT+12):
        if health(port):
            webbrowser.open(f'http://127.0.0.1:{port}')
            print('已打开 Paper-Radar 工作台')
            return
        import socket
        with socket.socket() as test:
            test.settimeout(.3)
            if test.connect_ex(('127.0.0.1',port))!=0:
                selected=port
                break
    if selected is None:
        raise RuntimeError('本地可用端口不足。请先关闭占用 8765—8776 端口的程序。')
    log_path=root/'logs'/'web_server.log'
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
        if health(selected):
            webbrowser.open(f'http://127.0.0.1:{selected}')
            print('Paper-Radar 已打开：'+f'http://127.0.0.1:{selected}')
            return
        time.sleep(.25)
    raise RuntimeError('本地页面未启动。请查看 logs/web_server.log。')
