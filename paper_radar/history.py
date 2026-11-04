# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Local execution history; no API keys, raw user prompts or private PDF text."""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
import json
import sqlite3
from .core import APP_ROOT

HISTORY_FILE=APP_ROOT/'data'/'run_history.sqlite3'


def record_run(topic_id: str, mode: str, state: str, *, result: dict | None = None,
               error: str = '', started_at: str = '', path: Path = HISTORY_FILE) -> None:
    if not (state in ('success','failed','partial') and mode in ('search','search_all','scheduled','export')):
        raise ValueError('运行记录的状态或模式无效。')
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    # Keep only a bounded metadata digest; never persist model keys or paper text.
    res=dict(result or {})
    summary={k:res[k] for k in ('retrieved','new','selected','topics_succeeded','topics_failed') if k in res}
    diagnostics=res.get('source_results', [])
    if isinstance(diagnostics,list):
        summary['sources']=[{'name':str(r.get('name',''))[:48],
                             'count':int(r.get('count',0)),
                             'ok':bool(r.get('ok',False)),
                             'partial':bool(r.get('partial',False)),
                             'query_count':len(r.get('queries') or [])}
                            for r in diagnostics[:15] if isinstance(r,dict)]
    summary['ai_rerank']=res.get('ai_rerank',{}) if isinstance(res.get('ai_rerank',{}),dict) else {}
    with sqlite3.connect(path,timeout=10) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS runs (
         run_id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id TEXT NOT NULL,
         mode TEXT NOT NULL, state TEXT NOT NULL, started_at TEXT NOT NULL,
         finished_at TEXT NOT NULL, summary TEXT NOT NULL, error TEXT NOT NULL DEFAULT ''
        )''')
        conn.execute('INSERT INTO runs(topic_id,mode,state,started_at,finished_at,summary,error) VALUES (?,?,?,?,?,?,?)',
          (str(topic_id)[:48],mode,state,started_at or datetime.now(timezone.utc).isoformat(),
           datetime.now(timezone.utc).isoformat(),json.dumps(summary,ensure_ascii=False),str(error)[:300]))
        conn.execute('DELETE FROM runs WHERE run_id NOT IN (SELECT run_id FROM runs ORDER BY run_id DESC LIMIT 1500)')


def recent_runs(*, topic_id: str | None = None, limit: int = 20, path: Path = HISTORY_FILE) -> list[dict]:
    if not Path(path).is_file():
        return []
    with sqlite3.connect(path,timeout=10) as conn:
        conn.row_factory=sqlite3.Row
        args=[]
        clause=''
        if topic_id is not None:
            clause=' WHERE topic_id=?'
            args.append(topic_id)
        try:
            rows=conn.execute('SELECT * FROM runs'+clause+' ORDER BY run_id DESC LIMIT ?',
                              [*args,max(1,min(100,int(limit)))]).fetchall()
        except sqlite3.OperationalError:
            return []
    items=[]
    for r in rows:
        value=dict(r)
        try:
            value['summary']=json.loads(value['summary'])
        except (ValueError,TypeError):
            value['summary']={}
        items.append(value)
    return items
