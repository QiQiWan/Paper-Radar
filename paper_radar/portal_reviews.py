# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Per-account, PDF-hash-bound evidence and pairwise user review journal."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import re

from .comparison_policy import pair_assessment, pair_key, validate_conditions
from . import portal_research

ID = re.compile(r'^[0-9a-f]{64}$')
DECISIONS = {'approved', 'rejected', 'needs_review'}
PAIR_DECISIONS = {'comparable', 'incompatible', 'needs_review'}


def _checked(uid, topic, portal, pid, evidence_id) -> dict:
    if not isinstance(evidence_id, str) or not ID.fullmatch(evidence_id):
        raise ValueError('无效的证据标识')
    if not isinstance(pid, str) or not 1 <= len(pid) <= 200:
        raise ValueError('无效的论文标识')
    portal_research.check_member(portal, uid, topic, pid)
    for row in portal_research.records_for_paper(portal, uid, topic, pid):
        if row['evidence_id'] == evidence_id:
            return row
    raise ValueError('证据不存在、PDF 已变更或原文引文失效')


def _comment(note, *, required=False):
    if not isinstance(note, str) or len(note) > 360 or ('\x00' in note):
        raise ValueError('复核备注无效或超过 360 字符')
    text = note.strip()
    if required and len(text) < 4:
        raise ValueError('请记录简短的人工复核依据')
    return text


def review_evidence(portal, uid, topic, pid, evidence_id, decision,
                    conditions=None, note='') -> dict:
    if decision not in DECISIONS:
        raise ValueError('未知证据复核决定')
    row = _checked(uid, topic, portal, pid, evidence_id)
    checked = validate_conditions(conditions or {}, require_complete=decision == 'approved')
    note = _comment(note, required=decision in ('approved', 'rejected'))
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    # Revalidate source hash and row while holding the SQLite write lock.
    # If a different process replaced the PDF during the request, reject it.
    with portal.workspace(uid) as conn:
        current = conn.execute('''SELECT e.id,e.sha256,d.sha256 AS current_sha
            FROM verified_evidence e JOIN pdf_documents d ON d.paper_id=e.paper_id
            JOIN topic_papers t ON t.paper_id=e.paper_id
            WHERE e.id=? AND e.paper_id=? AND t.topic_id=?''',
            (evidence_id, pid, topic)).fetchone()
        if not current or current['sha256'] != row['sha256'] or current['current_sha'] != row['sha256']:
            raise ValueError('PDF 或证据已更新，须刷新后重新复核')
        conn.execute('''INSERT INTO evidence_reviews
            (evidence_id,paper_id,sha256,decision,conditions_json,note,reviewer_id,reviewed_at)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(evidence_id) DO UPDATE SET
            paper_id=excluded.paper_id,sha256=excluded.sha256,decision=excluded.decision,
            conditions_json=excluded.conditions_json,note=excluded.note,
            reviewer_id=excluded.reviewer_id,reviewed_at=excluded.reviewed_at''',
            (evidence_id,pid,row['sha256'],decision,json.dumps(checked,ensure_ascii=False),
             note,uid,now))
        conn.execute('''INSERT INTO evidence_review_history (evidence_id,paper_id,sha256,
            decision,conditions_json,note,reviewer_id,reviewed_at)
            VALUES (?,?,?,?,?,?,?,?)''',
            (evidence_id,pid,row['sha256'],decision,json.dumps(checked,ensure_ascii=False),note,uid,now))
        # Pair review is invalid if either review's conditions or decision changed.
        conn.execute('''DELETE FROM comparison_pair_reviews
            WHERE evidence_a_id=? OR evidence_b_id=?''', (evidence_id,evidence_id))
    return {'evidence_id': evidence_id, 'decision': decision,
            'conditions': checked, 'reviewed_at': now,
            'warning': '用户已登记人工复核结论；未自动验证论文复现实验或统计显著性'}


def review_pair(portal, uid, topic, a_id, b_id, decision, note='') -> dict:
    if decision not in PAIR_DECISIONS:
        raise ValueError('未知可比性判定')
    if not all(isinstance(x, str) and ID.fullmatch(x) for x in (a_id,b_id)):
        raise ValueError('无效的证据标识')
    portal.available(uid,topic)
    # Pair decisions are topic-specific even when both papers appear in several topics.
    key = topic + '|' + pair_key(a_id,b_id)
    evidence = {r['evidence_id']:r for r in portal_research.records(portal,uid,topic)}
    if a_id not in evidence or b_id not in evidence:
        raise ValueError('证据不属于当前主题或已失效')
    a,b=evidence[a_id],evidence[b_id]
    if a['paper_id']==b['paper_id']:
        raise ValueError('不能比较同一篇论文中的两条证据')
    assess=pair_assessment(a,b)
    if decision=='comparable' and not assess['eligible']:
        raise ValueError('条件不满足可比性复核要求：'+'；'.join(assess['blockers'])[:240])
    note=_comment(note,required=True)
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    ids=sorted((a_id,b_id))
    by_id={a_id:a,b_id:b}
    # All writes remain scoped to this account's separate workspace.
    with portal.workspace(uid) as conn:
        # The preflight may race with a different review or PDF replacement.
        # Acquire the writer lock before rechecking *both* current review
        # conditions and source fingerprints, then commit the pair atomically.
        conn.execute('BEGIN IMMEDIATE')
        for identifier in ids:
            row=by_id[identifier]
            cur=conn.execute('''SELECT e.sha256,d.sha256 AS current_sha,r.decision,
                    r.conditions_json AS conditions_json
                FROM verified_evidence e JOIN pdf_documents d ON d.paper_id=e.paper_id
                LEFT JOIN evidence_reviews r ON r.evidence_id=e.id AND r.sha256=e.sha256
                WHERE e.id=?''',(identifier,)).fetchone()
            if not cur or cur['sha256']!=row['sha256'] or cur['current_sha']!=row['sha256']:
                raise ValueError('论文证据已经更新，请重新审核')
            if decision=='comparable':
                if cur['decision']!='approved':
                    raise ValueError('双方证据必须先完成关系复核')
                try:
                    current_conditions=json.loads(cur['conditions_json'])
                except (TypeError,ValueError):
                    raise ValueError('实验条件已失效，请重新审核') from None
                if current_conditions!=row.get('review_conditions',{}):
                    raise ValueError('实验条件已更新，请刷新后重新核对')
        conn.execute('''INSERT INTO comparison_pair_reviews
            (id,topic_id,evidence_a_id,evidence_b_id,sha_a,sha_b,
             decision,note,reviewer_id,reviewed_at) VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET topic_id=excluded.topic_id,
             sha_a=excluded.sha_a,sha_b=excluded.sha_b,decision=excluded.decision,
             note=excluded.note,reviewer_id=excluded.reviewer_id,
             reviewed_at=excluded.reviewed_at''',
            (key,topic,*ids,by_id[ids[0]]['sha256'],by_id[ids[1]]['sha256'],
             decision,note,uid,now))
        conn.execute('''INSERT INTO comparison_pair_review_history
            (topic_id,evidence_a_id,evidence_b_id,sha_a,sha_b,decision,note,reviewer_id,reviewed_at)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (topic,*ids,by_id[ids[0]]['sha256'],by_id[ids[1]]['sha256'],decision,note,uid,now))
    return {'pair_id':key,'decision':decision,'assessment':assess,
            'warning':'人工确认的对比关系仅适用于记录的实验条件，不能自动推断显著性或性能排名'}


def pair_reviews(portal, uid, topic) -> list[dict]:
    portal.available(uid,topic)
    with portal.workspace(uid) as conn:
        return [dict(r) for r in conn.execute('''SELECT * FROM comparison_pair_reviews
            WHERE topic_id=? ORDER BY reviewed_at DESC LIMIT 2000''',(topic,))]


def review_history(portal, uid, topic, pid: str, limit: int = 100) -> dict:
    # Immutable user decisions in authorized workspace
    if not isinstance(limit,int) or not 1<=limit<=200:
        raise ValueError('Limit must be between 1 and 200')
    portal_research.check_member(portal,uid,topic,pid)
    with portal.workspace(uid) as db:
        rows=db.execute('''SELECT evidence_id,sha256,decision,conditions_json,
            note,reviewed_at FROM evidence_review_history WHERE paper_id=?
            ORDER BY id DESC LIMIT ?''',(pid,limit)).fetchall()
    return {'paper_id':pid,'topic_id':topic,'history':[
        {**{k:r[k] for k in ('evidence_id','sha256','decision','note','reviewed_at')},
         'conditions':json.loads(r['conditions_json'])} for r in rows],
        'warning':'历史记录可能引用已被覆盖的 PDF；只有最新有效的复核决定参与报告'}
