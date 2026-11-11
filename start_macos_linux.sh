#!/usr/bin/env sh
# Paper-Radar unified entrypoint
set -eu
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
if [ ! -x .venv/bin/python ]; then
  "$PYTHON" -m venv .venv
fi
case "${1:-}" in
  --review|--review-init|--review-status|--review-export|--review-gold|--eval|--smoke|--public|--worker|--help|--diagnostics)
    if [ "${1:-}" != "--help" ] && [ "${1:-}" != "--diagnostics" ]; then
      .venv/bin/python -c "import pypdf, keyring, cryptography, fitz" 2>/dev/null ||
        .venv/bin/python -m pip install -r requirements.txt
    fi
    exec .venv/bin/python -m paper_radar.cli_launcher "$@" ;;
  *) exec .venv/bin/python startup.py "$@" ;;
esac
