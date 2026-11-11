"""Prioritize HUMAN REVIEW of machine-proposed RealPDF30 evidence.

This heuristic triage never approves an experimental result. In particular,
nearby numbers may be citations, sample counts, table indexes or equation tags.
"""
from __future__ import annotations
import argparse
import csv
import json
import re
from collections import Counter,defaultdict
from pathlib import Path


def read_rows(path:Path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))


def assessment(row:dict)->dict:
    quote=str(row.get('source_quote') or '')
    metric=str(row.get('metric_candidate') or '').strip()
    value=str(row.get('value_candidate') or '').strip()
    lower=quote.casefold()
    score=0
    flags=[]
    if len(quote)<55:
        flags.append('short_source_excerpt')
        score-=2
    if metric:
        score+=2
    else:
        flags.append('metric_missing')
    if value:
        score+=1
        if not re.search(r'(?<!\w)'+re.escape(value)+r'(?!\w)',quote):
            flags.append('value_not_exact_in_quote')
            score-=3
    else:
        flags.append('numeric_value_missing')
    if metric and value and metric.casefold() in lower:
        starts=[m.start() for m in re.finditer(re.escape(metric.casefold()),lower)]
        values=[m.start() for m in re.finditer(re.escape(value.casefold()),lower)]
        if starts and values:
            distance=min(abs(a-b) for a in starts for b in values)
            if distance<=60:
                score+=2
            elif distance>160:
                score-=2
                flags.append('metric_value_far_apart')
        else:
            flags.append('metric_or_value_not_located')
    elif metric:
        flags.append('metric_not_located')
    if re.search(r'\b(?:table|tab\.|benchmark|validation|test\s+set|ablation|results?)\b',lower):
        score+=1
    if value and re.search(r'\[\s*'+re.escape(value)+r'\s*\]',quote):
        flags.append('number_matches_bibliographic_reference')
        score-=5
    if value and re.search(r'\b(?:section|chapter|fig(?:ure)?|table|equation|appendix)\s*'+re.escape(value)+r'\b',lower):
        flags.append('number_may_be_section_or_table_index')
        score-=2
    if value and re.fullmatch(r'20\d{2}|19\d{2}',value):
        flags.append('number_may_be_year')
        score-=3
    if re.search(r'\b(?:references|bibliography)\b',lower):
        flags.append('reference_section_context')
        score-=2
    if not row.get('method_candidate'):
        flags.append('method_missing')
    if not row.get('dataset_candidate'):
        flags.append('dataset_missing')
    if not row.get('baseline_candidate'):
        flags.append('baseline_missing')
    priority='high' if score>=5 and 'number_matches_bibliographic_reference' not in flags else 'normal' if score>=2 else 'low'
    return {'review_priority':priority,'review_score':score,'risk_flags':';'.join(flags),
            'scientific_relation_verified':'no', 'automatic_gold_eligible':'no'}


def triage(dataset:Path,output:Path)->dict:
    papers={p['paper_id']:p for p in json.loads((dataset/'manifest.json').read_text(encoding='utf-8'))['papers']}
    rows=read_rows(dataset/'annotations'/'candidate_evidence.csv')
    scored=[]
    for row in rows:
        if row['paper_id'] not in papers:raise ValueError('Unknown paper in candidate table')
        m=assessment(row)
        scored.append({**row,**m,'pdf_url':papers[row['paper_id']]['pdf_url'],
                       'paper_title':papers[row['paper_id']]['title']})
    scored.sort(key=lambda r:({'high':0,'normal':1,'low':2}[r['review_priority']],-int(r['review_score']),r['paper_id']))
    output.mkdir(parents=True,exist_ok=True)
    path=output/'candidate_triage.csv'
    with path.open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=list(scored[0]),extrasaction='ignore')
        writer.writeheader();writer.writerows(scored)
    # Review-first list reserves one candidate from every paper, followed by
    # remaining top 60. This is a scheduling aid, not gold data.
    first=[];seen=set()
    for row in scored:
        pid=row['paper_id']
        if pid not in seen:
            first.append(row);seen.add(pid)
    first_ids={r['candidate_id'] for r in first}
    first.extend(row for row in scored if row['candidate_id'] not in first_ids)
    with (output/'review_queue.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['review_order',*list(scored[0])])
        writer.writeheader()
        for ix,row in enumerate(first,1):
            writer.writerow({'review_order':ix,**row})
    counter=Counter(row['review_priority'] for row in scored)
    risk=Counter(flag for row in scored for flag in row['risk_flags'].split(';') if flag)
    summary={'total':len(scored),'represented_papers':len(seen),
             'priority_levels':dict(counter), 'common_risk_flags':dict(risk.most_common(20)),
             'interpretation':'Rule-based reviewer ordering only. No numeric claim, scientific relation, or model prediction is verified.'}
    (output/'triage_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--dataset',type=Path,default=Path('benchmarks/real_pdf30'))
    ap.add_argument('--output',type=Path,default=Path('realpdf30_triage_v1451'))
    args=ap.parse_args()
    print(json.dumps(triage(args.dataset,args.output),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
