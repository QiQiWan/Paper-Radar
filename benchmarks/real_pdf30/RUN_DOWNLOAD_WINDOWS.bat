@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (
  py -3 scripts\download_official_pdfs.py --manifest manifest.json --dest pdfs
) else (
  python scripts\download_official_pdfs.py --manifest manifest.json --dest pdfs
)
if errorlevel 1 (
  echo.
  echo One or more PDFs failed. Check network and retry. Completed files are skipped on next run.
  pause
  exit /b 1
)
echo.
echo All 30 original PDF checksums verified.
pause
