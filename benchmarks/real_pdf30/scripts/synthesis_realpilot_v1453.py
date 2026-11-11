#!/usr/bin/env python3
"""Transparent *non-human-gold* evaluation of SynthesisAgent evidence routing.

Five PDF-table-derived benchmark claims are injected as a TEST-ONLY in-memory
fixture. They are NOT inserted into a user's confirmed knowledge base.
No LLM API is invoked; score is retrieval/traceability only.
"""
from __future__ import annotations
import argparse, hashlib,json,sys
from pathlib import Path
import fitz


def data_fixture(dataset, pdf_dir):
    import csv
    index={r['paper_id']:r for r in csv.DictReader((dataset/'PAPER_INDEX_30.csv').open(encoding='utf-8-sig'))}
    cases=[
      ('C-FNO-T1','2010.08895',8,'Table 1: Benchmarks on Navier Stokes',
       'FNO FNO-3D and FNO-2D Navier-Stokes Table 1 at 64x64: viscosity 1e-3 T=50 N=1000 gives FNO-3D 0.0086 and FNO-2D 0.0128. At viscosity 1e-4 T=30 N=1000 errors 0.1918 and 0.1559; viscosity 1e-4 T=30 N=10000 gives 0.0820 and 0.0834; viscosity 1e-5 T=20 N=1000 gives 0.1893 and 0.1556.'),
      ('C-UFNO-T2A','2109.03697',19,'Table 2: Accuracy of U-FNO and CNN',
       'U-FNO and CNN Table 2(a): gas saturation front prediction uses CO2 threshold value 0.01. Relative front error (%) on test set: U-FNO 3.4%, CNN 9.2%.'),
      ('C-UFNO-T2B','2109.03697',19,'pressure buildup “front” is deﬁned',
       'U-FNO and CNN Table 2(b): pressure buildup front prediction threshold 0.5 bar. Relative front error (%) on test set: U-FNO 12.0%, CNN 21.2%.'),
      ('C-PINO-T2','2111.03794',13,'DeepONet with data [36]',
       'PINO Darcy Flow Table 2 solution error: DeepONet with data 6.97 ± 0.09%; PINO with data 1.22 ± 0.03%; PINO without data 1.50 ± 0.03%. Method experiment uses physics-informed neural operator.'),
      ('C-PINO-T3','2111.03794',13,'# data samples',
       'PINO Kolmogorov flow Table 3, 2200 PDE instances, relative L2 solution error with 0, 800, 2200 data samples is 6.22 ± 0.11%, 6.01 ± 0.12%, 5.04 ± 0.11%, respectively.'),
      ('C-UFNO-T3','2109.03697',19,'Table 3: Gas saturation',
       'U-FNO and CNN Table 3 gas saturation MPE (%) on heterogeneous formations: U-FNO 2.7, CNN 4.7; on homogeneous formations: U-FNO 1.5, CNN 2.0.'),
      ('C-PINO-BURGERS','2111.03794',13,'On Burgers (18)',
       'PINO Burgers solution relative error 0.38% compared with PI-DeepONet 1.38%, reported in discussion on PDF page 13.'),
      ('C-PINO-TLONG','2111.03794',13,'Long temporal transient flow',
       'PINO long transient flow error 2.87% without instance-wise fine-tuning, FNO baseline 3.04%, and with anchor/PDE loss fine-tuning PINO error 1.84%.'),
    ]
    claims=[]
    for ident,pid,pno,needle,body in cases:
        row=index[pid];file=pdf_dir/row['local_filename']
        sha=hashlib.sha256(file.read_bytes()).hexdigest()
        assert sha==row['pdf_sha256'],f'PDF SHA differs from frozen dataset: {pid}'
        with fitz.open(file) as pdf:
            txt=pdf[pno-1].get_text('text',sort=True)
        assert needle in txt,repr((pid,needle))
        start=txt.index(needle)
        quote=txt[start:start+min(230,len(txt)-start)].strip()
        claims.append({'id':ident,'revision':1,'body':body,'kind':'experimental_result',
                       # This status is ONLY used in an isolated sandbox input.
                       # The evaluation JSON is labelled test fixture, not human gold.
                       'effective_status':'confirmed',
                       'anchors':[{'id':'ANCHOR-'+ident,'paper_id':pid,'title':row['title'],
                                   'doi':'','page':pno,'quote':quote,'pdf_sha256':sha,
                                   'status':'ready','experiment':{'review_decision':'needs_review'}}]})
    return {'claims':claims,'truncated':False}


EXPECTED={
'Q01':['C-FNO-T1'],
'Q02':['C-FNO-T1'],
'Q03':['C-UFNO-T2A'],
'Q04':['C-UFNO-T2B'],
'Q05':['C-PINO-T2'],
'Q06':['C-PINO-T3'],
'Q07':['C-FNO-T1','C-UFNO-T2A'],
'Q08':['C-UFNO-T2B','C-PINO-T2'],
'Q09':['C-FNO-T1'],
'Q10':['C-FNO-T1'],
'Q11':['C-FNO-T1'],
'Q12':['C-UFNO-T3'],
'Q13':['C-PINO-BURGERS'],
'Q14':['C-PINO-TLONG'],
}


def measure(dataset,pdf_dir):
    from paper_radar.research_synthesis_agent import build_report
    fixture=data_fixture(dataset,pdf_dir)
    questions=[json.loads(l) for l in (dataset/'tasks/pilot_visual_checked_qa.jsonl').read_text(encoding='utf8').splitlines() if l.strip()]
    questions += [json.loads(l) for l in (dataset/'tasks/pilot_v1453_additions.jsonl').read_text(encoding='utf8').splitlines() if l.strip()]
    details=[]
    for qa in questions:
        ident=qa['id'];report=build_report(fixture,qa['question'],use_model=False)
        retrieved=[f['claim_id'] for f in report['findings']]
        expected=EXPECTED[ident]
        top2=retrieved[:2]
        details.append({'question_id':ident,'type':qa['task_type'],
                       'gold_paper_page_provenance':'assistant_single_pass_visual',
                       'expected_claim_ids':expected,'first_2_claim_ids':top2,
                       'returned_ids':retrieved,
                       'hit_at_2':bool(set(expected)&set(top2)),
                       'all_required_at_2':set(expected)<=set(top2),
                       'precision_at_2':sum(c in expected for c in top2)/2,
                       'citation_ids_valid':all(f['evidence'] and all(x['pdf_sha256'] and x['page'] for x in f['evidence']) for f in report['findings']),
                       'methods_detected':[r['method'] for r in report['method_profiles']],
                       'model_status':report['model']['status'],
                       'proposed_numeric_contradictions':len([d for d in report['conflict_candidates'] if d['type']=='result_variation'])})
    out={'protocol':'realpdf30-v1453-synthesis-evidence-routing',
         'scientific_model_accuracy':None,'human_double_reviewed_gold':0,
         'notes':['Test-only in-memory confirmed fixture constructed from three visually inspected original PDF pages',
                  'No real human A/B provenance is claimed','No model API used; no generated-answer semantic correctness was scored'],
         'sample_size':len(details),
         'hit_at_2':sum(x['hit_at_2'] for x in details)/len(details),
         'all_required_at_2':sum(x['all_required_at_2'] for x in details)/len(details),
         'mean_precision_at_2':sum(x['precision_at_2'] for x in details)/len(details),
         'citation_validity_in_report':all(x['citation_ids_valid'] for x in details),
         'model_invocation_count':0,'details':details}
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--pdf-dir',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();r=measure(a.dataset,a.pdf_dir)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in r.items() if k not in ('details','notes')},ensure_ascii=False,indent=2))
    for x in r['details']:
        print(x['question_id'],'expected',x['expected_claim_ids'],'top2',x['first_2_claim_ids'],'methods',x['methods_detected'])
