@echo off
rem Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>nul
  if not errorlevel 1 (py -3 -m venv .venv) else (python -m venv .venv)
  if errorlevel 1 (
    echo Python 3.10 or newer is required.
    echo Install Python from https://www.python.org/downloads/
    pause
    exit /b 1
  )
)
rem Startup update runs before normal dependencies are imported.
rem The bootstrap skips networking if disabled or GitHub is unreachable.
".venv\Scripts\python.exe" startup.py %*
if errorlevel 1 (
  echo Paper-Radar could not start. Read .paper_radar_updates\status.json or logs\web_server.log.
  pause
  exit /b 1
)
