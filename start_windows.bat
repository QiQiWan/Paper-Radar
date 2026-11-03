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
".venv\Scripts\python.exe" -c "import docx, keyring" >nul 2>nul
if errorlevel 1 (
  echo First run: installing dependencies...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Dependency installation failed.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" main.py
if errorlevel 1 pause
