@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m paper_radar.sources.smoke --live --timeout 25 --output retrieval_smoke.json
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python not found. Run this check from a Python source installation.
    exit /b 2
  )
  python -m paper_radar.sources.smoke --live --timeout 25 --output retrieval_smoke.json
)
echo.
echo Check result saved as retrieval_smoke.json.
pause
endlocal
