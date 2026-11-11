# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Auditable *real* paper-library pilot metrics.

No synthetic success counts. Undefined metrics stay null until real download,
independent gold annotation, active review timing and compatibility review exist.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import sqlite3
import zipfile

from .evidence_batch import init_schema as batch_schema, comparison_matrix, _current
from .downloads.manager import resolve_saved_file
from .fulltext import paper_status
from .abstract_enrichment import abstract_quality

QUALITY_FIELDS = ('dataset', 'metric', 'value', 'baseline', 'setting')
QUALITY_VALUES = frozenset(('correct', 'incorrect', 'uncertain', 'unchecked'))
COMPATIBILITY_FIELDS = ('metric_definition', 'dataset_version', 'split_protocol',
                        'unit_scale', 'task_geometry', 'budget_and_seeds')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _normalized(v):
    return ' '.join(str(v or '').casefold().split())


def _safe_csv(v):
    text = '' if v is None else str(v)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else text


def init_schema(db: sqlite3.Connection):
    batch_schema(db)
    db.execute('''CREATE TABLE IF NOT EXISTS pilot_field_checks (
        candidate_id TEXT PRIMARY KEY, topic_id TEXT NOT NULL,
        candidate_revision INTEGER NOT NULL, active_seconds REAL NOT NULL DEFAULT 0,
        labels_json TEXT NOT NULL, original_json TEXT NOT NULL DEFAULT '{}',
        corrected_json TEXT NOT NULL DEFAULT '{}', recorded_at TEXT NOT NULL
    )''')
    existing={row['name'] for row in db.execute('PRAGMA table_info(pilot_field_checks)')}
    for column in ('original_json','corrected_json'):
        if column not in existing:
            db.execute(f"ALTER TABLE pilot_field_checks ADD COLUMN {column} TEXT NOT NULL DEFAULT '{{}}'")
    db.execute('''CREATE TABLE IF NOT EXISTS pilot_group_reviews (
        topic_id TEXT NOT NULL, group_key TEXT NOT NULL,
        fingerprint TEXT NOT NULL, approved INTEGER NOT NULL,
        checks_json TEXT NOT NULL, note TEXT NOT NULL, reviewed_at TEXT NOT NULL,
        PRIMARY KEY(topic_id, group_key)
    )''')
    db.commit()


def record_field_check(db, topic_id: str, candidate_id: str, revision: int,
                       active_seconds: float, labels: dict, original_fields: dict, corrected_fields: dict):
    """Record reviewer-observed quality (never silently mark automatic output correct)."""
    init_schema(db)
    if (type(candidate_id) is not str or not re.fullmatch('[0-9a-f]{32}', candidate_id)
            or type(revision) is not int or revision < 2):
        raise ValueError('审核记录编号或版本无效')
    if not isinstance(active_seconds, (int, float)) or not 0 <= active_seconds <= 14400:
        raise ValueError('人工核对耗时应在 0 至 4 小时内')
    if type(labels) is not dict or set(labels) != set(QUALITY_FIELDS):
        raise ValueError('字段正确性记录不完整')
    if any(type(value) is not str or value not in QUALITY_VALUES for value in labels.values()):
        raise ValueError('字段正确性取值无效')
    for values in (original_fields,corrected_fields):
        if type(values) is not dict or set(values)!=set(QUALITY_FIELDS):
            raise ValueError('字段原始数据记录不完整')
        if any(type(value) is not str or len(value)>500 for value in values.values()):
            raise ValueError('字段原始数据长度无效')
    row = db.execute('''SELECT revision,decision FROM evidence_batch_candidates
        WHERE id=? AND topic_id=?''', (candidate_id, topic_id)).fetchone()
    if not row or row['revision'] != revision or row['decision'] not in ('approved', 'rejected'):
        raise ValueError('人工核对已变化，请刷新后重新保存试点记录')
    db.execute('''INSERT INTO pilot_field_checks
       (candidate_id,topic_id,candidate_revision,active_seconds,labels_json,
        original_json,corrected_json,recorded_at)
       VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET
       candidate_revision=excluded.candidate_revision,
       active_seconds=excluded.active_seconds, labels_json=excluded.labels_json,
       original_json=excluded.original_json, corrected_json=excluded.corrected_json,
       recorded_at=excluded.recorded_at''',
       (candidate_id, topic_id, revision, round(float(active_seconds), 2),
        json.dumps(labels, sort_keys=True),json.dumps(original_fields, ensure_ascii=False),
        json.dumps(corrected_fields, ensure_ascii=False),_now()))
    db.commit()
    return {'saved': True, 'active_seconds': round(float(active_seconds), 2)}


def _group_key(group):
    return _normalized(group['metric']) + '\x1f' + _normalized(group['dataset'])


def _group_fingerprint(group):
    records = sorted((str(x['paper_id']), str(x['pdf_sha256']), int(x['page']),
                      _normalized(x['metric']), _normalized(x['value']),
                      _normalized(x['baseline']), _normalized(x['setting']), str(x['anchor_id']))
                     for x in group['entries'])
    return sha256(json.dumps(records, ensure_ascii=False).encode('utf-8')).hexdigest()


def group_reviews(db, topic_id: str, db_path: Path):
    """Potential groups and manually approved *current* groups are different counts."""
    init_schema(db)
    groups = comparison_matrix(db, topic_id, db_path=db_path)['groups']
    reviews = {row['group_key']: dict(row) for row in db.execute(
        'SELECT * FROM pilot_group_reviews WHERE topic_id=?', (topic_id,))}
    output=[]
    for group in groups:
        key=_group_key(group)
        fp=_group_fingerprint(group)
        r=reviews.get(key)
        current=bool(r and r['fingerprint']==fp)
        output.append({**group, 'group_key': key, 'fingerprint': fp,
                       'method_review_status': ('confirmed' if current and r['approved'] else
                                                'rejected' if current and not r['approved'] else
                                                'stale_review' if r else 'unreviewed'),
                       'method_review_note': r['note'] if current else ''})
    return output


def review_group(db, topic_id: str, db_path: Path, key: str, fingerprint: str,
                 checks: dict, approved: bool, note: str):
    init_schema(db)
    if type(key) is not str or len(key)>260 or type(fingerprint) is not str or not re.fullmatch('[0-9a-f]{64}',fingerprint):
        raise ValueError('对照组编号无效')
    if type(approved) is not bool or type(checks) is not dict or set(checks) != set(COMPATIBILITY_FIELDS):
        raise ValueError('可比性核对选项不完整')
    if any(type(v) is not bool for v in checks.values()):
        raise ValueError('可比性核对必须逐项确认')
    if type(note) is not str or not 15 <= len(note.strip()) <= 1200:
        raise ValueError('请填写至少 15 字的跨论文可比性核对说明')
    if approved and not all(checks.values()):
        raise ValueError('仍有未核对通过的比较条件，不能确认有效对照组')
    valid = next((group for group in group_reviews(db, topic_id, db_path) if group['group_key']==key),None)
    if not valid or valid['fingerprint']!=fingerprint:
        raise ValueError('对照组证据已改变，请刷新并重新检查')
    db.execute('''INSERT INTO pilot_group_reviews
        (topic_id,group_key,fingerprint,approved,checks_json,note,reviewed_at)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(topic_id,group_key) DO UPDATE SET
        fingerprint=excluded.fingerprint,approved=excluded.approved,
        checks_json=excluded.checks_json,note=excluded.note,
        reviewed_at=excluded.reviewed_at''',
        (topic_id,key,fingerprint,int(approved),json.dumps(checks,sort_keys=True),note.strip(),_now()))
    db.commit()
    return {'saved':True,'approved':approved}


def _latest_papers(db, topic_id):
    """One denominator per unique paper, using its most recent pilot attempt."""
    sql='''SELECT i.paper_id,i.state,i.stage,i.attempts,i.candidate_count,r.id AS run_id,
        p.title,p.source,p.pdf_url,p.download_status,p.download_error,p.local_file,p.is_oa
        FROM evidence_batch_items i JOIN evidence_batch_runs r ON i.run_id=r.id
        JOIN papers p ON p.paper_id=i.paper_id WHERE r.topic_id=?
        ORDER BY r.created_at DESC,r.rowid DESC'''
    # SQLite rowid cannot be referenced as r.rowid after ORDER BY across all versions? SQLite accepts.
    seen=set()
    for row in db.execute(sql,(topic_id,)):
        pid=row['paper_id']
        if pid not in seen:
            seen.add(pid)
            yield dict(row)


def overview(db, topic_id: str, db_path: Path) -> dict:
    init_schema(db)
    all_count=db.execute('SELECT COUNT(*) FROM topic_papers WHERE topic_id=?',(topic_id,)).fetchone()[0]
    paper_rows=list(_latest_papers(db, topic_id))
    for row in paper_rows:
        # Only a valid on-disk PDF counts as obtained; a metadata OA flag does not.
        row['pdf_present'] = bool(resolve_saved_file(Path(db_path), row['local_file']))
        if row['pdf_present']:
            try:
                status=paper_status(db,row['paper_id'],db_path=Path(db_path))
                row['fulltext_indexed']=bool(status.get('indexed'))
            except (OSError,ValueError,sqlite3.Error):
                row['fulltext_indexed']=False
        else:
            row['fulltext_indexed']=False
        row['error_category'] = ('not_offered' if row['state']=='no_pdf' and row['download_status']=='unavailable'
                                 else 'network_or_download' if row['state']=='no_pdf'
                                 else 'unreadable' if row['state']=='no_text' else
                                 'failed' if row['state']=='failed' else '')
    # Metadata coverage is calculated over the *whole* active topic, not only
    # the batch-PDF subset; do not silently conflate short with missing.
    quality_counts={key:0 for key in ('missing','short','possibly_truncated','adequate')}
    for item in db.execute('''SELECT p.abstract FROM topic_papers tp JOIN papers p
          ON tp.paper_id=p.paper_id WHERE tp.topic_id=?''',(topic_id,)):
        quality_counts[abstract_quality(item['abstract'])]+=1
    count=len(paper_rows)
    pdf=sum(bool(x['pdf_present']) for x in paper_rows)
    indexed=sum(bool(x['fulltext_indexed']) for x in paper_rows)
    approvals=[];candidate_count=0;pending=0;stale=0;file_cache={}
    selected_ids={paper['paper_id'] for paper in paper_rows}
    for row in db.execute('''SELECT * FROM evidence_batch_candidates WHERE topic_id=?''',(topic_id,)):
        x=dict(row)
        if x['paper_id'] not in selected_ids:continue
        candidate_count+=1
        if not _current(db,x,Path(db_path),file_cache):
            stale+=1
        elif x['decision']=='approved':approvals.append(x)
        elif x['decision']=='pending':pending+=1
    checks=db.execute('''SELECT f.*,c.decision,c.revision,c.source FROM pilot_field_checks f
        JOIN evidence_batch_candidates c ON c.id=f.candidate_id
        WHERE f.topic_id=?''',(topic_id,)).fetchall()
    sessions=[];quality={name:{'correct':0,'incorrect':0,'uncertain':0,'unchecked':0} for name in QUALITY_FIELDS}
    quality_source={name:{'correct':0,'incorrect':0,'uncertain':0,'unchecked':0}
                    for name in ('model_verified_quote','local_excerpt','other')}
    for row in checks:
        if row['candidate_revision']!=row['revision']:continue
        labels=json.loads(row['labels_json'])
        for field in QUALITY_FIELDS:
            quality[field][labels.get(field,'unchecked')]+=1
            group=row['source'] if row['source'] in quality_source else 'other'
            quality_source[group][labels.get(field,'unchecked')]+=1
        sessions.append({'candidate_id':row['candidate_id'],'active_seconds':row['active_seconds'],
                         'source':row['source'],
                         'revision':row['candidate_revision'],
                         'original_fields':row['original_json'],
                         'corrected_fields':row['corrected_json'],**labels})
    graded=sum(q['correct']+q['incorrect'] for q in quality.values())
    correct=sum(q['correct'] for q in quality.values())
    total_seconds=sum(x['active_seconds'] for x in sessions if x['active_seconds']>0)
    timed=sum(x['active_seconds']>0 for x in sessions)
    groups=group_reviews(db,topic_id,Path(db_path))
    confirmed=sum(x['method_review_status']=='confirmed' for x in groups)
    result={
      'version':'1.50.1','topic_id':topic_id,'generated_at':_now(),
      'library_total':all_count,'pilot_selected_unique':count,
      'abstract_quality':quality_counts,
      'abstract_adequate_fraction':round(quality_counts['adequate']/all_count,4) if all_count else None,
      'downloaded_or_existing_pdf':pdf,
      'pdf_acquisition_rate':round(pdf/count,4) if count else None,
      'fulltext_indexed':indexed,
      'fulltext_index_rate':round(indexed/pdf,4) if pdf else None,
      'download_outcomes':{name:sum(x['error_category']==name for x in paper_rows)
                           for name in ('not_offered','network_or_download','unreadable','failed')},
      'candidate_total':candidate_count,'candidate_pending_valid':pending,
      'candidate_approved_valid':len(approvals),'candidate_stale':stale,
      'field_checks':quality,
      'quality_by_extraction_source':{source:{**counts,
          'measured_accuracy':(round(counts['correct']/(counts['correct']+counts['incorrect']),4)
              if counts['correct']+counts['incorrect'] else None)}
          for source,counts in quality_source.items()},
      'field_accuracy_on_reviewed_fields':round(correct/graded,4) if graded else None,
      'field_accuracy_denominator':graded,
      'field_recall':None, 'field_recall_reason':'需要额外独立标注完整实验字段，不能由待审核候选推断召回率',
      'timed_reviews':timed,
      'manual_review_seconds':round(total_seconds,2) if timed else None,
      'mean_active_review_seconds':round(total_seconds/timed,2) if timed else None,
      'potential_comparison_groups':len(groups),
      'method_confirmed_comparison_groups':confirmed,
      'notes':['下载率分母为实际批量提交的去重论文；只计入已存在且有效的本地 PDF。',
               '模型实验字段准确率仅按人工明确标注正确/错误的字段计数；未标注不视为正确。',
               '人工核对耗时仅为审核人员主动计时的数据，缺失时保持 null。',
               '有效对照组需要逐项核对评价指标、数据集、划分、尺度、任务、预算与随机重复。']}
    return {'summary':result,'papers':paper_rows,'sessions':sessions,'groups':groups}


def to_csv_bytes(rows,columns):
    f=io.StringIO(newline='');w=csv.writer(f);w.writerow(columns)
    for row in rows:w.writerow([_safe_csv(row.get(c,'')) for c in columns])
    return ('\ufeff'+f.getvalue()).encode('utf-8')


def export_zip(db,topic_id:str,db_path:Path)->bytes:
    info=overview(db,topic_id,db_path)
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('summary.json',json.dumps(info['summary'],ensure_ascii=False,indent=2))
        cols=['paper_id','title','source','run_id','state','stage','attempts','pdf_present','fulltext_indexed','download_status','error_category','download_error']
        z.writestr('papers.csv',to_csv_bytes(info['papers'],cols))
        cols=['candidate_id','revision','active_seconds','source','original_fields','corrected_fields',*QUALITY_FIELDS]
        z.writestr('field_review.csv',to_csv_bytes(info['sessions'],cols))
        cols=['metric','dataset','method_review_status','method_review_note','fingerprint']
        z.writestr('comparison_groups.csv',to_csv_bytes(info['groups'],cols))
        z.writestr('HOW_TO_READ.txt',
            'Pilot results reflect the actual connected library only.\n'
            'null = no valid measurements; zero = a measured zero.\n'
            'Field precision is measured only on independently checked labels.\n'
            'The cross-paper eligible group count requires manual method comparability approval.\n'
            'Check metadata abstract quality separately; short is heuristic, not necessarily incomplete.\n'
            'Review original PDF and personal data before sharing.\n')
    return out.getvalue()
