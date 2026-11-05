@echo off
setlocal
cd /d "%~dp0"
if not defined PAPERRADAR_PORTAL_DATA set "PAPERRADAR_PORTAL_DATA=%CD%\storage\portal"
if not defined PAPERRADAR_LEASE_SECONDS set "PAPERRADAR_LEASE_SECONDS=45"
if not defined PAPERRADAR_HEARTBEAT_SECONDS set "PAPERRADAR_HEARTBEAT_SECONDS=5"
if not defined PAPERRADAR_JOB_TIMEOUT set "PAPERRADAR_JOB_TIMEOUT=150"
if not defined PAPERRADAR_WORKER_MEMORY_MB set "PAPERRADAR_WORKER_MEMORY_MB=1536"
if exist ".venv\Scripts\python.exe" (
  set "WORKER_PYTHON=.venv\Scripts\python.exe"
) else (
  set "WORKER_PYTHON=python"
)
"%WORKER_PYTHON%" -m paper_radar.portal_worker --data-root "%PAPERRADAR_PORTAL_DATA%" --lease-seconds %PAPERRADAR_LEASE_SECONDS% --heartbeat-seconds %PAPERRADAR_HEARTBEAT_SECONDS% --job-timeout %PAPERRADAR_JOB_TIMEOUT% --memory-mb %PAPERRADAR_WORKER_MEMORY_MB% run
endlocal
