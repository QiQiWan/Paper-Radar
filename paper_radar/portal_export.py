# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Account-scoped evidence ledger CSV, retaining scientific review caveats."""
from __future__ import annotations
import csv
from io import StringIO

HEADERS = ('论文ID', '论文标题', '证据ID', '数据集', '指标', '原文数值',
           '对照基线', '实验设置', 'PDF页码', '原文引文', '来源', '人工复核状态', '审核时间')


def _safe_cell(value) -> str:
    """Prevent spreadsheet formula execution, including whitespace prefixes."""
    text = str(value if value is not None else '').replace('\x00', '')
    # Some spreadsheet parsers ignore a leading BOM or zero-width mark before
    # their formula-prefix check. Prefix the original text rather than stripping
    # the author's content.
    probe = text.lstrip(' \t\r\n\f\v\ufeff\u200b\u200e\u200f')
    if probe.startswith(('=', '+', '-', '@')):
        text = "'" + text
    return text


def export_evidence(portal, uid: str, topic: str) -> bytes:
    """Export live-PDF verified evidence and clearly labelled unverified imports.

    PDF-hash mismatch and foreign-topic entries never leave the workspace.
    Reviews are only current when matching the document fingerprint.
    """
    portal.available(uid, topic)
    with portal.workspace(uid) as db:
        verified = db.execute('''SELECT p.id paper_id,p.title,v.id evidence_id,
                v.dataset,v.metric,v.value,v.baseline,v.setting,v.page,v.quote,v.provenance,
                COALESCE(r.decision,'needs_review') review_status,COALESCE(r.reviewed_at,'') reviewed_at
            FROM verified_evidence v
            JOIN topic_papers tp ON tp.paper_id=v.paper_id AND tp.topic_id=?
            JOIN papers p ON p.id=v.paper_id
            JOIN pdf_documents d ON d.paper_id=v.paper_id AND d.sha256=v.sha256
            LEFT JOIN evidence_reviews r ON r.evidence_id=v.id AND r.sha256=v.sha256
            ORDER BY p.id,v.page,v.id LIMIT 2001''', (topic,)).fetchall()
        imported = db.execute('''SELECT p.id paper_id,p.title,e.id evidence_id,
                e.dataset,e.metric,e.value,e.baseline,e.setting,e.page,e.quote,e.provenance
            FROM evidence e JOIN topic_papers tp ON tp.paper_id=e.paper_id AND tp.topic_id=?
            JOIN papers p ON p.id=e.paper_id
            ORDER BY p.id,e.page,e.id LIMIT 2001''', (topic,)).fetchall()
    if len(verified) + len(imported) > 2000:
        raise ValueError('当前主题证据超过 2000 条，请缩小范围或联系管理员分批导出')
    out = StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(HEADERS)
    for record in verified:
        row = [record[x] for x in ('paper_id','title','evidence_id','dataset','metric','value',
                                   'baseline','setting','page','quote','provenance')]
        row.extend([record['review_status'],record['reviewed_at']])
        writer.writerow([_safe_cell(x) for x in row])
    for record in imported:
        row = [record[x] for x in ('paper_id','title','evidence_id','dataset','metric','value',
                                   'baseline','setting','page','quote','provenance')]
        row.extend(['unverified_import', ''])
        writer.writerow([_safe_cell(x) for x in row])
    return ('\ufeff' + out.getvalue()).encode('utf-8')
