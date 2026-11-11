"""Prepare and score genuinely independent human review of model QA answers.

A generated answer is never graded by the generating model. No unreviewed answer
is silently counted as correct. Disagreements require a third adjudication row.
"""
from __future__ import annotations
import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path

FIELDS=['question_id','prediction_sha256','reviewer','reviewer_id','reviewed_at_utc',
        'answer_quality_0_3','citation_support_yes_no','reason']

def load_pred(path):
    rows=[json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]
    qa=[x for x in rows if x.get('task_type')=='research_qa']
    if len(qa)!=88 or len({r['id'] for r in qa})!=88:
        raise ValueError('Expected 88 unique research QA model predictions')
    return sorted(qa,key=lambda x:x['id'])


def make_review(run_dir:Path):
    qa=load_pred(run_dir/'model_predictions.jsonl')
    output={}
    for reviewer in ('A','B'):
        path=run_dir/f'model_qa_review_{reviewer}.csv'
        if path.exists():raise FileExistsError(f'Existing review must not be overwritten: {path}')
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader()
            for row in qa:
                fingerprint=sha256(json.dumps(row['prediction'],sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
                writer.writerow({'question_id':row['id'],'prediction_sha256':fingerprint,
                                 'reviewer':reviewer})
        output[reviewer]=str(path)
    adjudicated=run_dir/'model_qa_adjudicated.csv'
    if not adjudicated.exists():
        with adjudicated.open('w',encoding='utf-8-sig',newline='') as f:
            csv.DictWriter(f,fieldnames=FIELDS).writeheader()
    return output


def load_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def grade(run_dir:Path,reviewers:dict):
    pred=load_pred(run_dir/'model_predictions.jsonl')
    a=load_csv(run_dir/'model_qa_review_A.csv')
    b=load_csv(run_dir/'model_qa_review_B.csv')
    aj=load_csv(run_dir/'model_qa_adjudicated.csv')
    aa={x['question_id']:x for x in a};bb={x['question_id']:x for x in b}
    adj={x['question_id']:x for x in aj if x.get('question_id')}
    expected={x['id'] for x in pred}
    if set(aa)!=expected or set(bb)!=expected or set(adj)-expected:
        raise ValueError('Invalid model QA human review IDs')
    if len(a)!=88 or len(b)!=88 or len(adj)!=len(aj):raise ValueError('Duplicate model QA human review ID')
    resolved={}
    blockers=[]
    for item in pred:
        key=item['id']
        sha=sha256(json.dumps(item['prediction'],sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
        x=aa[key];y=bb[key]
        expected_a=str(reviewers['A'].get('reviewer_id') or '')
        expected_b=str(reviewers['B'].get('reviewer_id') or '')
        def valid(r,expected_id):
            return (r.get('prediction_sha256')==sha and
                    bool(expected_id) and r.get('reviewer_id')==expected_id and
                    bool(r.get('reviewed_at_utc')) and
                    r.get('answer_quality_0_3') in ('0','1','2','3') and
                    r.get('citation_support_yes_no') in ('yes','no') and
                    bool(str(r.get('reason') or '').strip()))
        if not valid(x,expected_a) or not valid(y,expected_b):
            blockers.append(key+':independent_review_incomplete')
            continue
        score_fields=('answer_quality_0_3','citation_support_yes_no')
        if all(x[field]==y[field] for field in score_fields):
            resolved[key]=x
        elif key in adj and valid(adj[key],str(reviewers.get('adjudicator') or '')):
            resolved[key]=adj[key]
        else:blockers.append(key+':third_party_adjudication_missing')
    if blockers:
        return {'state':'pending_human_model_answer_review','resolved':len(resolved),
                'expected':88,'blockers':blockers[:25],
                'semantic_qa_quality':None,'citation_support_rate':None}
    return {'state':'human_review_completed','resolved':len(resolved),
            'expected':88,
            'semantic_qa_quality_mean_0_3':round(sum(int(x['answer_quality_0_3']) for x in resolved.values())/88,4),
            'semantic_qa_correct_rate_score_ge_2':round(sum(int(x['answer_quality_0_3'])>=2 for x in resolved.values())/88,4),
            'citation_support_rate':round(sum(x['citation_support_yes_no']=='yes' for x in resolved.values())/88,4)}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--mode',choices=('prepare','score'),default='prepare')
    p.add_argument('--attestation',type=Path,help='human_review_attestation.json for score')
    args=p.parse_args()
    if args.mode=='prepare':result=make_review(args.run_dir)
    else:
        if not args.attestation:raise ValueError('--attestation required')
        prov=json.loads(args.attestation.read_text(encoding='utf-8'))
        r=dict(prov['reviewers'])
        r['adjudicator']=str((prov.get('adjudicator') or {}).get('reviewer_id') or '')
        result=grade(args.run_dir,r)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
