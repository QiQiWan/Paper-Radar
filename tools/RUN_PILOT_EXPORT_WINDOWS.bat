@echo off
setlocal
cd /d "%~dp0\.."
python tools\export_pilot_audit.py %*
if errorlevel 1 echo Export failed. Please check current Python environment.
pause
