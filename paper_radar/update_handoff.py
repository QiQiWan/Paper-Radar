# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Explicit, same-user handoff for installing a verified source Release.

The web server never replaces its own loaded code. On a user-confirmed POST
it starts this stdlib-only helper, which drains and shuts down the user's
localhost services, invokes the existing signed/hashed updater, and restarts.
Public/systemd services and another user's local instance are never killed.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from .desktop_identity import instance_fingerprint, read_or_create_secret
from .startup_update import StartupUpdater, atomic_json, now_iso

ROOT = Path(__file__).resolve().parent.parent
HANDOFF_TIMEOUT = 300
RESTART_TIMEOUT = 25


def handoff_root(root: Path = ROOT) -> Path:
    return Path(root) / '.paper_radar_updates'


def state_path(root: Path = ROOT) -> Path:
    return handoff_root(root) / 'handoff_status.json'


def state(root: Path = ROOT) -> dict:
    try:
        value = json.loads(state_path(root).read_text(encoding='utf-8'))
        return {key:value.get(key) for key in ('state','message','at','target_version','installed_version') if key in value} if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}


def record(status: str, message: str, root: Path = ROOT, **metadata):
    atomic_json(state_path(root), {'state':status,'message':message[:240],'at':now_iso(),**metadata})


def schedule_install(port: int, target: str, root: Path = ROOT) -> bool:
    """Called only from the authenticated, confirmed local HTTP endpoint."""
    if getattr(sys,'frozen',False):
        raise ValueError('安装版请下载官方安装包更新；不可用源码热更新覆盖 EXE/APP。')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',target):
        raise ValueError('待安装的版本号无效。')
    base = handoff_root(root)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = base/'handoff.request.lock'
    if lock.is_symlink():raise ValueError('升级状态文件存在不安全的符号链接。')
    try:
        fd=os.open(str(lock),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:
        try:
            if time.time()-lock.stat().st_mtime>HANDOFF_TIMEOUT+90:
                lock.unlink()
                fd=os.open(str(lock),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            else:
                return False
        except (OSError,FileExistsError):
            return False
    try:
        with os.fdopen(fd,'w') as writer:
            writer.write(str(os.getpid()))
        record('queued','已确认更新；正在等待科研任务结束并关闭本地服务。',root,target_version=target)
        with (base/'handoff.log').open('a',encoding='utf-8') as output:
            subprocess.Popen([sys.executable,'-m','paper_radar.update_handoff',
                             '--port',str(port),'--version',target],
                cwd=str(root),stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                close_fds=True,start_new_session=os.name!='nt',
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
        return True
    except (OSError,ValueError):
        lock.unlink(missing_ok=True)
        record('failed','无法启动独立升级进程，旧服务继续运行。',root,target_version=target)
        return False


def _request_control(port: int, secret: str, route: str) -> int:
    req=Request(f'http://127.0.0.1:{port}{route}',data=b'{}',method='POST',
        headers={'X-Desktop-Supervision-Key':secret,'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=1.7) as response:
            return response.status
    except HTTPError as exc:
        return exc.code
    except (URLError,OSError,TimeoutError):
        return 0


def shutdown_user_services(root: Path, *, timeout: int = HANDOFF_TIMEOUT) -> bool:
    """Gracefully stop only services with the same private per-user identity.

    Current tasks may finish, but no service is forcibly killed. If a different
    user's Paper-Radar is running on any managed port, never update code.
    """
    from .desktop_manager import PORTS, read_health
    from .core import APP_ROOT
    # APP_ROOT and installation root must agree in source mode.
    if Path(APP_ROOT).resolve() != Path(root).resolve():
        raise RuntimeError('运行目录与升级目标不一致，已中止。')
    secret=read_or_create_secret(Path(APP_ROOT)/'data')
    owner=instance_fingerprint(secret)
    deadline=time.monotonic()+timeout
    while time.monotonic() < deadline:
        online=[]
        for port in PORTS:
            health=read_health(port,timeout=.3)
            if health:
                if health.get('desktop_instance') != owner:
                    raise RuntimeError('检测到其他用户的 Paper-Radar 服务，无法安全覆盖程序。')
                online.append(port)
        if not online:
            return True
        for port in online:
            result=_request_control(port,secret,'/api/desktop/stop')
            if result in (401,403):
                raise RuntimeError('本地服务身份不匹配，已拒绝关闭。')
            if result not in (0,200,409):
                raise RuntimeError('本地服务拒绝安全关闭，响应状态 '+str(result))
        time.sleep(.75)
    return False


def _resume_surviving_services(root: Path):
    from .desktop_manager import PORTS,read_health
    from .core import APP_ROOT
    secret=read_or_create_secret(Path(APP_ROOT)/'data')
    owner=instance_fingerprint(secret)
    for port in PORTS:
        value=read_health(port,timeout=.2)
        if value and value.get('desktop_instance')==owner:
            _request_control(port,secret,'/api/desktop/resume')


def restart_local_service(root: Path, port: int, expected_version: str, *, wait=RESTART_TIMEOUT):
    from .desktop_manager import read_health
    log = root/'logs'/'web_server.log'
    log.parent.mkdir(parents=True,exist_ok=True)
    with log.open('a',encoding='utf-8') as output:
        child=subprocess.Popen([sys.executable,str(root/'main.py'),'--serve','--port',str(port)],
            cwd=str(root),stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
            close_fds=True,start_new_session=os.name!='nt',
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0)
    deadline=time.monotonic()+wait
    while time.monotonic()<deadline:
        response=read_health(port,timeout=.35)
        if response and response.get('version')==expected_version:
            return True
        if child.poll() is not None:
            break
        time.sleep(.3)
    return False


def execute(port: int, target: str, root: Path = ROOT) -> int:
    root=Path(root).resolve()
    lock=handoff_root(root)/'handoff.request.lock'
    updater=StartupUpdater(root)
    updated=False
    try:
        if updater.public_service_running():
            raise RuntimeError('同一安装目录的公网服务仍运行，请由管理员停机升级。')
        record('validating','正在预检 GitHub 正式版本及发布附件。',root,target_version=target)
        eligible=updater.check_release()
        if not eligible or eligible.get('version')!=target:
            raise RuntimeError('最新正式 Release 或可信源码附件不匹配；旧服务保留运行。')
        record('draining','等待当前科研任务结束，并安全关闭本用户服务。',root,target_version=target)
        if not shutdown_user_services(root):
            raise RuntimeError('等待任务结束超过五分钟；未强制终止任务，请稍后重试。')
        if updater.is_web_running():
            raise RuntimeError('仍检测到本地服务，已取消覆盖代码。')
        if updater.public_service_running() or (root/'data'/'running.lock').exists():
            raise RuntimeError('仍存在公网服务或后台科研任务锁；本次升级已取消。')
        record('installing','所有可管理的本地服务已停止，正在校验、备份和安装。',root,target_version=target)
        result=updater.run(enabled=True)
        # Even a different version that slipped in between preflight and the
        # actual install must enter the rollback path.
        updated=result.get('state')=='updated'
        if result.get('state')!='updated' or updater.local_version()!=target:
            raise RuntimeError('安装未完成：'+str(result.get('message') or result.get('state')))
        # A new Release can change requirements; do not start an unchecked build.
        from startup import ensure_dependencies
        ensure_dependencies(root,updated=True)
        record('restarting','安装完成，正在启动新服务。',root,target_version=target)
        if not restart_local_service(root,port,target):
            raise RuntimeError('新版健康检查失败。')
        updater.status('success','手动更新成功，服务已重新启动。',installed_version=target)
        record('success','更新成功，已恢复研究工作台。',root,
               target_version=target,installed_version=target)
        return 0
    except Exception as exc:
        message=str(exc)[:220]
        # Never claim successful upgrade if the server failed to start.
        if updated:
            try:
                if updater.is_web_running():
                    shutdown_user_services(root,timeout=10)
                if updater.mark_failed_and_rollback(failed_version=target,reason=message):
                    record('rollback','更新失败，已恢复旧版程序并准备重启。',root,target_version=target)
            except Exception:
                message+='；自动回滚失败，请从备份恢复。'
        # Restore the pre-update service when possible, even after timeout.
        if not updater.is_web_running():
            previous=updater.local_version()
            try:
                restart_local_service(root,port,previous)
            except Exception:
                pass
        _resume_surviving_services(root)
        record('failed',message,root,target_version=target,
               installed_version=updater.local_version())
        return 1
    finally:
        lock.unlink(missing_ok=True)


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--version',required=True)
    args=parser.parse_args(argv)
    if args.port not in range(8765,8777) or not re.fullmatch(r'\d+\.\d+\.\d+',args.version):
        return 2
    return execute(args.port,args.version)

if __name__=='__main__':
    raise SystemExit(main())
