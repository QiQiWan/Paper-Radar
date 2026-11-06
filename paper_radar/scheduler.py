# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Manage a Windows user-level scheduled job (no resident application required).

Task Scheduler runs a fresh worker at the configured time. It is deliberately
not installed as a Windows service: no administrator privilege or daemon needed.
"""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

TASK_NAME = "Paper-Radar_Daily"
LEGACY_TASK_NAME = "LiteratureTracker_Daily"


def _quote_ps(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_install_script(time_str: str, python_exe: str, main_file: str, *, frozen=False) -> str:
    import datetime
    datetime.datetime.strptime(time_str, "%H:%M")
    exe = _quote_ps(python_exe)
    args = _quote_ps('--once' if frozen else f'"{main_file}" --once')
    workdir = _quote_ps(str(Path(python_exe if frozen else main_file).parent))
    name = _quote_ps(TASK_NAME)
    legacy = _quote_ps(LEGACY_TASK_NAME)
    return (
        "$ErrorActionPreference='Stop';"
        f"$action=New-ScheduledTaskAction -Execute {exe} -Argument {args} -WorkingDirectory {workdir};"
        f"$trigger=New-ScheduledTaskTrigger -Daily -At '{time_str}';"
        "$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable "
        "-MultipleInstances IgnoreNew -RestartCount 2 "
        "-RestartInterval (New-TimeSpan -Minutes 15) "
        "-ExecutionTimeLimit (New-TimeSpan -Minutes 90) "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries;"
        f"Register-ScheduledTask -TaskName {name} -Action $action "
        "-Trigger $trigger -Settings $settings -Force | Out-Null;"
        f"$task=Get-ScheduledTask -TaskName {name};"
        "if($task.State -eq 'Disabled'){throw 'Task registered but disabled'};"
        f"$old=Get-ScheduledTask -TaskName {legacy} -ErrorAction SilentlyContinue;"
        f"if($old){{Unregister-ScheduledTask -TaskName {legacy} -Confirm:$false}};"
        "'installed'"
    )


def _ps(script: str, timeout=45) -> str:
    if os.name != "nt":
        raise RuntimeError("后台定时任务仅支持 Windows。其他系统可使用系统自带的定时任务工具。")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, errors="replace", timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout or "定时任务操作失败").strip()[-700:])
    return proc.stdout.strip()


def install(time_str: str, python_exe: str | None = None, main_file: str | None = None) -> dict:
    executable = str(Path(python_exe or sys.executable).resolve())
    possible_pythonw = str(Path(executable).with_name("pythonw.exe"))
    if Path(possible_pythonw).exists():
        executable = possible_pythonw
    if not main_file:
        main_file = str((Path(__file__).resolve().parent.parent / "main.py").resolve())
    _ps(build_install_script(time_str, executable, main_file,
                             frozen=getattr(sys,'frozen',False)))
    return status()


def remove() -> dict:
    for task_name in (TASK_NAME, LEGACY_TASK_NAME):
        name = _quote_ps(task_name)
        _ps(f"$task=Get-ScheduledTask -TaskName {name} -ErrorAction SilentlyContinue;"
            f"if ($task) {{Unregister-ScheduledTask -TaskName {name} -Confirm:$false}}")
    return {"installed": False, "state": "not_installed"}


def run_now() -> dict:
    name = _quote_ps(TASK_NAME)
    _ps(f"Start-ScheduledTask -TaskName {name} -ErrorAction Stop")
    return status()


def status() -> dict:
    if os.name != "nt":
        return {"installed": False, "state": "unsupported", "next_run": "", "last_run": "", "last_result": None}
    name = _quote_ps(TASK_NAME)
    legacy = _quote_ps(LEGACY_TASK_NAME)
    code = (
        f"$task=Get-ScheduledTask -TaskName {name} -ErrorAction SilentlyContinue;"
        f"$legacy=Get-ScheduledTask -TaskName {legacy} -ErrorAction SilentlyContinue;"
        "if (-not $task) { if ($legacy) { '{\"installed\":false,\"state\":\"legacy_installed\",\"legacy_installed\":true}' } "
        "else { '{\"installed\":false}' } } else {"
        f"$info=Get-ScheduledTaskInfo -TaskName {name};"
        "@{installed=$true;state=[string]$task.State;"
        "next_run=($info.NextRunTime.ToString('yyyy-MM-dd HH:mm'));"
        "last_run=($info.LastRunTime.ToString('yyyy-MM-dd HH:mm'));"
        "last_result=[int]$info.LastTaskResult} | ConvertTo-Json -Compress}"
    )
    try:
        data = json.loads(_ps(code, timeout=15))
        return {"installed": bool(data.get("installed")),
                "state": data.get("state", "not_installed"),
                "next_run": data.get("next_run", ""),
                "last_run": data.get("last_run", ""),
                "last_result": data.get("last_result"), "legacy_installed": bool(data.get("legacy_installed", False))}
    except Exception:
        return {"installed": False, "state": "unknown", "next_run": "", "last_run": "", "last_result": None}
