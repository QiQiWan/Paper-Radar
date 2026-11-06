# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Offline *synthetic* comparison-protocol regression, never a model F1 claim."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from paper_radar.comparison_policy import pair_assessment

GOLD=ROOT/'benchmarks/synthetic_comparability_v1/gold_pairs.json'


def evaluate(file:Path=GOLD) -> dict:
    data=json.loads(file.read_text(encoding='utf-8'))
    base=data['base']
    conditions=base['conditions']
    first={'paper_id':'p1','kind':base['kind'],'dataset':base['dataset'],
        'metric':base['metric'],'setting':base['setting'],'baseline':base['baseline'],
        'value':base['value_a'],'evidence_id':'a'*64,
        'review_decision':'approved','quote_verified_against_pdf':True,
        'review_conditions':conditions}
    tp=tn=fp=fn=0
    cases=[]
    for test in data['cases']:
        second={**first,'paper_id':'p2','evidence_id':'b'*64,'value':base['value_b'],
                'review_conditions':dict(conditions)}
        for key,value in test['delta'].items():
            if key in conditions:second['review_conditions'][key]=value
            else:second[key]=value
        predicted=pair_assessment(first,second)
        truth=bool(test['eligible']);guess=predicted['eligible']
        if truth and guess:tp+=1
        elif truth:fn+=1
        elif guess:fp+=1
        else:tn+=1
        cases.append({'id':test['id'],'gold_eligible':truth,
            'predicted_eligible':guess,'correct':truth==guess,
            'blockers':predicted['blockers']})
    return {'benchmark':data['id'],'data_type':'synthetic_hand_authored',
            'evaluation_target':'deterministic protocol consistency; not PDF extraction, model accuracy or scientific validity',
            'n':len(cases),'true_positive':tp,'true_negative':tn,'false_positive':fp,'false_negative':fn,
            'exact_case_accuracy':round((tp+tn)/len(cases),4) if cases else None,
            'false_acceptance_rate':round(fp/(fp+tn),4) if fp+tn else None,
            'cases':cases,'limitations':[
                'Gold labels were written to exercise the rule system and are not independent human expert judgments.',
                'This regression does not measure real-paper precision, recall, F1, or statistically valid comparability.',
                'An eligible result still requires deliberate user confirmation of the pair.']}


def cli():
    parser=argparse.ArgumentParser()
    parser.add_argument('--gold',type=Path,default=GOLD)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    report=evaluate(args.gold)
    text=json.dumps(report,ensure_ascii=False,indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(text+'\n',encoding='utf-8')
    print(text)
    return 0 if all(case['correct'] for case in report['cases']) else 1

if __name__=='__main__':raise SystemExit(cli())
