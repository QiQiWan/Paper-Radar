#!/bin/sh
set -eu
cd "$(dirname "$0")"
PORT="${1:-${PAPERRADAR_PUBLIC_PORT:-8899}}"
if [ -z "${PAPERRADAR_PUBLIC_ORIGIN:-}" ]; then
  echo "PAPERRADAR_PUBLIC_ORIGIN must be set to https://your-domain" >&2
  echo "For local testing: PAPERRADAR_PUBLIC_ORIGIN=http://127.0.0.1:$PORT" >&2
  exit 2
fi
DATA="${PAPERRADAR_PORTAL_DATA:-$(pwd)/storage/portal}"
if [ -n "${PYTHON:-}" ]; then
  PYTHON_EXE="$PYTHON"
elif [ -x .venv/bin/python ]; then
  PYTHON_EXE=.venv/bin/python
else
  PYTHON_EXE=python3
fi
exec "$PYTHON_EXE" -m paper_radar.public_portal --data-root "$DATA" serve --host 127.0.0.1 --port "$PORT" --origin "$PAPERRADAR_PUBLIC_ORIGIN"
