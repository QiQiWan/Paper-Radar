# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Stable pre-launch supervisor: update, install needed dependencies, launch.

Use start_windows.bat / start_macos_linux.sh for ordinary desktop starts.
Only standard-library modules are imported before dependency installation.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from paper_radar.startup_update import StartupUpdater, read_startup_settings

ROOT = Path(__file__).resolve().parent
REQUIRED_IMPORTS = 'import docx, keyring, pypdf, cryptography'


def ensure_dependencies(root: Path, *, updated=False, runner=None) -> None:
    runner = runner or subprocess.run
    if not updated:
        probe = runner([sys.executable, '-c', REQUIRED_IMPORTS],cwd=str(root),capture_output=True)
        if probe.returncode == 0:
            return
    print('正在核查 / 安装当前版本的 Python 依赖…',flush=True)
    install = runner([sys.executable, '-m', 'pip', 'install',
                      '--disable-pip-version-check', '--no-input',
                      '-r', str(root/'requirements.txt')],cwd=str(root))
    if install.returncode != 0:
        raise RuntimeError('依赖安装失败；请检查网络环境和 requirements.txt。')
    probe = runner([sys.executable, '-c', REQUIRED_IMPORTS],cwd=str(root),capture_output=True)
    if probe.returncode:
        raise RuntimeError('升级后仍缺少所需依赖，启动被阻止。')


def launch(root: Path, *, runner=None, mode=None) -> None:
    runner = runner or subprocess.run
    mode = mode or read_startup_settings(root)['service_run_mode']
    if mode not in ('foreground','background'):
        raise ValueError('Invalid service run mode')
    # A second shell invocation must not bind the same port twice.
    free_foreground_port=None
    if runner is subprocess.run:
        from paper_radar.desktop_manager import find_service, free_port
        from paper_radar.desktop_identity import read_or_create_secret, instance_fingerprint
        owner=instance_fingerprint(read_or_create_secret(root/'data'))
        if find_service(owner) is not None:
            from paper_radar.launcher import main as open_existing
            print('检测到本用户已有本地服务，正在打开现有工作台。',flush=True)
            open_existing()
            return
        if mode=='foreground':
            free_foreground_port=free_port()
            if free_foreground_port is None:
                raise RuntimeError('本机服务端口都已占用，请关闭其他实例后重试。')
    entry = '--web' if mode == 'foreground' else '--launch'
    print(('前台运行：按 Ctrl+C 停止本地服务。' if mode == 'foreground' else
           '后台运行：可在网页设置或桌面管理器中停止服务。'), flush=True)
    command=[sys.executable,str(root/'main.py'),entry]
    if mode=='foreground' and free_foreground_port is not None and free_foreground_port!=8765:
        command.extend(['--port',str(free_foreground_port)])
    try:
        outcome = runner(command,cwd=str(root))
    except KeyboardInterrupt:
        # Terminal users must be able to Ctrl+C without a Python traceback.
        print('已收到中断请求，本地工作台已停止。',flush=True)
        return
    if outcome.returncode:
        raise RuntimeError('本地工作台未启动，请查看 logs/web_server.log。')


def startup(root: Path = ROOT, *, updater=None, do_update=True,
            runner=None, mode=None) -> int:
    root=Path(root).resolve()
    updater=updater or StartupUpdater(root)
    settings=read_startup_settings(root)
    enabled = do_update and settings['update_check_enabled'] and settings['auto_update_on_startup']
    result=updater.run(enabled=enabled)
    print(result.get('message',''),flush=True)
    if result.get('state')=='recovery_failed':
        print('更新恢复失败，已阻止启动以免进一步损坏用户资料。',file=sys.stderr)
        return 2
    updated=result.get('state')=='updated'
    try:
        ensure_dependencies(root,updated=updated,runner=runner)
        launch(root,runner=runner,mode=mode)
        if updated:
            updater.status('success','新版依赖检查及本地工作台启动均已通过。',
                           previous_version=result.get('previous_version'),
                           installed_version=updater.local_version(),
                           release_url=result.get('release_url'),
                           transaction_id=result.get('transaction_id'))
        return 0
    except Exception as exc:
        print('启动检查失败：'+str(exc),file=sys.stderr,flush=True)
        if updated:
            try:
                if updater.mark_failed_and_rollback(failed_version=updater.local_version(),reason=str(exc)):
                    print('已恢复更新前程序，尝试使用原版本启动。',flush=True)
                    ensure_dependencies(root,updated=False,runner=runner)
                    launch(root,runner=runner,mode=mode)
                    return 0
            except Exception as recovery:
                print('回滚启动失败：'+str(recovery),file=sys.stderr,flush=True)
        return 1


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description='Paper-Radar 离线优先的安全启动器')
    parser.add_argument('--no-update',action='store_true',help='本次跳过自动检查')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--foreground',action='store_true',help='本次在终端前台运行；Ctrl+C 停止')
    mode.add_argument('--background',action='store_true',help='本次在后台启动，由界面管理服务')
    args=parser.parse_args(argv)
    override='foreground' if args.foreground else 'background' if args.background else None
    return startup(do_update=not args.no_update,mode=override)


if __name__ == '__main__':
    raise SystemExit(main())
