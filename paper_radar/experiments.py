# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Conservative, document-bound experiment extraction.

Every accepted result cites an exact quote inside a PDF chunk. The structured
fields are grounded in that same quote; results from different papers are not
implicitly comparable. No OCR, web retrieval, or user-supplied file paths.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from pathlib import Path
import csv

from .quality import normalized
from .evidence_relations import attribution_assessment
from . import fulltext

EXPERIMENT_VERSION = 'experimental-extraction-v1.14.0-1'
MAX_PROMPT_CHUNKS = 16
MAX_FINDINGS = 12
MAX_JSON_CHARS = 65000
FIELD_LIMITS = {'kind': 20, 'dataset': 120, 'metric': 120, 'value': 80,
                'baseline': 120, 'setting': 220, 'quote': 600}
VALID_KINDS = frozenset(('metric', 'ablation', 'efficiency'))
NUMBER = re.compile(r'(?<![\w])\d+(?:[.,]\d+)?(?:\s*%|\s*[×x])?', re.I)
EXPERIMENT_HINT = re.compile(r'\b(?:experiment|result|benchmark|ablation|baseline|performance|accuracy|error|'
                             r'dataset|speed|runtime|flops|memory|improv|compar|evaluation|table\s*\d+)\b', re.I)


def init_schema(conn):
    fulltext.init_schema(conn)
    conn.execute('''CREATE TABLE IF NOT EXISTS experimental_findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT NOT NULL, pdf_sha256 TEXT NOT NULL, cache_key TEXT NOT NULL,
        kind TEXT NOT NULL, dataset TEXT NOT NULL DEFAULT '',
        metric TEXT NOT NULL, value TEXT NOT NULL, baseline TEXT NOT NULL DEFAULT '',
        setting TEXT NOT NULL DEFAULT '', chunk_id INTEGER NOT NULL,
        page INTEGER NOT NULL, quote TEXT NOT NULL, extracted_at TEXT NOT NULL
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS ix_experimental_findings_paper ON experimental_findings(paper_id)')
    conn.commit()


def model_cache_key(document: dict, config: dict) -> str:
    payload = [EXPERIMENT_VERSION, document['pdf_sha256'],
               config.get('api_provider'), config.get('api_model'), config.get('api_base_url')]
    return sha256(json.dumps(payload, ensure_ascii=False).encode('utf-8')).hexdigest()


def read_findings(conn, paper_id: str, *, db_path: Path) -> dict:
    """Always revalidate PDF bytes, including after out-of-process replacement."""
    init_schema(conn)
    status = fulltext.paper_status(conn, paper_id, db_path=db_path)
    document = status.get('document') or {}
    if not status.get('indexed'):
        return {'paper_id': paper_id, 'status': 'stale' if document.get('state') == 'stale' else 'not_indexed',
                'findings': [], 'note': status.get('note') or '请先解析 PDF。'}
    rows = conn.execute('''SELECT kind,dataset,metric,value,baseline,setting,chunk_id,page,quote
        FROM experimental_findings WHERE paper_id=? AND pdf_sha256=? ORDER BY id''',
        (paper_id, document['pdf_sha256'])).fetchall()
    return {'paper_id': paper_id, 'status': 'ready' if rows else 'no_results',
            'findings': [dict(row) for row in rows],
            'note': '仅收录与原文片段逐字对应的结果；表格跨行关系、误差定义及实验可比性仍须人工核对。'}


def selected_experiment_chunks(conn, paper_id: str, *, max_items: int = MAX_PROMPT_CHUNKS) -> list[dict]:
    """Cover several pages and prefer experimental evidence over introductions."""
    rows = [dict(row) for row in conn.execute('''SELECT id,page,section,content FROM fulltext_chunks
             WHERE paper_id=? ORDER BY page,id''', (paper_id,))]
    if not rows:
        return []
    # Keep the first chunk for terminology/experimental context; diversity per page.
    scored = sorted(rows, key=lambda row: (
        -(10 if row['section'] in ('实验','结果') else 0)
        - min(8, len(EXPERIMENT_HINT.findall(row['content'])))
        - min(4, len(NUMBER.findall(row['content']))), row['page'], row['id']))
    seen_pages = set()
    diverse = []
    for row in scored:
        if row['page'] not in seen_pages:
            diverse.append(row)
            seen_pages.add(row['page'])
        if len(diverse) >= max_items:
            break
    for row in scored:
        if len(diverse) >= max_items:
            break
        if row['id'] not in {item['id'] for item in diverse}:
            diverse.append(row)
    return sorted(diverse[:max_items], key=lambda row:(row['page'],row['id']))


def safe_model_material(rows: list[dict]) -> tuple[list[dict], dict[int,dict]]:
    """Limit material sent to the cloud and validate only against sent text."""
    materials=[]
    by_id={}
    for row in rows[:MAX_PROMPT_CHUNKS]:
        text=str(row['content'])[:1050]
        if text:
            materials.append({'chunk_id':row['id'],'page':row['page'],
                              'section':row['section'],'text':text})
            by_id[row['id']]={'page':row['page'],'content':text}
    return materials, by_id


def _json_payload(raw):
    if not isinstance(raw,str) or len(raw)>MAX_JSON_CHARS:
        return None
    raw=raw.strip()
    if raw.startswith('```'):
        raw=re.sub(r'^```(?:json)?\s*','',raw,flags=re.I)
        raw=re.sub(r'\s*```$','',raw)
    try:return json.loads(raw)
    except (ValueError,TypeError):return None


def validate_experiments(raw: str, by_id: dict[int,dict]) -> list[dict]:
    """EvidenceVerifierAgent: strict continuous quote and exact field support.

    Values/metrics/datasets/baselines must appear verbatim in one quote. This
    rejects inferred percentages, conversions and clearly detectable
    table cross-row mistakes. Ambiguous layouts require human review.
    """
    payload = _json_payload(raw)
    if not isinstance(payload,dict) or not isinstance(payload.get('findings'),list):
        return []
    results=[]
    seen=set()
    for obj in payload['findings'][:MAX_FINDINGS * 3]:
        if not isinstance(obj,dict) or type(obj.get('chunk_id')) is not int or obj['chunk_id'] not in by_id:
            continue
        if any(not isinstance(obj.get(field,''),str) for field in FIELD_LIMITS):
            continue
        fields={key: obj.get(key,'').strip() for key in FIELD_LIMITS}
        if any(len(value)>FIELD_LIMITS[key] for key,value in fields.items()):
            continue
        if fields['kind'] not in VALID_KINDS or not fields['metric'] or not fields['value']:
            continue
        quote=fields['quote']
        if len(quote)<18 or not normalized(quote) in normalized(by_id[obj['chunk_id']]['content']):
            continue
        # All structured fields must be grounded in this same quotation.
        if any(normalized(fields[k]) not in normalized(quote)
               for k in ('dataset','metric','value','baseline','setting') if fields[k]):
            continue
        if not NUMBER.search(fields['value']):
            continue
        # A multi-row quote can contain every requested field while assigning
        # another row's value to the chosen dataset/baseline. Reject provable
        # mismatches; successful substring checking never verifies table layout.
        attribution=attribution_assessment(quote,dataset=fields['dataset'],
                 value=fields['value'],baseline=fields['baseline'])
        if not attribution['accepted']:
            continue
        quote_numbers={re.sub(r'\s+','',number.group().casefold()) for number in NUMBER.finditer(quote)}
        if any(re.sub(r'\s+','',number.group().casefold()) not in quote_numbers
               for number in NUMBER.finditer(fields['setting'])):
            continue
        signature=(obj['chunk_id'],normalized(fields['metric']),normalized(fields['value']),normalized(quote))
        if signature in seen:continue
        seen.add(signature)
        results.append({**fields,'chunk_id':obj['chunk_id'],'page':by_id[obj['chunk_id']]['page']})
        if len(results)==MAX_FINDINGS:break
    return results


def persist_findings(conn, paper_id: str, document: dict, cache_key: str, findings: list[dict]) -> dict:
    init_schema(conn)
    stamp=datetime.now(timezone.utc).isoformat(timespec='seconds')
    # Two-phase commit: do not replace valid findings on an empty or malformed AI answer.
    if findings:
        with conn:
            conn.execute('DELETE FROM experimental_findings WHERE paper_id=?', (paper_id,))
            conn.executemany('''INSERT INTO experimental_findings
                (paper_id,pdf_sha256,cache_key,kind,dataset,metric,value,baseline,setting,chunk_id,page,quote,extracted_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''', [
                (paper_id,document['pdf_sha256'],cache_key,x['kind'],x['dataset'],x['metric'],x['value'],
                 x['baseline'],x['setting'],x['chunk_id'],x['page'],x['quote'],stamp) for x in findings])
    return {'paper_id':paper_id,'status':'analyzed' if findings else 'no_valid_evidence',
            'findings':len(findings),
            'note':'结果为经过逐字证据校验的辅助提取，必须核对原文表格、评价口径和实验条件。'}


def export_experiment_index(conn, *, topic_id: str, db_path: Path, destination: Path) -> dict:
    """Export only current-topic evidence whose on-disk PDF is still unchanged.

    Includes source URL and PDF page, no invented comparability claims; all
    spreadsheet cells are escaped to prevent CSV formula injection.
    """
    init_schema(conn)
    if not isinstance(topic_id,str) or not topic_id or len(topic_id)>48:
        raise ValueError('研究主题 ID 无效。')
    papers=conn.execute('''SELECT DISTINCT e.paper_id,p.title,p.doi,p.url FROM experimental_findings e
        JOIN topic_papers t ON t.paper_id=e.paper_id JOIN papers p ON p.paper_id=e.paper_id
        WHERE t.topic_id=? ORDER BY p.title,p.paper_id''',(topic_id,)).fetchall()
    if len(papers)>5000:
        raise ValueError('实验指标导出上限为 5000 篇论文，请按主题缩小范围。')
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    def safe(value):
        text=str(value if value is not None else '')
        return ("'" + text) if text.lstrip().startswith(('=','+','-','@')) else text
    written=0
    skipped=0
    with destination.open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.writer(handle)
        writer.writerow(['论文ID','论文标题','DOI','论文链接','类型','数据集',
                         '评价指标','原文数值','对照基线','实验条件','PDF页码','原文引文','可比性说明'])
        for paper in papers:
            status=read_findings(conn,paper['paper_id'],db_path=db_path)
            if status['status']!='ready':
                skipped+=1
                continue
            for rec in status['findings']:
                writer.writerow([safe(v) for v in [paper['paper_id'],paper['title'],paper['doi'],paper['url'],
                    rec['kind'],rec['dataset'],rec['metric'],rec['value'],rec['baseline'],rec['setting'],
                    rec['page'],rec['quote'],'仅供人工复核；不同论文条件与指标需核对']])
                written+=1
    return {'rows':written,'papers':len(papers),'skipped_stale':skipped,'file':destination.name}
