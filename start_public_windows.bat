@echo off
rem Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
setlocal EnableExtensions
cd /d "%~dp0"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=%PAPERRADAR_PUBLIC_PORT%"
if "%PORT%"=="" set "PORT=8899"
if "%PAPERRADAR_PUBLIC_ORIGIN%"=="" (
  echo [ERROR] Please set PAPERRADAR_PUBLIC_ORIGIN=https://your-domain before public deployment.
  echo For local-only testing: set PAPERRADAR_PUBLIC_ORIGIN=http://127.0.0.1:%PORT%
  exit /b 2
)
if "%PAPERRADAR_PORTAL_DATA%"=="" set "PAPERRADAR_PORTAL_DATA=%~dp0storage\portal"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>nul
  if not errorlevel 1 (py -3 -m venv .venv) else (python -m venv .venv)
  if errorlevel 1 (
    echo [ERROR] Python 3.10 or newer is required.
    exit /b 1
  )
)
set "PYTHON_EXE=.venv\Scripts\python.exe"
"%PYTHON_EXE%" -c "import cryptography, pypdf, docx, keyring" >nul 2>nul
if errorlevel 1 (
  echo [INFO] Installing Paper-Radar dependencies in .venv ...
  "%PYTHON_EXE%" -m pip install --disable-pip-version-check --no-input -r requirements.txt
  if errorlevel 1 exit /b 1
)
echo Paper-Radar Public Portal - Port %PORT%
echo Origin: %PAPERRADAR_PUBLIC_ORIGIN%
"%PYTHON_EXE%" -m paper_radar.public_portal --data-root "%PAPERRADAR_PORTAL_DATA%" serve --host 127.0.0.1 --port "%PORT%" --origin "%PAPERRADAR_PUBLIC_ORIGIN%"
exit /b %ERRORLEVEL%
