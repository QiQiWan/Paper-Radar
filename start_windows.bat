@echo off
rem Paper-Radar unified entrypoint (local, public, worker, review and tests)
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>nul
  if not errorlevel 1 (py -3 -m venv .venv) else (python -m venv .venv)
  if errorlevel 1 (echo Python 3.10+ required. & exit /b 1)
)
if /I "%~1"=="--review" goto tools
if /I "%~1"=="--review-init" goto tools
if /I "%~1"=="--review-status" goto tools
if /I "%~1"=="--review-export" goto tools
if /I "%~1"=="--review-gold" goto tools
if /I "%~1"=="--smoke" goto tools
if /I "%~1"=="--eval" goto tools
if /I "%~1"=="--public" goto tools
if /I "%~1"=="--worker" goto tools
if /I "%~1"=="--help" goto tools
if /I "%~1"=="--diagnostics" goto dispatch
".venv\Scripts\python.exe" startup.py %*
exit /b %errorlevel%
:tools
if /I "%~1"=="--help" goto dispatch
".venv\Scripts\python.exe" -c "import pypdf, keyring, cryptography, fitz" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
)
:dispatch
".venv\Scripts\python.exe" -m paper_radar.cli_launcher %*
exit /b %errorlevel%
