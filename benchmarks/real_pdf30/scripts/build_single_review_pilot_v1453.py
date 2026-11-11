#!/usr/bin/env python3
"""Source-page-audited, single-assistant-pass demonstration; never human gold.

Twelve deliberately selected candidate records include hard negatives. This
is a diagnostic test, not a random sample and not scientific population F1.
"""
from pathlib import Path
import argparse,csv,json,hashlib
import fitz

# Value, interpretation, status, reason: based on PDF page reading in v1.45.3.
CASES={
 'P230808468-1':('true','Allen-Cahn relative L2 error = 5.37e-5','Table 1, Allen-Cahn row; the exponent is part of the result'),
 'P230808468-2':('true','Relative L2 error = 5.84e-4 for the full algorithm in the ablation discussion','Page text explicitly links metric and value'),
 'P230808468-3':('true','Fine-tuned PINN relative L2 error = 8.04e-5','Page text explicitly links metric and value'),
 'P211103794-1':('true','PINO example reports relative error 14.52%','Page text states the method and error; context must be retained'),
 'P231007984-1':('true','AUC-ROC 74.43% = prior best, not the new model result (76.60%)','Value is correctly associated with AUC-ROC but refers to the baseline'),
 'P171110561-3':('true','Relative L2 error = 6.7e-4','Page text directly associates relative L2 error with value'),
 'P221007182-1':('false','44 is a bibliography citation [44]','RMSE in this sentence is discussed conceptually, no RMSE=44'),
 'P210209618-2':('false','5.8 refers to equation (5.8)','Recall here is the English verb, not classification recall'),
 'P220313181-1':('false','4.1 is a subsection number','Section 4.1 is titled Neural Network Architectures; no accuracy 4.1 claim'),
 'P191003193-2':('false','0.01 is learning rate','MSE is the metric, 0.01 is explicitly a learning-rate setting'),
 'P171110561-2':('false','2.2 is subsection number','2.2 introduces the Schrodinger example, not reported model accuracy'),
 'P230213971-3':('false','10 is table number','Table 10 is a reference to a results table, not reported accuracy 10'),
}

def audit(dataset:Path,pdf_dir:Path,out:Path)->dict:
    byid={r['candidate_id']:r for r in csv.DictReader((dataset/'annotations/candidate_evidence.csv').open(encoding='utf-8-sig'))}
    index={r['paper_id']:r for r in csv.DictReader((dataset/'PAPER_INDEX_30.csv').open(encoding='utf-8-sig'))}
    result=[]
    for cid,(decision,interpretation,reason) in CASES.items():
        r=byid[cid];idx=index[r['paper_id']];f=pdf_dir/idx['local_filename']
        sha=hashlib.sha256(f.read_bytes()).hexdigest()
        if sha!=r['pdf_sha256'] or sha!=idx['pdf_sha256']:
            raise RuntimeError(f'frozen PDF SHA failed for {cid}')
        with fitz.open(f) as doc:page=doc[int(r['page'])-1].get_text('text',sort=True)
        if len(page)<200:raise ValueError('Unusable PDF text '+cid)
        if r['metric_candidate'] and r['metric_candidate'].lower() not in page.lower():
            raise ValueError('Metric not on source page '+cid)
        if r['value_candidate'] and r['value_candidate'] not in page:
            raise ValueError('Value not on source page '+cid)
        result.append({'candidate_id':cid,'paper_id':r['paper_id'],'pdf_sha256':sha,
                       'page':r['page'],'metric_candidate':r['metric_candidate'],
                       'value_candidate':r['value_candidate'],
                       'metric_value_relation_supported':decision,
                       'verified_interpretation':interpretation,
                       'reason':reason,'review_provenance':'assistant_single_pass_source_page',
                       'two_distinct_human_reviewers':False,
                       'eligible_for_human_gold':False})
    out.mkdir(parents=True,exist_ok=True)
    with (out/'pilot_12_literature_checked.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=result[0].keys());w.writeheader();w.writerows(result)
    pos=sum(row['metric_value_relation_supported']=='true' for row in result)
    n=len(result)
    summary={'reviewed_assistant_single_pass':n,'positive_metric_value_link':pos,
             'negative_numeric_false_relation':n-pos,
             'naive_all_positive_precision_on_balanced_hard_case_pilot':pos/n,
             'selection':'case-control, deliberately stratified for hard negatives; not generalizable',
             'two_human_review_count':0, 'official_model_precision':None}
    (out/'pilot_audit_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',required=True,type=Path)
    p.add_argument('--pdf-dir',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args();audit(a.dataset,a.pdf_dir,a.out)
