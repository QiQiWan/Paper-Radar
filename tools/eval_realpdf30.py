"""Run Paper-Radar's real-PDF-30 benchmark in an isolated temporary workspace.

Usage:
  python -m tools.eval_realpdf30 --dataset benchmarks/real_pdf30 \\
      --output realpdf30_run --timeout 90

This is a *retrieval and indexing diagnostic*. Candidate anchors are machine
proposals; their coverage is not scientific precision/recall or QA accuracy.
No model API is called. Every PDF is SHA-256 checked before it can be indexed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone

from paper_radar.models import Paper
from paper_radar.core import connect, save_papers
from paper_radar.fulltext import PARSER_VERSION, MAX_PAGES, search_evidence


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def normalized_text(text: str) -> str:
    """Strict order-preserving whitespace/Unicode comparison of PDF text."""
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text)).casefold())


def load_dataset(dataset: Path):
    manifest = json.loads((dataset / 'manifest.json').read_text(encoding='utf-8'))
    papers = manifest.get('papers')
    if not isinstance(papers, list) or len(papers) != 30:
        raise ValueError('Expected exactly 30 frozen-version benchmark papers')
    ids = [item['paper_id'] for item in papers]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicated benchmark paper IDs')
    with (dataset / 'annotations' / 'candidate_evidence.csv').open(encoding='utf-8-sig', newline='') as src:
        candidates = list(csv.DictReader(src))
    if len({item['candidate_id'] for item in candidates}) != len(candidates):
        raise ValueError('Duplicated candidate evidence IDs')
    return manifest, candidates


def prepare_scratch(dataset: Path, scratch: Path, papers: list[dict], pdf_source: Path) -> Path:
    """Never open or modify the user's actual Paper-Radar research database."""
    pdf_source = pdf_source.resolve()
    pdf_dir = scratch / 'downloads'
    db_file = scratch / 'data' / 'papers.sqlite3'
    pdf_dir.mkdir(parents=True, exist_ok=True)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_file)
    try:
        for item in papers:
            name = item['paper_id'] + item['version'] + '.pdf'
            source = (pdf_source / name).resolve()
            if not source.is_relative_to(pdf_source) or not source.is_file():
                raise FileNotFoundError(f'Missing frozen PDF: {name}. Run the corpus downloader first.')
            actual = hash_file(source)
            if actual != item['sha256']:
                raise ValueError(f'SHA-256 mismatch: {name} expected={item["sha256"]} actual={actual}')
            target = pdf_dir / name
            if target.exists() and hash_file(target) != actual:
                target.unlink()
            if not target.exists():
                # Independent bytes: a crash or future code change in the scratch
                # workspace can never mutate the user's reference PDF via hardlinks.
                shutil.copyfile(source, target)
                if hash_file(target) != actual:
                    target.unlink(missing_ok=True)
                    raise ValueError(f'Scratch PDF copy integrity failure: {name}')
            p = Paper(paper_id=item['paper_id'], title=item['title'], abstract='',
                      authors='', published='2024-01-01', updated='2024-01-01',
                      category=item['category'], url=item['arxiv_url'],
                      pdf_url=item['pdf_url'], source='arxiv', local_file=name,
                      download_status='downloaded')
            save_papers(conn, [p], topic_id='default')
            conn.execute('UPDATE papers SET local_file=?, download_status=? WHERE paper_id=?',
                         (name, 'downloaded', p.paper_id))
        conn.commit()
    finally:
        conn.close()
    return db_file


def index_child(db: Path, paper_id: str) -> int:
    from paper_radar.fulltext import index_paper
    from paper_radar.core import connect
    conn = connect(db)
    try:
        result = index_paper(conn, paper_id, db_path=db)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        conn.close()


def run_index(db_file: Path, paper_id: str, timeout: int) -> dict:
    before = time.monotonic()
    command = [sys.executable, '-m', 'tools.eval_realpdf30', '--index-child',
               '--db', str(db_file), '--paper-id', paper_id]
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=timeout, check=False)
        if done.returncode != 0:
            return {'paper_id': paper_id, 'state': 'failed', 'exit_code': done.returncode,
                    'error': done.stderr[-650:], 'seconds': round(time.monotonic()-before, 2)}
        result = json.loads(done.stdout.splitlines()[-1])
        return {'paper_id': paper_id, 'state': result['status'],
                'indexed_pages': result['pages'], 'total_pages': result['total_pages'],
                'chunks': result['chunks'], 'seconds': round(time.monotonic()-before, 2)}
    except (subprocess.TimeoutExpired, IndexError, ValueError, KeyError) as exc:
        return {'paper_id': paper_id,
                'state': 'timeout' if isinstance(exc, subprocess.TimeoutExpired) else 'failed',
                'seconds': round(time.monotonic()-before, 2),
                'error': str(exc)[:250]}


def _candidate_query(row: dict) -> str:
    metric = str(row.get('metric_candidate') or '').strip()
    if metric:
        return metric[:130]
    quote = str(row.get('source_quote') or '')
    words = [word for word in re.findall(r'[A-Za-z][a-z]{3,}', quote)
             if word.casefold() not in {'that','with','from','this','which','were','have','into',
                                         'their','there','more','table','figure','these','some',
                                         'such','using','would','also','results','study'}]
    return ' '.join(words[:3]) or 'experiment'


def assess_evidence(conn: sqlite3.Connection, candidates: list[dict], eligible_ids: set[str]) -> dict:
    result=[]
    by_paper=defaultdict(list)
    for row in candidates:
        if row['paper_id'] in eligible_ids:
            by_paper[row['paper_id']].append(row)
    for pid, items in by_paper.items():
        pages=defaultdict(list)
        rows=conn.execute('SELECT page, content FROM fulltext_chunks WHERE paper_id=? ORDER BY page,id',
                          (pid,)).fetchall()
        for row in rows:
            pages[int(row['page'])].append(row['content'])
        known_pages=set(pages)
        for item in items:
            page=int(item['page'])
            page_present=page in known_pages
            # Strict substring match is deliberately not treated as a scientific relation check.
            source=normalized_text(item.get('source_quote', ''))
            page_body=normalized_text(''.join(pages.get(page, [])))
            text_recovered=bool(page_present and len(source)>10 and source in page_body)
            query=_candidate_query(item)
            hits = search_evidence(conn, query, paper_id=pid, limit=20)
            hit_pages=[int(x['page']) for x in hits]
            result.append({'candidate_id': item['candidate_id'], 'paper_id':pid,
                           'source_page':page, 'query':query,
                           'page_indexed':page_present,
                           'quote_contiguous_after_pypdf':text_recovered,
                           'anchor_page_in_search_top20':page in hit_pages,
                           'returned_pages_top20':hit_pages})
    return {'total':len(result),
            'page_indexed':sum(x['page_indexed'] for x in result),
            'quote_contiguous_after_pypdf':sum(x['quote_contiguous_after_pypdf'] for x in result),
            'anchor_page_in_search_top20':sum(x['anchor_page_in_search_top20'] for x in result),
            'items':result}


def manual_coverage(dataset: Path):
    # Standard project scorer leaves scientific performance metrics unmeasured
    # until reviewer A/B and adjudication have completed.
    from runpy import run_path
    module=run_path(str(dataset / 'scripts' / 'validate_and_score.py'))
    return module['validate'](dataset)


def run(dataset: Path, output: Path, *, timeout: int=90, max_papers: int=30, pdf_source: Path|None=None) -> dict:
    dataset=dataset.resolve()
    output=output.resolve()
    if output.is_relative_to(dataset) or dataset.is_relative_to(output):
        raise ValueError('Evaluation output and the immutable dataset must be separate directories')
    output.mkdir(parents=True, exist_ok=True)
    manifest, candidates = load_dataset(dataset)
    papers=manifest['papers'][:max_papers]
    scratch = output / 'scratch'
    db_file = prepare_scratch(dataset,scratch,papers, pdf_source or dataset/'pdfs')
    indexed=[]
    for item in papers:
        row = run_index(db_file,item['paper_id'],timeout)
        indexed.append(row)
        print(f"[{len(indexed)}/{len(papers)}] {item['paper_id']}: {row['state']} "
              f"({row.get('indexed_pages', '-')}/{item['pages']} pages, {row['seconds']} s)",flush=True)
    conn = connect(db_file)
    try:
        observations=assess_evidence(conn, candidates, {p['paper_id'] for p in papers})
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise RuntimeError('Scratch SQLite integrity check failed')
    finally:
        conn.close()
    status=Counter(x['state'] for x in indexed)
    total_expected=sum(int(p['pages']) for p in papers)
    actual_pages=sum(int(x.get('indexed_pages') or 0) for x in indexed)
    manual=manual_coverage(dataset)
    from runpy import run_path
    four_task=run_path(str(dataset/'scripts'/'score_four_tasks.py'))['summarize'](dataset)
    report={'benchmark_id':manifest['benchmark_id'],
            'run_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'application_parser_version':PARSER_VERSION,
            'pdf_limit_pages':MAX_PAGES,'papers_requested':len(papers),
            'pdf_pages_expected':total_expected,'pdf_pages_indexed':actual_pages,
            'index_status':dict(status),'index_results':indexed,
            'candidate_proxy_coverage':observations,
            'human_gold':manual,
            'review_task_readiness':{
                'qa':four_task['question_review'],
                'pair':four_task['cross_paper_pair_review']},
            'scientific_accuracy_measured':bool(manual['model_f1'] is not None),
            'interpretation':(
                'Candidate page coverage, literal quote continuity and query-page hits are '
                'automated infrastructure diagnostics against UNREVIEWED candidates. '
                'They are not experimental extraction accuracy, scientific conflict accuracy, '
                'citation faithfulness or question-answering correctness.'),
            'isolated_scratch_db':str(db_file)}
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    diagnostic_rows=observations['items']
    if diagnostic_rows:
        with (output/'candidate_retrieval_diagnostics.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(diagnostic_rows[0]))
            writer.writeheader()
            for row in diagnostic_rows:
                writer.writerow({**row,'returned_pages_top20':';'.join(map(str,row['returned_pages_top20']))})
    lines=[f'# Paper-Radar RealPDF30 | v1.45.1', '',
           f'- 论文：{len(papers)}；PDF 原始页数：{total_expected}；已索引页数：{actual_pages}',
           f'- 状态：{dict(status)}',
           f'- 候选页已索引：{observations["page_indexed"]}/{observations["total"]}',
           f'- 候选引文经 pypdf 保持连续：{observations["quote_contiguous_after_pypdf"]}/{observations["total"]}',
           f'- 关键词检索 Top20 含候选物理页：{observations["anchor_page_in_search_top20"]}/{observations["total"]}',
           '', '以上为未人工复核候选的系统诊断，不能解释为论文实验提取准确率。',
           f'正式人工金标准：{manual["accepted_human_gold"]} 条；模型 F1：{manual["model_f1"]}',
           f'人工问答双人审核：{four_task["question_review"]["double_reviewed"]}/{four_task["question_review"]["items"]}；'
           f'跨论文配对双人审核：{four_task["cross_paper_pair_review"]["double_reviewed"]}/{four_task["cross_paper_pair_review"]["items"]}',
           '', '## 失败与不完整论文','']
    lines += [f'- {r["paper_id"]}: {r["state"]}; {r.get("error", r.get("indexed_pages",""))}'
              for r in indexed if r['state'] not in ('indexed','cached')]
    if len(lines) and lines[-1]=='':lines.append('无。')
    (output/'result.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return report


def main(argv: list[str]|None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=Path('benchmarks/real_pdf30'))
    parser.add_argument('--output', type=Path, default=Path('realpdf30_eval_v1451'))
    parser.add_argument('--pdf-dir', type=Path, help='Private directory containing 30 version-pinned original PDFs')
    parser.add_argument('--timeout',type=int,default=90)
    parser.add_argument('--max-papers',type=int,default=30)
    parser.add_argument('--index-child',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--db',type=Path,help=argparse.SUPPRESS)
    parser.add_argument('--paper-id',type=str,help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.index_child:
        if not args.db or not args.paper_id:
            parser.error('index-child needs --db and --paper-id')
        return index_child(args.db,args.paper_id)
    if not 1<=args.max_papers<=30 or not 5<=args.timeout<=300:
        parser.error('--max-papers must be 1..30; --timeout must be 5..300 seconds')
    try:
        report=run(args.dataset,args.output,timeout=args.timeout,max_papers=args.max_papers,pdf_source=args.pdf_dir)
    except (ValueError,FileNotFoundError,RuntimeError) as exc:
        print(f'EVALUATION ERROR: {exc}',file=sys.stderr)
        return 2
    print(json.dumps({'status':'completed','papers':report['papers_requested'],
                      'index':report['index_status'],
                      'candidate_page_coverage':report['candidate_proxy_coverage']['page_indexed'],
                      'human_gold':report['human_gold']['accepted_human_gold']},ensure_ascii=False))
    return int(any(x['state'] not in ('indexed','cached') for x in report['index_results']))

if __name__=='__main__':
    raise SystemExit(main())
