#!/usr/bin/env python3
"""Independent human review progress for RealPDF30 research QA and pairs.

Adjudications resolve disagreements only after both independent reviews exist.
A model prediction can never serve as reviewer A/B or as adjudication.
"""
from __future__ import annotations
import argparse
import csv
import json
import statistics
from collections import Counter
from pathlib import Path


def rows(path):
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def normalize(x):return ' '.join(str(x or '').strip().casefold().split())


QA_ANSWERABILITY={'yes','no','true','false','1','0'}
COMPARABILITY={'yes','no','true','false','1','0','unverified','insufficient_evidence'}
CONFLICT={'confirmed_conflict','apparent_conflict_due_to_settings','no_conflict','insufficient_evidence'}


def _valid_review(row, stem):
    if stem=='qa':
        return (normalize(row.get('answerable')) in QA_ANSWERABILITY
                and normalize(row.get('rubric_score')) in ('0','1','2','3'))
    if stem=='pair':
        return (normalize(row.get('quantitatively_comparable')) in COMPARABILITY
                and normalize(row.get('contradiction_label')) in CONFLICT
                and all(normalize(row.get(key)) in ('yes','no','unverified','unknown')
                        for key in ('same_dataset','same_metric','same_setting')))
    return False


def resolved_records(root,stem,key,fields):
    a=rows(root/'tasks'/f'{stem}_review_A.csv')
    b=rows(root/'tasks'/f'{stem}_review_B.csv')
    adj=rows(root/'tasks'/f'{stem}_adjudicated.csv')
    aa={r[key]:r for r in a};bb={r[key]:r for r in b}
    if len(aa)!=len(a) or len(bb)!=len(b) or set(aa)!=set(bb):
        raise ValueError(f'{stem} human review IDs duplicate or missing')
    # Prevent incomplete CSVs with the same missing rows from being treated
    # as fully reviewed. IDs must equal the frozen evaluation manifest.
    if stem=='qa':
        expected={json.loads(line)['question_id'] for line in
                  (root/'tasks'/'single_and_cross_paper_qa.jsonl').read_text(encoding='utf-8').splitlines()
                  if line.strip()}
    else:
        expected={row['pair_id'] for row in rows(root/'tasks'/'method_and_conflict_pairs.csv')}
    if not expected or len(expected)!=len(aa) or set(aa)!=expected:
        raise ValueError(f'{stem} review IDs do not cover the frozen benchmark')
    if any(normalize(row.get('reviewer')) not in ('a','b')
           for row in a+b):
        raise ValueError(f'{stem} reviewer identity label missing')
    if any(normalize(row.get('reviewer'))!='a' for row in a) or any(
            normalize(row.get('reviewer'))!='b' for row in b):
        raise ValueError(f'{stem} A/B reviewer template mismatch')
    ad={r[key]:r for r in adj if r.get(key)}
    if len(ad)!=len([r for r in adj if r.get(key)]):raise ValueError('Duplicated adjudication IDs')
    if set(ad)-set(aa):raise ValueError('Unknown adjudication ID')
    completed=[];agreed=[];disagreed=[];resolved={}
    for key_id in sorted(aa):
        x=aa[key_id];y=bb[key_id]
        if (not all(normalize(x.get(f)) and normalize(y.get(f)) for f in fields)
                or not _valid_review(x,stem) or not _valid_review(y,stem)):
            continue
        completed.append(key_id)
        if all(normalize(x.get(f))==normalize(y.get(f)) for f in fields):
            agreed.append(key_id)
            resolved[key_id]=x
        else:
            disagreed.append(key_id)
            item=ad.get(key_id)
            if item and all(normalize(item.get(f)) for f in fields) and _valid_review(item,stem):
                resolved[key_id]=item
    unresolved=[i for i in disagreed if i not in resolved]
    info={'items':len(aa),'double_reviewed':len(completed),'agreed':len(agreed),
          'disagreement':len(disagreed),'adjudication_resolved':len(disagreed)-len(unresolved),
          'adjudication_pending':len(unresolved),
          'completed':len(completed)==len(aa) and not unresolved,
          'agreement_rate':round(len(agreed)/len(completed),4) if completed else None}
    return aa,bb,info,resolved


def audit(root,stem,key,fields):
    a,b,info,resolved=resolved_records(root,stem,key,fields)
    return a,b,info,list(resolved)


def summarize(root):
    base=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    qa_a,qa_b,qa,qa_resolved=resolved_records(root,'qa','question_id',
        ('answer_gold','answerable','rubric_score','citation_gold','judgement_reason'))
    pair_a,pair_b,pair,pair_resolved=resolved_records(root,'pair','pair_id',
        ('same_dataset','same_metric','same_setting','quantitatively_comparable',
         'contradiction_label','judgement_reason'))
    rubric=[]
    for item in qa_resolved.values():
        try:
            v=float(item['rubric_score'])
            if v in (0,1,2,3):rubric.append(v)
        except (ValueError,TypeError):pass
    comparable=sum(normalize(r['quantitatively_comparable']) in ('yes','true','1')
                   for r in pair_resolved.values())
    conflicting=sum(normalize(r['contradiction_label'])=='confirmed_conflict'
                    for r in pair_resolved.values())
    types=Counter(json.loads(line)['task_type'] for line in
                  (root/'tasks/single_and_cross_paper_qa.jsonl').read_text(encoding='utf-8').splitlines()
                  if line.strip())
    return {'benchmark_id':base['benchmark_id'],'papers':len(base['papers']),
            'task_counts':types,
            'question_review':{**qa,'human_rubric_mean':round(statistics.mean(rubric),3)
                               if rubric else None},
            'cross_paper_pair_review':{**pair,
                'confirmed_comparable_positive':comparable if pair['completed'] else None,
                'confirmed_conflict_positive':conflicting if pair['completed'] else None},
            'scientific_model_accuracy_measured':False,
            'notice':'Human review completeness is not scientific model accuracy.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,default=Path('.'));p.add_argument('--out',type=Path)
    args=p.parse_args();result=summarize(args.dataset)
    result['task_counts']=dict(result['task_counts'])
    data=json.dumps(result,ensure_ascii=False,indent=2)
    if args.out:args.out.write_text(data,encoding='utf-8')
    print(data)
if __name__=='__main__':main()
