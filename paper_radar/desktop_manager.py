# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Small dependency-free desktop process controller and runtime monitor.

All commands are fixed. Never runs a user-provided shell command or binds a
public socket. The UI may attach to an existing healthy localhost instance;
only processes started by *this* controller can be stopped from it.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from .desktop_identity import read_or_create_secret, instance_fingerprint
import webbrowser
from . import __app_id__, __version__
from .core import APP_ROOT
from .desktop_paths import ensure_data_dir

PORTS = tuple(range(8765, 8777))


def read_health(port: int, *, timeout=.5):
    try:
        with urlopen(f'http://127.0.0.1:{port}/api/health', timeout=timeout) as response:
            value=json.loads(response.read(4096))
        if value.get('app')==__app_id__:
            return {'port':port,'version':str(value.get('version') or ''),
                    'desktop_instance':str(value.get('desktop_instance') or '')}
    except (OSError, ValueError, TimeoutError):pass
    return None


def read_runtime(port: int):
    """Read non-secret operational fields from localhost only."""
    try:
        with urlopen(f'http://127.0.0.1:{port}/api/state',timeout=.75) as response:
            result=json.loads(response.read(512_000))
        runtime=result.get('runtime') or {}
        last=result.get('last_run') or {}
        try:
            from math import isfinite
            percent=float((runtime.get('progress') or {}).get('percent') or 0)
            percent=max(0.0,min(100.0,percent)) if isfinite(percent) else 0.0
        except (TypeError,ValueError):percent=0.0
        return {'progress_percent':percent,
                'busy':bool(runtime.get('busy')),
                'phase':str(runtime.get('phase') or '')[:120],
                'job':str(runtime.get('job') or '')[:90],
                'last_run_state':str(last.get('state') or '')[:40],
                'last_run_finished':str(last.get('finished_at') or '')[:80]}
    except (OSError,ValueError,TimeoutError):
        return {}


def find_service(owner_instance: str | None = None):
    for port in PORTS:
        result=read_health(port)
        if result and (owner_instance is None or
                       result.get('desktop_instance')==owner_instance):
            return result
    return None


def free_port():
    for port in PORTS:
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',port))
            except OSError:continue
            return port
    return None


def backend_command(port: int):
    if getattr(sys,'frozen',False):
        # Windows GUI executables have no usable stdio streams. The sidecar
        # is a console-mode PyInstaller binary started with CREATE_NO_WINDOW;
        # it can correctly supervise PDF/source child pipes without a popup.
        service=Path(sys.executable).with_name('Paper-Radar-Service.exe')
        exe=str(service) if os.name=='nt' and service.is_file() else sys.executable
        return [exe,'--serve','--port',str(port)]
    return [sys.executable,'-m','paper_radar.desktop_app',
            '--serve','--port',str(port)]


def read_log(path: Path, max_bytes=6500):
    """Tail only a bounded amount of local text; never follow symlinks."""
    if path.is_symlink() or not path.is_file():return ''
    with path.open('rb') as file:
        file.seek(0,2)
        file.seek(max(0,file.tell()-max_bytes))
        return file.read(max_bytes).decode('utf-8',errors='replace')[-4500:]


def doctor():
    from .run_status import read_status
    secret=read_or_create_secret(APP_ROOT/'data')
    service=find_service(instance_fingerprint(secret))
    try:last_run=read_status()
    except (OSError,ValueError):last_run={}
    return {'version':__version__, 'data_dir':str(APP_ROOT),
            'service':service, 'last_run':last_run,
            'log_file':str(APP_ROOT/'logs'/'desktop_server.log')}


class Controller:
    def __init__(self, data_dir: Path=APP_ROOT):
        self.data_dir=ensure_data_dir(data_dir)
        # Frozen app's user data dir; inject the same identity into child via
        # APP_ROOT, which is derived independently by the backend.
        self.secret=read_or_create_secret(self.data_dir/'data')
        self.instance_id=instance_fingerprint(self.secret)
        self.process: subprocess.Popen | None=None
        self.port: int | None=None
        self.log=self.data_dir/'logs'/'desktop_server.log'
        self.log.parent.mkdir(parents=True,exist_ok=True)

    def start(self):
        active=find_service(self.instance_id)
        if active:
            self.port=active['port']
            return {'state':'attached','port':self.port, 'version':active['version']}
        port=free_port()
        if port is None:return {'state':'error','message':'本机 8765—8776 端口均不可用'}
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0
        with self.log.open('a',encoding='utf-8') as output:
            self.process=subprocess.Popen(backend_command(port),stdin=subprocess.DEVNULL,
                 stdout=output,stderr=subprocess.STDOUT,close_fds=True,
                 creationflags=flags,cwd=str(self.data_dir),
                 start_new_session=os.name!='nt')
        self.port=port
        return {'state':'starting','port':port}

    def status(self):
        if self.process is not None and self.process.poll() is not None:
            code=self.process.returncode
            self.process=None
            return {'state':'stopped','exit_code':code, 'port':self.port}
        if self.port is not None:
            online=read_health(self.port)
            if online:return {'state':'running',**online, 'owned':self.process is not None}
        online=find_service(self.instance_id)
        if online:
            self.port=online['port']
            return {'state':'running',**online, 'owned':False}
        return {'state':'starting' if self.process else 'stopped','port':self.port}

    def stop(self):
        info=self.status()
        if info.get('state')!='running' and self.process is None:
            return False
        # The same user may reopen the manager while the service still runs.
        # Prefer an authenticated graceful shutdown, which refuses busy jobs.
        if self.port is not None and info.get('state')=='running':
            try:
                req=Request(f'http://127.0.0.1:{self.port}/api/desktop/stop',
                    data=b'{}',headers={'X-Desktop-Supervision-Key':self.secret,
                    'Content-Type':'application/json'},method='POST')
                with urlopen(req,timeout=2) as response:
                    if response.status!=200:return False
            except (OSError,ValueError,TimeoutError,HTTPError):
                return False
            if self.process:
                try:self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:return False
            old_port=self.port
            self.process=None
            self.port=None
            # Do not claim a restart while the old socket is still bound.
            for _ in range(30):
                if read_health(old_port) is None:break
                time.sleep(.1)
            return read_health(old_port) is None
        if self.process is not None and self.process.poll() is None:
            # When an incomplete startup cannot accept HTTP yet, only the
            # exact process created by this manager can be terminated.
            self.process.terminate()
            try:self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait(timeout=4)
            self.process=None;self.port=None
            return True
        return False

    def open_browser(self):
        status=self.status()
        if status.get('state')!='running':return False
        return bool(webbrowser.open(f'http://127.0.0.1:{status["port"]}/'))


def main():
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        print('当前系统缺少图形组件 Tcl/Tk；请使用平台原生安装包。',file=sys.stderr)
        return 2
    controller=Controller()
    root=tk.Tk()
    root.title(f'Paper-Radar {__version__} · 运行管理')
    # Ordinary users see service status and one primary action. Operational
    # details remain available without occupying most of the window.
    root.geometry('680x365')
    root.minsize(560,330)
    root.configure(bg='#f5f8fb')
    style=ttk.Style(root)
    try:style.theme_use('clam')
    except tk.TclError:pass
    style.configure('TFrame',background='#f5f8fb')
    style.configure('TLabel',background='#f5f8fb',foreground='#19384a',font=('Arial',10))
    style.configure('TButton',padding=(13,9),background='#e9f1f5',foreground='#19384a',font=('Arial',10))
    style.map('TButton',background=[('active','#d8e7ed')])
    style.configure('Primary.TButton',padding=(16,10),background='#0d6178',foreground='#ffffff',font=('Arial',11,'bold'))
    style.map('Primary.TButton',background=[('active','#0e7890')],foreground=[('active','#ffffff')])
    frame=ttk.Frame(root,padding=25);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='Paper-Radar',font=('Arial',19,'bold')).pack(anchor='w')
    ttk.Label(frame,text='本地科研工作台',font=('Arial',10)).pack(anchor='w',pady=(2,14))
    state=tk.StringVar(value='正在检查服务…')
    ttk.Label(frame,textvariable=state,font=('Arial',11,'bold')).pack(anchor='w',pady=(0,11))
    def do_start():
        result=controller.start()
        if result['state']=='error':messagebox.showerror('启动失败',result['message'])
        update()
    def do_stop():
        if not controller.stop():messagebox.showinfo('运行管理','服务仍在运行。可能有任务正在处理，请查看日志。')
        update()
    def do_restart():
        if controller.status().get('state')=='running' and not controller.stop():
            messagebox.showinfo('运行管理','当前任务可能仍在运行，暂时无法重启。')
            return
        do_start()
    buttons=ttk.Frame(frame);buttons.pack(fill='x',pady=(0,14))
    ttk.Button(buttons,text='打开研究工作台',style='Primary.TButton',command=lambda: controller.open_browser() or
               messagebox.showinfo('服务未就绪','请先启动服务，状态显示为运行正常后再打开。')).pack(side='left',padx=(0,12))
    ttk.Button(buttons,text='启动',command=do_start).pack(side='left',padx=(0,6))
    ttk.Button(buttons,text='重启',command=do_restart).pack(side='left',padx=(0,6))
    ttk.Button(buttons,text='停止',command=do_stop).pack(side='left')
    operation=tk.StringVar(value='正在检查科研任务…')
    ttk.Label(frame,textvariable=operation,wraplength=620).pack(anchor='w',pady=(0,8))
    progress_value=tk.DoubleVar(value=0)
    style.configure('Horizontal.TProgressbar',background='#0d6178',troughcolor='#e2eaf0')
    bar=ttk.Progressbar(frame,mode='determinate',maximum=100,variable=progress_value)
    bar.pack(fill='x',pady=(0,14))
    # A compact pair of disclosure controls replaces the permanent log pane.
    controls=ttk.Frame(frame);controls.pack(fill='x',pady=(0,7))
    detail_visible=[False]; log_visible=[False]
    details=tk.StringVar(value='')
    path_label=ttk.Label(frame,textvariable=details,wraplength=610)
    log_frame=ttk.Frame(frame)
    log_heading=ttk.Label(log_frame,text='最近运行日志',font=('Arial',11,'bold'))
    log_heading.pack(anchor='w',pady=(0,7))
    scroll=ttk.Frame(log_frame);scroll.pack(fill='both',expand=True)
    textbox=tk.Text(scroll,height=11,wrap='word',font=('Courier New',10),state='disabled',
                    bg='#ffffff',fg='#213c4b',relief='flat',borderwidth=0,
                    highlightthickness=1,highlightbackground='#d7e2e9',padx=10,pady=9)
    scrollbar=ttk.Scrollbar(scroll,command=textbox.yview)
    textbox.configure(yscrollcommand=scrollbar.set)
    textbox.pack(side='left',fill='both',expand=True)
    scrollbar.pack(side='right',fill='y')
    def toggle_logs():
        log_visible[0]=not log_visible[0]
        if log_visible[0]:
            log_frame.pack(fill='both',expand=True,pady=(7,0))
            root.geometry('680x610')
        else:
            log_frame.pack_forget()
            root.geometry('680x365')
        log_button.configure(text='收起日志' if log_visible[0] else '查看运行日志')
    def toggle_data():
        detail_visible[0]=not detail_visible[0]
        if detail_visible[0]:path_label.pack(fill='x',anchor='w',pady=(5,0))
        else:path_label.pack_forget()
        data_button.configure(text='收起存储位置' if detail_visible[0] else '查看数据位置')
    log_button=ttk.Button(controls,text='查看运行日志',command=toggle_logs)
    log_button.pack(side='left',padx=(0,8))
    data_button=ttk.Button(controls,text='查看数据位置',command=toggle_data)
    data_button.pack(side='left')
    last_log=''
    auto_open=[True]
    def update():
        nonlocal last_log
        info=controller.status()
        names={'running':'运行正常','starting':'正在启动','stopped':'未运行'}
        state.set(f'● {names.get(info["state"],info["state"])}   '
                  f'端口：{info.get("port") or "—"}   版本：{info.get("version",__version__)}'
                  +('（由其他窗口管理）' if info['state']=='running' and not info.get('owned') else ''))
        details.set(f'个人数据目录：{controller.data_dir}\n运行日志：{controller.log}')
        if info['state']=='running':
            if auto_open[0]:
                auto_open[0]=False
                # Double-click should land the user in the actual workspace.
                controller.open_browser()
            running=read_runtime(info['port'])
            if running:
                phase=running['phase'] or running['job'] or '空闲'
                progress_value.set(running['progress_percent'] if running['busy'] else 0)
                operation.set(('科研任务执行中' if running['busy'] else '科研任务空闲')+
                              (f' · 进度 {running["progress_percent"]:.0f}%' if running['busy'] else '')+
                              f' · 当前阶段：{phase} · 最近任务：{running["last_run_state"] or "无"}')
            else:operation.set('服务正在运行；详细任务状态暂不可用')
        else:
            progress_value.set(0)
            operation.set('本地科研服务未启动')
        value=read_log(controller.log)
        if value != last_log:
            textbox.config(state='normal');textbox.delete('1.0','end')
            textbox.insert('1.0',value or '暂无日志，点击“启动服务”开始使用。')
            textbox.see('end');textbox.config(state='disabled');last_log=value
    def tick():
        update();root.after(2000,tick)
    def on_close():
        # Closing the manager leaves research tasks untouched. The next
        # manager instance can safely attach and stop its own local service.
        # The server's idle timeout still handles abandoned instances.
        root.destroy()
    root.protocol('WM_DELETE_WINDOW',on_close)
    root.after(300,do_start)
    root.after(500,tick)
    root.mainloop()
    return 0
