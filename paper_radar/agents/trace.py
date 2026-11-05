# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Bounded privacy-preserving traces in a separate local SQLite file."""
from __future__ import annotations
from pathlib import Path
import json
import sqlite3


def store_trace(trace: dict, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    compact = json.dumps(trace, ensure_ascii=False, separators=(',', ':'))
    with sqlite3.connect(path, timeout=10) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS agent_runs (
            run_id TEXT PRIMARY KEY, topic_id TEXT NOT NULL,
            started_at TEXT NOT NULL, state TEXT NOT NULL, trace TEXT NOT NULL)''')
        conn.execute('INSERT OR REPLACE INTO agent_runs VALUES (?,?,?,?,?)',
                     (trace['run_id'], trace['topic_id'], trace['started_at'],trace['state'],compact))
        conn.execute('CREATE INDEX IF NOT EXISTS idx_agent_runs_topic ON agent_runs(topic_id,started_at)')
        conn.execute('''DELETE FROM agent_runs WHERE run_id NOT IN
                        (SELECT run_id FROM agent_runs ORDER BY started_at DESC LIMIT 300)''')


def read_traces(path: Path, *, topic_id: str | None = None, limit: int = 10) -> list[dict]:
    path = Path(path)
    if not path.is_file():
        return []
    limit = max(1, min(50, int(limit)))
    with sqlite3.connect(path, timeout=10) as conn:
        try:
            if topic_id is None:
                rows = conn.execute('SELECT trace FROM agent_runs ORDER BY started_at DESC LIMIT ?',
                                    (limit,)).fetchall()
            else:
                rows = conn.execute('SELECT trace FROM agent_runs WHERE topic_id=? ORDER BY started_at DESC LIMIT ?',
                                    (topic_id,limit)).fetchall()
        except sqlite3.OperationalError:
            return []
    return [json.loads(row[0]) for row in rows]
