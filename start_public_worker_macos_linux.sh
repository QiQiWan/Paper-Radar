#!/bin/sh
set -eu
cd "$(dirname "$0")"
DATA="${PAPERRADAR_PORTAL_DATA:-$(pwd)/storage/portal}"
if [ -n "${PYTHON:-}" ]; then PYTHON_EXE="$PYTHON"
elif [ -x .venv/bin/python ]; then PYTHON_EXE=.venv/bin/python
else PYTHON_EXE=python3; fi
exec "$PYTHON_EXE" -m paper_radar.portal_worker \
  --data-root "$DATA" \
  --lease-seconds "${PAPERRADAR_LEASE_SECONDS:-45}" \
  --heartbeat-seconds "${PAPERRADAR_HEARTBEAT_SECONDS:-5}" \
  --job-timeout "${PAPERRADAR_JOB_TIMEOUT:-480}" --cpu-seconds "${PAPERRADAR_CPU_SECONDS:-240}" \
  --memory-mb "${PAPERRADAR_WORKER_MEMORY_MB:-1536}" run
