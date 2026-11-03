# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Tiny status journal used by both the UI and the headless scheduled worker."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import json
import os
from .core import APP_ROOT

STATUS_FILE = APP_ROOT / "data" / "last_run.json"


def write_status(state: dict, path: Path = STATUS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {**state, "timestamp": datetime.now().astimezone().isoformat(timespec="seconds")}
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def read_status(path: Path = STATUS_FILE) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "never", "timestamp": ""}
