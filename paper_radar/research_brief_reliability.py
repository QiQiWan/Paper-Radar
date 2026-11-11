# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.45.5: resumable and idempotent research briefs with late-event amendments.

A brief is immutable. New events for a closed period become append-only
amendments, and their identity is consumed once per topic and cadence.
A durable job cursor records the scan even if the worker exits halfway.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import secrets
import sqlite3

from .research_continuity import _build_brief, _claim_map, _time, _window, to_markdown, utc_now

JOB_BATCH = 200
MAX_BATCHES = 50


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(obj):
    return sha256(_canon(obj).encode('utf-8')).hexdigest()


def _period_end_for_event(cadence, observed_at):
    observed = _time(observed_at)
    if cadence == 'daily':
        base = observed.replace(hour=0, minute=0, second=0, microsecond=0)
        return (base + timedelta(days=1)).isoformat()
    if cadence == 'weekly':
        base = (observed - timedelta(days=observed.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return (base + timedelta(days=7)).isoformat()
    if cadence == 'monthly':
        yy, mm = observed.year, observed.month
        return datetime(yy + int(mm == 12), 1 if mm == 12 else mm + 1,
                        1, tzinfo=timezone.utc).isoformat()
    raise ValueError('Invalid cadence')


def _history_ledger_backfill(db, topic, cadence):
    """Recover legacy event ranges, including events beyond 60 displayed rows."""
    rows = db.execute('''SELECT id,period_end,snapshot_json FROM research_period_briefs
        WHERE topic_id=? AND cadence=?''', (topic, cadence)).fetchall()
    for row in rows:
        source = json.loads(row['snapshot_json'])
        low, high = int(source.get('from_event_cursor') or 0), int(source.get('to_event_cursor') or 0)
        if high <= low:
            continue
        db.execute('''INSERT OR IGNORE INTO research_brief_event_ledger
            (topic_id,cadence,event_id,brief_id,delivery_kind,logged_at)
            SELECT ?,?,id,?,'base',? FROM following_events
            WHERE topic_id=? AND id>? AND id<=? AND observed_at<?''',
            (topic, cadence, row['id'], utc_now(), topic, low, high, row['period_end']))


def _latest_snapshot(db, topic, cadence, end):
    return db.execute('''SELECT * FROM research_period_briefs
       WHERE topic_id=? AND cadence=? AND period_end=?''',
       (topic, cadence, end)).fetchone()


def _job(db, topic, cadence, start, end):
    row = db.execute('''SELECT * FROM research_brief_jobs
        WHERE topic_id=? AND cadence=? AND period_end=?''', (topic,cadence,end)).fetchone()
    if row is None:
        identifier = secrets.token_hex(16)
        watermark = db.execute('SELECT COALESCE(MAX(id),0) FROM following_events WHERE topic_id=?',
                               (topic,)).fetchone()[0]
        timestamp = utc_now()
        db.execute('''INSERT INTO research_brief_jobs
            (id,topic_id,cadence,period_start,period_end,state,last_scanned_id,
             watermark_id,attempts,created_at,updated_at) VALUES
            (?,?,?,?,?,'running',0,?,0,?,?)''',
            (identifier,topic,cadence,start,end,watermark,timestamp,timestamp))
        row=db.execute('SELECT * FROM research_brief_jobs WHERE id=?',(identifier,)).fetchone()
    else:
        watermark=db.execute('SELECT COALESCE(MAX(id),0) FROM following_events WHERE topic_id=?',
                             (topic,)).fetchone()[0]
        if row['state']=='complete' and watermark>row['watermark_id']:
            db.execute('''UPDATE research_brief_jobs SET state='running',watermark_id=?,
                          updated_at=? WHERE id=?''',(watermark,utc_now(),row['id']))
            row=db.execute('SELECT * FROM research_brief_jobs WHERE id=?',(row['id'],)).fetchone()
    return row


def _append_amendment(db, topic, cadence, row, events, kind, *, revision=None):
    source = {'schema_version':2,'kind':kind,'brief_id':row['id'],
              'topic_id':topic,'cadence':cadence,'period_end':row['period_end'],
              'events':[{'id':e['id'],'paper_id':e['paper_id'],'title':e['title'][:260],
                         'source':e['source_name'],'kind':e['kind'],'observed_at':e['observed_at']}
                        for e in events],
              'revision_diff':revision or {}}
    digest=_digest(source)
    ident=secrets.token_hex(16)
    db.execute('''INSERT OR IGNORE INTO research_brief_amendments
        (id,brief_id,topic_id,cadence,created_at,digest,snapshot_json)
        VALUES (?,?,?,?,?,?,?)''',(ident,row['id'],topic,cadence,utc_now(),digest,_canon(source)))
    return digest


def revision_diff(snapshot, current_claims):
    older=snapshot.get('confirmed_claims') or {}
    return {'new':sorted(set(current_claims)-set(older)),
            'revised':sorted(k for k in current_claims.keys() & older.keys()
                             if current_claims[k]!=older[k]),
            'no_longer_current':sorted(set(older)-set(current_claims))}


def _finish(db,topic,cadence,start,end,chain,job,*,backend='local',
            model_config=None,use_model=False,confirm_transfer=False,model_callable=None):
    # SQLite DB readers see a consistent set because the owning transaction
    # includes creating a snapshot and marking its events consumed.
    events=[dict(r) for r in db.execute('''SELECT e.id,e.kind,e.paper_id,e.title,e.source_name,
            e.observed_at,e.details_json FROM research_brief_job_events je
            JOIN following_events e ON e.id=je.event_id
            LEFT JOIN research_brief_event_ledger l
              ON l.topic_id=? AND l.cadence=? AND l.event_id=e.id
            WHERE je.job_id=? AND l.event_id IS NULL ORDER BY e.id''',
            (topic,cadence,job['id']))]
    base=_latest_snapshot(db,topic,cadence,end)
    group=defaultdict(list)
    current=[]
    # Route truly late events to their original closed period whenever it exists.
    for event in events:
        intended=_period_end_for_event(cadence,event['observed_at'])
        if intended!=end:
            old=_latest_snapshot(db,topic,cadence,intended)
            if old is not None:group[old['id']].append(event);continue
        current.append(event)
    if base is None:
        report=_build_brief(db,topic,chain,cadence,_time(start),_time(end),
                            model_config=model_config,use_model=use_model,
                            confirm_transfer=confirm_transfer,model_callable=model_callable,
                            backend=backend,events_override=current)
        raw=_canon(report)
        if len(raw.encode('utf-8'))>500_000:
            raise ValueError('简报太大，请缩小周期，已保留断点')
        ident=secrets.token_hex(16)
        db.execute('''INSERT INTO research_period_briefs
            (id,topic_id,cadence,period_start,period_end,created_at,
             cursor_event_id,source_sha256,snapshot_json) VALUES (?,?,?,?,?,?,?,?,?)''',
            (ident,topic,cadence,start,end,report['generated_at'],
             report['to_event_cursor'],sha256(raw.encode()).hexdigest(),raw))
        base=_latest_snapshot(db,topic,cadence,end)
        for event in current:
            db.execute('''INSERT INTO research_brief_event_ledger VALUES (?,?,?,?,?,?)''',
                (topic,cadence,event['id'],base['id'],'base',utc_now()))
    elif current:
        group[base['id']].extend(current)
    for target_id,group_events in group.items():
        target=db.execute('SELECT * FROM research_period_briefs WHERE id=?',(target_id,)).fetchone()
        _append_amendment(db,topic,cadence,target,group_events,'late_events')
        for event in group_events:
            db.execute('''INSERT INTO research_brief_event_ledger VALUES (?,?,?,?,?,?)''',
                (topic,cadence,event['id'],target_id,'amendment',utc_now()))
    # Persist a provenance-only revision difference; never rewrite the old brief.
    snapshot=json.loads(base['snapshot_json'])
    _,current_claims=_claim_map(chain)
    changes=revision_diff(snapshot,current_claims)
    if any(changes.values()):
        _append_amendment(db,topic,cadence,base,[],'research_evidence_changed',
                          revision={'changes':changes,'claim_digest':_digest(current_claims)})
    db.execute('''UPDATE research_brief_jobs SET state='complete',lease_owner='',
        lease_until='',last_error='',updated_at=? WHERE id=?''',(utc_now(),job['id']))
    db.execute('''UPDATE research_brief_preferences SET last_generated=? WHERE topic_id=?''',
               (utc_now(),topic))
    return {'id':base['id'],'late_events':len(group.get(base['id'],[])),
            'amendments':sum(len(v) for v in group.values()),'revision_diff':changes}


def run(db,topic,chain,*,cadence='weekly',now=None,model_config=None,
        use_model=False,confirm_transfer=False,model_callable=None,backend='local',
        max_batches=MAX_BATCHES,batch_size=JOB_BATCH):
    """Run or resume a periodic brief job. Each scan chunk commits durably.

    Lease/ownership is respected across processes; crashes permit takeover once
    lease expires. Call again when `state=processing` to continue.
    """
    if not 1<=int(batch_size)<=1000 or not 1<=int(max_batches)<=200:
        raise ValueError('Invalid brief job batch limits')
    start,end=_window(cadence,now)
    begin,until=start.isoformat(),end.isoformat()
    owner=secrets.token_hex(12)
    from datetime import timedelta
    timestamp=_time()
    lease_deadline=(timestamp+timedelta(seconds=120)).isoformat()
    db.commit()
    try:
        db.execute('BEGIN IMMEDIATE')
        _history_ledger_backfill(db,topic,cadence)
        job=_job(db,topic,cadence,begin,until)
        if job['state']=='running' and job['lease_owner'] and job['lease_until']>timestamp.isoformat():
            db.commit()
            return {'state':'busy','job_id':job['id'],'current':job['last_scanned_id']}
        db.execute('''UPDATE research_brief_jobs SET lease_owner=?,lease_until=?,
            attempts=attempts+1,updated_at=? WHERE id=?''',
            (owner,lease_deadline,utc_now(),job['id']))
        db.commit()
        for _ in range(max_batches):
            db.execute('BEGIN IMMEDIATE')
            job=db.execute('SELECT * FROM research_brief_jobs WHERE id=?',(job['id'],)).fetchone()
            if job['lease_owner']!=owner:
                raise RuntimeError('Brief job lease was taken over by another runner')
            rows=db.execute('''SELECT id,observed_at FROM following_events
                WHERE topic_id=? AND id>? AND id<=? ORDER BY id ASC LIMIT ?''',
                (topic,job['last_scanned_id'],job['watermark_id'],batch_size)).fetchall()
            selected=[]
            for row in rows:
                if _time(row['observed_at'])<_time(until):
                    db.execute('''INSERT OR IGNORE INTO research_brief_job_events(job_id,event_id)
                        VALUES (?,?)''',(job['id'],row['id']))
                    selected.append(row['id'])
            last=rows[-1]['id'] if rows else job['watermark_id']
            # A scan can span several expensive batches. Extend the lease
            # from the current clock, otherwise a healthy active runner can
            # lose ownership after the original 120-second lease expires.
            renewed_deadline=(_time()+timedelta(seconds=120)).isoformat()
            db.execute('''UPDATE research_brief_jobs SET last_scanned_id=?,
                lease_until=?,updated_at=? WHERE id=?''',
                (last,renewed_deadline,utc_now(),job['id']))
            db.execute('''INSERT INTO research_brief_job_audit
                (job_id,topic_id,action,from_cursor,to_cursor,event_count,created_at)
                VALUES (?,?,?,?,?,?,?)''',
                (job['id'],topic,'scan',job['last_scanned_id'],last,len(selected),utc_now()))
            db.commit()
            if not rows or last>=job['watermark_id']:
                db.execute('BEGIN IMMEDIATE')
                latest=db.execute('SELECT * FROM research_brief_jobs WHERE id=?',(job['id'],)).fetchone()
                payload=_finish(db,topic,cadence,begin,until,chain,latest,backend=backend,
                    model_config=model_config,use_model=use_model,
                    confirm_transfer=confirm_transfer,model_callable=model_callable)
                db.commit()
                from .research_continuity import read_brief
                result=read_brief(db,topic,payload['id'],chain,cadence=cadence)
                result.update(state='complete',job_id=job['id'],
                              late_events_recorded=payload['amendments'])
                return result
        db.execute('BEGIN IMMEDIATE')
        db.execute('''UPDATE research_brief_jobs SET lease_owner='',lease_until='',updated_at=?
            WHERE id=? AND lease_owner=?''',(utc_now(),job['id'],owner))
        db.commit()
        return {'state':'processing','job_id':job['id'],
                'last_scanned_id':last,'watermark_id':job['watermark_id']}
    except Exception as exc:
        db.rollback()
        db.execute('''UPDATE research_brief_jobs SET state='failed',lease_owner='',
            lease_until='',last_error=?,updated_at=? WHERE topic_id=? AND cadence=? AND period_end=?''',
            (type(exc).__name__,utc_now(),topic,cadence,until))
        db.commit()
        raise
