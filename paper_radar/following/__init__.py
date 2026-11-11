# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Durable, per-topic literature following with conservative coverage accounting.

All events are append-only and the inbox is an idempotent projection. A source
checkpoint advances only after its observations and events have been committed.
A partial, cached or saturated query never claims complete coverage.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import re
import sqlite3
from typing import Iterable

from ..models import Paper

MAX_EVENT_PAGE = 100
ISO_UTC = '%Y-%m-%dT%H:%M:%S+00:00'


def utc_now(now=None) -> str:
    t = now or datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse_time(raw: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _hash(data) -> str:
    return sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':')).encode('utf-8')).hexdigest()


def query_fingerprint(config: dict, source: str) -> str:
    """A changed query starts a new window, never reuses an old checkpoint."""
    return _hash({'source':source, 'keywords':config.get('keywords',[]),
                  'categories':config.get('categories',[]),
                  'goal':config.get('research_goal',''),
                  'max_queries_per_source':config.get('max_queries_per_source',8),
                  'max_results':config.get('max_results',30),
                  'lookback_days':config.get('lookback_days',7)})


def preference(db: sqlite3.Connection, topic_id: str) -> dict:
    row=db.execute('SELECT * FROM following_preferences WHERE topic_id=?', (topic_id,)).fetchone()
    if row is None:
        return {'enabled':True,'notify_new':True,'notify_updated':True,
                'email_enabled':False,'email_to':''}
    row=dict(row)
    return {key:bool(row[key]) for key in ('enabled','notify_new','notify_updated','email_enabled')} | {
        'email_to':row['email_to']}


def update_preference(db, topic_id: str, payload: dict) -> dict:
    from ..core import TOPIC_ID_RE
    if not TOPIC_ID_RE.fullmatch(topic_id):
        raise ValueError('研究主题无效')
    before=preference(db,topic_id)
    allowed={'enabled','notify_new','notify_updated','email_enabled','email_to'}
    if not isinstance(payload,dict) or set(payload)-allowed:
        raise ValueError('订阅参数无效')
    result={**before, **payload}
    for key in allowed-{'email_to'}:
        if type(result[key]) is not bool:
            raise ValueError('提醒开关必须为明确的开启或关闭')
    recipient=str(result['email_to'] or '').strip()
    if recipient and (len(recipient)>254 or not re.fullmatch(r'[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+',recipient)):
        raise ValueError('邮件地址格式无效')
    if result['email_enabled'] and not recipient:
        raise ValueError('开启邮件提醒前需填写邮箱地址')
    result['email_to']=recipient
    db.execute('''INSERT INTO following_preferences
        (topic_id,enabled,notify_new,notify_updated,email_enabled,email_to,changed_at)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(topic_id) DO UPDATE SET
        enabled=excluded.enabled,notify_new=excluded.notify_new,
        notify_updated=excluded.notify_updated,email_enabled=excluded.email_enabled,
        email_to=excluded.email_to,changed_at=excluded.changed_at''',
        (topic_id,*[int(result[k]) for k in ('enabled','notify_new','notify_updated','email_enabled')],
         recipient,utc_now()))
    return result


def plan_windows(db: sqlite3.Connection, config: dict, now=None) -> dict:
    """Plan each source independently. No cursor is advanced here."""
    topic=str(config.get('active_topic_id') or 'default')
    if not preference(db,topic)['enabled']:
        return {}
    end=_parse_time(utc_now(now))
    assert end is not None
    windows={}
    for source in config.get('sources', []):
        finger=query_fingerprint(config,source)
        row=db.execute('SELECT last_success,query_hash FROM following_checkpoints WHERE topic_id=? AND source_name=?',
                       (topic,source)).fetchone()
        cutoff=end-timedelta(days=max(1,int(config.get('lookback_days',7))))
        if row and row['query_hash']==finger:
            previous=_parse_time(row['last_success'])
            if previous and previous < end:
                cutoff=min(cutoff,previous-timedelta(hours=48))
        # Do not silently discard a large interrupted period. Source caps may
        # leave coverage pending, which is then shown to the user.
        windows[source]={'start':utc_now(cutoff), 'end':utc_now(end),
                         'query_hash':finger, 'fresh_start':not bool(row and row['query_hash']==finger)}
    return windows


def watched_arxiv_ids(db,topic_id: str,limit=24) -> list[str]:
    """Rotate old arXiv works through a bounded ID-list version check."""
    rows=db.execute('''SELECT source_id FROM following_observations
        WHERE topic_id=? AND source_name='arxiv'
        ORDER BY observed_at ASC, source_id ASC LIMIT ?''',(topic_id,max(1,min(30,int(limit))))).fetchall()
    return [row['source_id'] for row in rows if re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-zA-Z.\-]+/\d{7})',row['source_id'])]


def _source_id(p: Paper, name: str) -> str:
    value=str(p.paper_id or '').strip()
    if not value or len(value)>180:return ''
    if name == 'arxiv':
        value=re.sub(r'v\d+$','',value)
    return value


def _snapshot(p: Paper) -> dict:
    from ..core import normalize_doi
    return {'title':str(p.title or '').strip()[:400],
            'abstract':str(p.abstract or '').strip()[:12000],
            'doi':normalize_doi(p.doi), 'updated':str(p.updated or '')[:60],
            'published':str(p.published or '')[:60],
            'venue':str(p.venue or '')[:250]}


def _event_kind(old: dict | None, new: dict, *, first_in_topic=False) -> str | None:
    if old is None:
        return 'new' if first_in_topic else None
    if old==new:
        return None
    if new['doi'] and new['doi']!=old.get('doi') and not old.get('doi'):
        return 'published_version'
    if re.search(r'\b(withdrawn|retracted)\b',new['title'],re.I) and not re.search(r'\b(withdrawn|retracted)\b',old.get('title',''),re.I):
        return 'withdrawal_signal'  # Title signal, never a confirmed retraction.
    if (_parse_time(new['updated']) and _parse_time(old.get('updated',''))
        and _parse_time(new['updated']) > _parse_time(old['updated'])):
        return 'revised'
    return 'metadata_updated'


def record_cycle(db: sqlite3.Connection, config: dict, observed: Iterable[Paper],
                 stored: Iterable[Paper], source_rows: list[dict], *,
                 new_to_topic: set[str] | None=None, now=None) -> dict:
    """Persist observations, events, inbox and checkpoints within caller transaction.

    Only sources with successful, non-partial, unsaturated, non-cache queries
    advance to the current search end. A stale result never erases a newer
    observed revision. Zero-result successful queries still advance.
    """
    topic=str(config.get('active_topic_id') or 'default')
    if not preference(db,topic)['enabled']:
        return {'enabled':False,'events':0,'pending_sources':0}
    windows=config.get('_following_windows') or {}
    if not windows or not source_rows:
        return {'enabled':True,'events':0,'pending_sources':0,'not_tracked':True}
    start=utc_now(now)
    old_topic=new_to_topic or set()
    saved_ids={p.paper_id for p in stored}
    by_source={str(row['name']):row for row in source_rows if isinstance(row,dict) and row.get('name')}
    created=0
    for p in observed:
        candidates=[x for x in str(p.source or '').split(',') if x in by_source]
        for name in candidates[:3]:
            source_id=_source_id(p,name)
            if not source_id:continue
            # Only track a previously observed/followed work or one accepted
            # into this topic. Unrelated historical search hits are ignored.
            previous=db.execute('''SELECT * FROM following_observations
                WHERE topic_id=? AND source_name=? AND source_id=?''',
                (topic,name,source_id)).fetchone()
            if not previous and p.paper_id not in saved_ids:
                continue
            snap=_snapshot(p)
            signature=_hash(snap)
            old=json.loads(previous['snapshot_json']) if previous and 'snapshot_json' in previous.keys() else None
            if previous and previous['signature']==signature:
                db.execute('''UPDATE following_observations SET observed_at=?
                    WHERE topic_id=? AND source_name=? AND source_id=?''',
                    (start,topic,name,source_id))
                continue
            if previous:
                previous_version=db.execute('''SELECT snapshot_json FROM following_versions
                    WHERE topic_id=? AND source_name=? AND source_id=? AND signature=?''',
                    (topic,name,source_id,previous['signature'])).fetchone()
                old=json.loads(previous_version['snapshot_json']) if previous_version else None
                # Older API replies never move an observed work backwards.
                old_updated=_parse_time((old or {}).get('updated',''))
                new_updated=_parse_time(snap['updated'])
                if old_updated and (new_updated is None or new_updated < old_updated):
                    continue
            kind=_event_kind(old,snap,first_in_topic=p.paper_id in old_topic)
            db.execute('''INSERT OR IGNORE INTO following_versions
                (topic_id,source_name,source_id,paper_id,signature,source_updated,observed_at,snapshot_json)
                VALUES (?,?,?,?,?,?,?,?)''',
                (topic,name,source_id,p.paper_id,signature,snap['updated'],start,
                 json.dumps(snap,ensure_ascii=False)))
            db.execute('''INSERT INTO following_observations
                (topic_id,source_name,source_id,paper_id,signature,source_updated,
                 doi,title,published,venue,observed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(topic_id,source_name,source_id) DO UPDATE SET
                paper_id=excluded.paper_id,signature=excluded.signature,
                source_updated=excluded.source_updated,doi=excluded.doi,title=excluded.title,
                published=excluded.published,venue=excluded.venue,observed_at=excluded.observed_at''',
                (topic,name,source_id,p.paper_id,signature,snap['updated'],snap['doi'],
                 snap['title'],snap['published'],snap['venue'],start))
            if not kind:
                continue
            inserted=db.execute('''INSERT OR IGNORE INTO following_events
                (topic_id,source_name,source_id,paper_id,kind,signature,observed_at,title,details_json)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (topic,name,source_id,p.paper_id,kind,signature,start,snap['title'],
                 json.dumps({'old':old,'current':snap},ensure_ascii=False)))
            if inserted.rowcount:
                created+=1
                prefs=preference(db,topic)
                alert=prefs['notify_new'] if kind=='new' else prefs['notify_updated']
                if alert:
                    event_id=db.execute('SELECT last_insert_rowid()').fetchone()[0]
                    db.execute('''INSERT OR IGNORE INTO following_notifications
                        (topic_id,event_id,created_at) VALUES (?,?,?)''',(topic,event_id,start))
    pending=0
    for name,row in by_source.items():
        window=windows.get(name)
        if not window:continue
        queries=row.get('queries') or []
        cached=any(q.get('cache_state') not in (None,'disabled','miss') for q in queries)
        incomplete=any(q.get('saturated') or q.get('truncated') for q in queries)
        complete=bool(row.get('ok')) and not row.get('partial') and not row.get('timed_out') and not cached and not incomplete
        if not complete:pending+=1
        code=('SOURCE_FAILED' if not row.get('ok') else 'SATURATED' if incomplete else
              'CACHED' if cached else 'PARTIAL') if not complete else ''
        previous=db.execute('''SELECT last_success,query_hash,fail_count FROM following_checkpoints
            WHERE topic_id=? AND source_name=?''',(topic,name)).fetchone()
        prior_success=previous['last_success'] if previous and previous['query_hash']==window['query_hash'] else ''
        failures=0 if complete else (int(previous['fail_count'])+1 if previous else 1)
        db.execute('''INSERT INTO following_checkpoints
            (topic_id,source_name,query_hash,last_success,last_attempt,window_start,window_end,
             status,error_code,fail_count,result_count)
            VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(topic_id,source_name) DO UPDATE SET
            query_hash=excluded.query_hash,last_success=excluded.last_success,
            last_attempt=excluded.last_attempt,window_start=excluded.window_start,
            window_end=excluded.window_end,status=excluded.status,error_code=excluded.error_code,
            fail_count=excluded.fail_count,result_count=excluded.result_count''',
            (topic,name,window['query_hash'],window['end'] if complete else prior_success,
             start,window['start'],window['end'],'complete' if complete else 'pending',code,
             failures,int(row.get('count',0))))
    db.execute('''INSERT INTO following_runs(topic_id,started_at,completed_at,status,source_json,discovered,events_created)
        VALUES (?,?,?,?,?,?,?)''',
        (topic,start,utc_now(now),'partial' if pending else 'complete',
         json.dumps([{'name':name,'ok':bool(r.get('ok')),'partial':bool(r.get('partial')),
                      'count':int(r.get('count',0)),'error':str(r.get('error_code',''))[:60]}
                     for name,r in by_source.items()],ensure_ascii=False),
         len(saved_ids),created))
    return {'enabled':True,'events':created,'pending_sources':pending,
            'checked_sources':len(by_source)}


def status(db: sqlite3.Connection,topic_id: str, *, limit=40) -> dict:
    limit=max(1,min(MAX_EVENT_PAGE,int(limit)))
    pref=preference(db,topic_id)
    checkpoints=[dict(r) for r in db.execute('''SELECT * FROM following_checkpoints
        WHERE topic_id=? ORDER BY source_name''',(topic_id,))]
    events=[]
    for row in db.execute('''SELECT e.id,e.paper_id,e.source_name,e.kind,e.title,e.observed_at,
        n.id AS notification_id,n.seen_at FROM following_events e
        LEFT JOIN following_notifications n ON n.event_id=e.id
        WHERE e.topic_id=? ORDER BY e.id DESC LIMIT ?''',(topic_id,limit)):
        events.append(dict(row))
    unread=db.execute('''SELECT COUNT(*) FROM following_notifications
        WHERE topic_id=? AND seen_at='' ''',(topic_id,)).fetchone()[0]
    run=db.execute('''SELECT * FROM following_runs WHERE topic_id=? ORDER BY id DESC LIMIT 1''',
                   (topic_id,)).fetchone()
    return {'preferences':pref,'checkpoints':checkpoints,'events':events,
            'unread':unread,'last_run':dict(run) if run else None,
            'pending_sources':sum(row['status']!='complete' for row in checkpoints)}


def mark_seen(db,topic_id: str, ids: list[int]) -> int:
    if not isinstance(ids,list) or len(ids)>100 or any(type(x) is not int or x<1 for x in ids):
        raise ValueError('提醒标识列表无效')
    changed=0
    for event_id in set(ids):
        changed+=db.execute('''UPDATE following_notifications SET seen_at=?
           WHERE topic_id=? AND event_id=? AND seen_at='' ''',
           (utc_now(),topic_id,event_id)).rowcount
    return changed


def digest(db,topic_id: str, *, unread_only=True, limit=50) -> str:
    predicate="AND n.seen_at=''" if unread_only else ''
    rows=db.execute(f'''SELECT e.* FROM following_events e
        JOIN following_notifications n ON n.event_id=e.id
        WHERE n.topic_id=? {predicate} ORDER BY e.id DESC LIMIT ?''',
        (topic_id,min(100,max(1,int(limit))))).fetchall()
    labels={'new':'新收录','revised':'新版本','metadata_updated':'元数据更新',
            'published_version':'正式发表线索','withdrawal_signal':'撤稿线索（待核实）'}
    lines=['# Paper-Radar 研究跟进','',f'研究主题：{topic_id}','',
           f'生成时间：{utc_now()}','',f'待处理：{len(rows)} 条','']
    for row in rows:
        lines.append(f"- **{labels.get(row['kind'],row['kind'])}** · {row['title']} · {row['source_name']} · {row['paper_id']}")
    if not rows:lines.append('暂无待处理更新。')
    return '\n'.join(lines)+'\n'
