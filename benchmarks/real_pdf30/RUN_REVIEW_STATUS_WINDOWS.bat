@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (
  py -3 scripts\validate_and_score.py --dataset . --out annotation_status.json
) else (
  python scripts\validate_and_score.py --dataset . --out annotation_status.json
)
pause
