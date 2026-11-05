#!/usr/bin/env sh
# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
set -eu
cd "$(dirname "$0")"
PYTHON=${PYTHON:-python3}
if [ ! -x .venv/bin/python ]; then
  "$PYTHON" -m venv .venv
fi
exec .venv/bin/python startup.py "$@"
