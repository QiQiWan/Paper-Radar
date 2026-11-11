"""Verify 30 immutable PDFs and generate physical PDF evidence geometry review.

Do not report PDF alignment coverage as scientific extraction accuracy.
One source PDF is hashed before any of its pages are read. CPU-only, offline.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
from datetime import datetime,timezone
import sys

from paper_radar.pdf_geometry import _locate_page
import fitz


def read_csv(path:Path):
    with path.open(encoding='utf-8-sig',newline='') as stream:return list(csv.DictReader(stream))


def run(dataset:Path, pdf_dir:Path, output:Path, *, max_candidates:int=0, baseline:Path|None=None)->dict:
    dataset=dataset.resolve();pdf_dir=pdf_dir.resolve();output=output.resolve()
    manifest=json.loads((dataset/'manifest.json').read_text(encoding='utf-8'))
    papers={x['paper_id']:x for x in manifest['papers']}
    if len(papers)!=30:raise ValueError('Expected 30 fixed version PDFs')
    candidates=read_csv(dataset/'annotations/candidate_evidence.csv')
    if max_candidates:candidates=candidates[:max_candidates]
    assert len({x['candidate_id'] for x in candidates})==len(candidates)
    # Check the full corpus, even when a subset of candidate rows is requested.
    for item in papers.values():
        path=(pdf_dir/(item['paper_id']+item['version']+'.pdf')).resolve()
        if not path.is_relative_to(pdf_dir) or not path.is_file():
            raise FileNotFoundError(f'Missing frozen original PDF: {path.name}')
        h=hashlib.sha256(path.read_bytes()).hexdigest()
        if h!=item['sha256']:
            raise ValueError(f'SHA-256 mismatch in original {path.name}')
    old={x['candidate_id']:x for x in read_csv(baseline)} if baseline else {}
    rows=[]
    by_paper=defaultdict(list)
    for row in candidates:by_paper[row['paper_id']].append(row)
    processed=0
    # Corpus verification was completed above; share one PDF handle per paper
    # and cache pages, avoiding repeated whole-PDF hashing and parsing.
    for pid,paper_rows in by_paper.items():
        item=papers[pid]
        path=pdf_dir/(item['paper_id']+item['version']+'.pdf')
        with fitz.open(path) as document:
            for row in paper_rows:
                result=_locate_page(document[int(row['page'])-1],row['source_quote'],
                    metric=row.get('metric_candidate') or '',value=row.get('value_candidate') or '')
                records={k:result.get(k) for k in ('text_matched','matched','match_strategy',
                         'geometry_status','total_line_segments','boxes','table_context','formula_context',
                         'context','scientific_relation_verified')}
                rows.append({'candidate_id':row['candidate_id'],'paper_id':row['paper_id'],
                     'page':int(row['page']),'pdf_sha256':item['sha256'],
                     'pypdf_contiguous_before':old.get(row['candidate_id'],{}).get('quote_contiguous_after_pypdf','unknown'),
                     **records,'human_review':'pending','review_required':True})
                processed+=1
                if processed%20==0:print(f'[{processed}/{len(candidates)}] geometry',flush=True)
    output.mkdir(parents=True,exist_ok=True)
    with (output/'geometry_candidates.jsonl').open('w',encoding='utf-8') as stream:
        for x in rows:stream.write(json.dumps(x,ensure_ascii=False)+'\n')
    simple=[]
    for x in rows:
        tc=x.get('table_context')
        simple.append({k:x[k] for k in ('candidate_id','paper_id','page','pdf_sha256',
                                        'pypdf_contiguous_before','text_matched','matched','match_strategy',
                                        'geometry_status','total_line_segments')}
                      | {'rule_based_table_cells': len(tc['intersecting_cells']) if tc else 0,
                         'formula_context':bool(x.get('formula_context')),
                         'review_status':'pending_human_review'})
    with (output/'geometry_review_queue.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(simple[0]));writer.writeheader();writer.writerows(simple)
    # Human worklist: source text is an unverified machine candidate, never a
    # pre-filled reviewer judgment. Prioritize unresolved geometry and math.
    original_by_id={r['candidate_id']:r for r in candidates}
    worklist=[]
    for x in rows:
        raw=original_by_id[x['candidate_id']]
        priority='P0' if not x['matched'] else 'P1' if x.get('formula_context') else 'P2'
        worklist.append({'priority':priority,'candidate_id':x['candidate_id'],
            'paper_id':x['paper_id'],'pdf_version':papers[x['paper_id']]['version'],
            'physical_pdf_page':x['page'],
            'official_pdf_url':papers[x['paper_id']].get('pdf_url') or
                f'https://arxiv.org/pdf/{x["paper_id"]}{papers[x["paper_id"]]["version"]}',
            'sha256':x['pdf_sha256'],'text_recovered':x['text_matched'],
            'geometry_verified':x['matched'],'formula_context_candidate':bool(x.get('formula_context')),
            'table_structure_verified':False,'scientific_relation_verified':False,
            'metric_candidate':raw.get('metric_candidate',''),
            'value_candidate':raw.get('value_candidate',''),
            'original_quote_candidate':str(raw.get('source_quote') or '')[:700],
            'pdf_review_instruction':'Check actual page and table row/column or equation before final judgment',
            'human_judgment':'','human_corrected_relation':'','human_reviewer_id':''})
    worklist.sort(key=lambda r:(r['priority'],r['paper_id'],r['candidate_id']))
    with (output/'HUMAN_REVIEW_WORKLIST.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(worklist[0]));writer.writeheader();writer.writerows(worklist)
    previous_misses=[x for x in rows if x['pypdf_contiguous_before']=='False']
    summary={'schema':'realpdf30-geometry-v1452','generated_at':datetime.now(timezone.utc).isoformat(),
        'papers_verified_sha256':len(papers),'candidate_count':len(rows),
        'layout_text_matches':sum(x['text_matched'] for x in rows),
        'geometry_verified':sum(x['matched'] for x in rows),
        'geometry_unresolved':sum(x['text_matched'] and not x['matched'] for x in rows),
        'prior_39_misses':len(previous_misses),
        'prior_misses_recovered_as_text':sum(x['text_matched'] for x in previous_misses),
        'prior_misses_with_geometry':sum(x['matched'] for x in previous_misses),
        'rule_based_table_candidates':sum(bool(x['table_context']) for x in rows),
        'formula_context_candidates':sum(bool(x['formula_context']) for x in rows),
        'match_strategy':dict(Counter(x['match_strategy'] for x in rows)),
        'human_gold_approved':0,
        'warning':'All structural and quote matches are automatic; no scientific numeric/table relation is human verified.'}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'summary.md').write_text('# 真实 PDF 引文定位与结构化候选核验\n\n'+
        '\n'.join(f'- {k}: {v}' for k,v in summary.items())+'\n',encoding='utf-8')
    return summary


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--dataset',type=Path,default=Path('benchmarks/real_pdf30'))
    parser.add_argument('--pdf-dir',type=Path,default=Path('benchmarks/real_pdf30/pdfs'))
    parser.add_argument('--output',type=Path,default=Path('realpdf30_geometry_v1452'))
    parser.add_argument('--baseline',type=Path,default=None)
    parser.add_argument('--max-candidates',type=int,default=0)
    args=parser.parse_args()
    print(json.dumps(run(args.dataset,args.pdf_dir,args.output,
        max_candidates=args.max_candidates,baseline=args.baseline),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
