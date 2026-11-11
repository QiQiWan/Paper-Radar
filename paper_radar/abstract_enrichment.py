# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Auditable, non-generative abstract completion for a local research topic.

The metadata provider is allowed to return no abstract. Existing strong abstracts
are never overwritten. Network failures and missing source metadata are counted
separately; no AI guesses missing abstracts or sends local paper content away.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import sqlite3
from time import monotonic
from uuid import uuid4

from .abstract_recovery import _candidate
from .core import open_database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def abstract_quality(raw: str) -> str:
    text = ' '.join(re.sub(r'<[^>]*>', ' ', str(raw or '')).split())
    if not text:
        return 'missing'
    if text.endswith(('...', '…', '[...]')):
        return 'possibly_truncated'
    if len(text) < 215 or (not re.search(r'[\u4e00-\u9fff]', text) and len(text.split()) < 38):
        return 'short'
    return 'adequate'


def init_schema(db: sqlite3.Connection):
    db.execute('''CREATE TABLE IF NOT EXISTS abstract_enrichment_runs (
        id TEXT PRIMARY KEY, topic_id TEXT NOT NULL, state TEXT NOT NULL,
        attempted INTEGER NOT NULL DEFAULT 0, improved INTEGER NOT NULL DEFAULT 0,
        unavailable INTEGER NOT NULL DEFAULT 0, unchanged INTEGER NOT NULL DEFAULT 0,
        failed INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, completed_at TEXT DEFAULT ''
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS abstract_enrichment_attempts (
        run_id TEXT NOT NULL, paper_id TEXT NOT NULL, old_quality TEXT NOT NULL,
        new_quality TEXT NOT NULL, old_length INTEGER NOT NULL, new_length INTEGER NOT NULL,
        provider TEXT NOT NULL, outcome TEXT NOT NULL, error_class TEXT NOT NULL,
        elapsed_ms INTEGER NOT NULL, at TEXT NOT NULL,
        PRIMARY KEY (run_id,paper_id)
    )''')
    db.commit()


def _recent_attempts(db: sqlite3.Connection, topic_id: str) -> dict[str,dict]:
    """Most recent source check per paper; no abstract or personal data in audit."""
    rows=db.execute("""SELECT a.paper_id,a.outcome,a.new_quality,a.at,a.provider,a.error_class
        FROM abstract_enrichment_attempts a JOIN topic_papers tp ON tp.paper_id=a.paper_id
        WHERE tp.topic_id=? ORDER BY a.at DESC,a.rowid DESC""",(topic_id,)).fetchall()
    latest={}
    for row in rows:
        if row['paper_id'] not in latest:latest[row['paper_id']]=dict(row)
    return latest


def _actionable(quality: str, previous: dict | None, *, force=False) -> bool:
    if quality=='adequate':return False
    if force or not previous:return True
    try:
        checked=datetime.fromisoformat(str(previous.get('at') or ''))
        age=datetime.now(timezone.utc)-checked.astimezone(timezone.utc)
    except (ValueError,TypeError):return True
    # Avoid hammering a source that returned a short/empty official abstract.
    # Genuine temporary errors become retryable sooner.
    cooldown=timedelta(hours=12) if previous['outcome']=='failed' else timedelta(days=7)
    return age >= cooldown


def quality_summary(db: sqlite3.Connection, topic_id: str) -> dict:
    init_schema(db)
    rows = db.execute('''SELECT p.paper_id,p.abstract FROM topic_papers tp JOIN papers p
         ON p.paper_id=tp.paper_id WHERE tp.topic_id=?''',(topic_id,)).fetchall()
    counts={name:0 for name in ('missing','short','possibly_truncated','adequate')}
    latest=_recent_attempts(db,topic_id)
    pending=[];checked=[]
    for row in rows:
        status=abstract_quality(row['abstract'])
        counts[status]+=1
        prev=latest.get(row['paper_id'])
        if _actionable(status,prev):
            pending.append(row['paper_id'])
        elif status != 'adequate':
            checked.append({'paper_id':row['paper_id'],'quality':status,
                'reason':('来源未提供摘要' if prev and prev['outcome']=='unavailable' else
                          '来源摘要已检查，暂无更完整版本' if prev and prev['outcome']=='unchanged' else
                          '来源暂时获取失败，稍后可重试'),
                'last_outcome':prev['outcome'] if prev else ''})
    previous=db.execute('''SELECT * FROM abstract_enrichment_runs
        WHERE topic_id=? ORDER BY started_at DESC,id DESC LIMIT 1''',(topic_id,)).fetchone()
    return {'topic_id':topic_id,'total':len(rows),'by_quality':counts,
            'needs_refresh':len(pending),'eligible_paper_ids':pending[:100],
            'checked_without_improvement':len(checked),
            'checked_details':checked[:100],
            'last_run':dict(previous) if previous else None}


def create_run(db: sqlite3.Connection, topic_id: str) -> str:
    init_schema(db)
    rid = uuid4().hex
    db.execute('''INSERT INTO abstract_enrichment_runs
        (id,topic_id,state,started_at) VALUES(?,?,?,?)''',(rid,topic_id,'running',_now()))
    db.commit()
    return rid


def _better(old: str, candidate: str) -> bool:
    a = ' '.join(str(old or '').split())
    b = ' '.join(str(candidate or '').split())
    if abstract_quality(a) == 'adequate':
        return False
    # Avoid replacing a true, albeit concise, source abstract with a worse one.
    if not a:
        return len(b)>=35
    if abstract_quality(b)=='possibly_truncated':return False
    # A complete official short abstract may be valid; only replace if clearly better.
    return len(b)>=len(a)+55 and len(b)>=len(a)*1.2


def refresh_one(db: sqlite3.Connection, paper_id: str, *, fetcher=None) -> dict:
    row = db.execute('SELECT paper_id,title,abstract,doi,source FROM papers WHERE paper_id=?',(paper_id,)).fetchone()
    if row is None:
        raise ValueError('论文不存在')
    old = row['abstract'] or ''
    old_quality = abstract_quality(old)
    if old_quality == 'adequate':
        return {'outcome':'unchanged','old_quality':old_quality,'new_quality':old_quality,
                'old_length':len(old),'new_length':len(old),'provider':'','error_class':''}
    provider = ''
    try:
        candidate,provider = (fetcher or _candidate)(dict(row))
    except Exception as exc:
        # Provider payloads may contain sensitive values; persist only exception type.
        return {'outcome':'failed','old_quality':old_quality,'new_quality':old_quality,
                'old_length':len(old),'new_length':len(old),'provider':'','error_class':type(exc).__name__}
    candidate = ' '.join(str(candidate or '').split())[:12000]
    if not candidate:
        outcome='unavailable'
    elif not _better(old,candidate):
        outcome='unchanged'
    else:
        # Concurrent manual edits must not be overwritten: update only if the old
        # abstract is still identical to the one used for the comparison.
        updated = db.execute('''UPDATE papers SET abstract=?,summary='',summary_fingerprint='',summary_model=''
            WHERE paper_id=? AND coalesce(abstract,'')=?''',(candidate,paper_id,old)).rowcount
        outcome = 'improved' if updated else 'unchanged'
    return {'outcome':outcome,'old_quality':old_quality,
            'new_quality':abstract_quality(candidate) if outcome=='improved' else old_quality,
            'old_length':len(old),'new_length':len(candidate) if outcome=='improved' else len(old),
            'provider':str(provider)[:32], 'error_class':''}


def run_batch(db_path: Path, topic_id: str, *, limit=30, run_id: str | None = None,
              progress=None, logger=None, fetcher=None, force_retry=False) -> dict:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('摘要补取数量必须在 1 至 100 之间')
    if type(topic_id) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',topic_id):
        raise ValueError('研究主题编号无效')
    with open_database(db_path) as db:
        init_schema(db)
        # Prefer missing, then obviously truncated/short abstracts; each paper
        # is fetched at most once in one batch, with bounded network time.
        rows=db.execute('''SELECT p.paper_id,p.abstract FROM topic_papers tp JOIN papers p
             ON p.paper_id=tp.paper_id WHERE tp.topic_id=?''',(topic_id,)).fetchall()
        rank={'missing':0,'possibly_truncated':1,'short':2,'adequate':3}
        recent=_recent_attempts(db,topic_id)
        selected=[r['paper_id'] for r in sorted(rows,key=lambda r:(rank[abstract_quality(r['abstract'])],r['paper_id']))
                  if _actionable(abstract_quality(r['abstract']),recent.get(r['paper_id']),force=force_retry)][:limit]
        run_id=run_id or create_run(db,topic_id)
    totals={'attempted':0,'improved':0,'unavailable':0,'unchanged':0,'failed':0}
    for i,pid in enumerate(selected,1):
        started=monotonic()
        with open_database(db_path) as db:
            record=refresh_one(db,pid,fetcher=fetcher)
            duration=int((monotonic()-started)*1000)
            db.execute('''INSERT OR REPLACE INTO abstract_enrichment_attempts
                (run_id,paper_id,old_quality,new_quality,old_length,new_length,provider,outcome,error_class,elapsed_ms,at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (run_id,pid,record['old_quality'],record['new_quality'],record['old_length'],
                record['new_length'],record['provider'],record['outcome'],record['error_class'],duration,_now()))
            totals['attempted']+=1
            totals[record['outcome']]+=1
            db.execute('''UPDATE abstract_enrichment_runs SET attempted=?,improved=?,unavailable=?,
                unchanged=?,failed=? WHERE id=?''',(totals['attempted'],totals['improved'],totals['unavailable'],
                totals['unchanged'],totals['failed'],run_id))
        if progress: progress(i,len(selected),f'已检查 {i}/{len(selected)} 篇，新增完整摘要 {totals["improved"]} 篇')
        if logger and record['outcome'] in ('failed','improved'):
            logger(f'摘要补取：{i}/{len(selected)}，{record["outcome"]}，来源 {record["provider"] or "未提供"}')
    with open_database(db_path) as db:
        state=('partial' if totals['failed'] else 'no_changes' if not totals['improved'] else 'completed')
        db.execute('UPDATE abstract_enrichment_runs SET state=?,completed_at=? WHERE id=?',
                   (state,_now(),run_id))
        current=quality_summary(db,topic_id)
    return {'run_id':run_id,'state':state,'selected':len(selected),**totals,
            'quality':current['by_quality'],'remaining_actionable':current['needs_refresh'],
            'checked_without_improvement':current['checked_without_improvement']}

