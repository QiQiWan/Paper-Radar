#!/usr/bin/env sh
# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
set -eu
cd "$(dirname "$0")"
PYTHON=${PYTHON:-python3}
if [ ! -x .venv/bin/python ]; then
  "$PYTHON" -m venv .venv
fi
.venv/bin/python -c "import docx, keyring" 2>/dev/null || .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py --web
