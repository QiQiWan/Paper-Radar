# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Immutable, account-scoped snapshots of evidence-based synthesis reports.

Generation is performed only by an authorized background job. HTTP GET uses
read-only queries; opening a report never makes a new model call or snapshot.
"""
from __future__ import annotations
import hashlib
import json
import re
import time
import uuid

REPORT_ID = re.compile(r'^[0-9a-f]{32}$')
MAX_MARKDOWN_BYTES = 300_000


def create_report(portal, uid: str, topic_id: str, *, job_id: int | None = None) -> dict:
    from .portal_research import synthesis
    data = synthesis(portal, uid, topic_id)
    summary = data['summary']
    raw_summary = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    markdown = data['markdown']
    if len(markdown.encode('utf-8')) > MAX_MARKDOWN_BYTES or len(raw_summary.encode('utf-8')) > MAX_MARKDOWN_BYTES:
        raise ValueError('研究报告超过预览上限，请缩小主题范围后重新生成')
    digest = hashlib.sha256(raw_summary.encode('utf-8')).hexdigest()
    report_id = uuid.uuid4().hex
    created = int(time.time())
    with portal.workspace(uid) as db:
        if not db.execute('SELECT 1 FROM topics WHERE id=?',(topic_id,)).fetchone():
            raise ValueError('研究主题已经删除')
        db.execute('''INSERT INTO research_reports
            (id,topic_id,job_id,generated_at,schema_version,source_digest,paper_count,finding_count,summary_json,markdown)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (report_id,topic_id,job_id,created,1,digest,
             int(summary.get('paper_count',0)),int(summary.get('finding_count',0)),raw_summary,markdown))
        # Bounded account history. Never delete a different topic's reports.
        db.execute('''DELETE FROM research_reports WHERE id IN (
             SELECT id FROM research_reports WHERE topic_id=? ORDER BY generated_at DESC,id DESC LIMIT -1 OFFSET 40)''',
             (topic_id,))
    return {'id':report_id,'topic_id':topic_id,'job_id':job_id,'generated_at':created,
            'source_digest':digest,'paper_count':int(summary.get('paper_count',0)),
            'finding_count':int(summary.get('finding_count',0)),
            'comparison_count':len(summary.get('comparisons',[]))}


def list_reports(portal, uid: str, topic_id: str, *, limit=30) -> list[dict]:
    portal.available(uid,topic_id)
    limit=max(1,min(100,int(limit)))
    with portal.workspace(uid) as db:
        rows=db.execute('''SELECT id,topic_id,job_id,generated_at,schema_version,source_digest,
            paper_count,finding_count FROM research_reports WHERE topic_id=?
            ORDER BY generated_at DESC,id DESC LIMIT ?''',(topic_id,limit)).fetchall()
    return [dict(r) for r in rows]


def read_report(portal, uid: str, report_id: str) -> dict | None:
    if not REPORT_ID.fullmatch(str(report_id)):
        return None
    with portal.workspace(uid) as db:
        row=db.execute('SELECT * FROM research_reports WHERE id=?',(report_id,)).fetchone()
    if not row:return None
    data=dict(row)
    try:data['summary']=json.loads(data.pop('summary_json'))
    except (ValueError,TypeError):data['summary']={}
    return data
