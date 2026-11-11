#!/usr/bin/env python3
"""Conservative manual-gold verification for the 30 real-PDF benchmark.

No annotated gold => no precision/recall/F1. PDF hashes and reviewer provenance
are checked; ambiguous rows do not silently become gold.
"""
from __future__ import annotations
import argparse,csv,hashlib,json
from pathlib import Path

def csvrows(path):
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def normalized(v):return ' '.join(str(v or '').strip().lower().split())

def validate(root:Path, *,predictions:Path|None=None,pdf_dir:Path|None=None):
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    pdf_dir=pdf_dir or root/'pdfs'
    papers={p['paper_id']:p for p in manifest['papers']}
    candidates=csvrows(root/'annotations/candidate_evidence.csv')
    byid={c['candidate_id']:c for c in candidates}
    ids=set(byid)
    if len(ids)!=len(candidates):raise ValueError('Duplicate candidate IDs')
    for c in candidates:
        p=papers.get(c['paper_id'])
        if p is None or c['pdf_sha256']!=p['sha256']:raise ValueError('Bad PDF ID/hash')
        if not 1<=int(c['page'])<=p['pages']:raise ValueError('Page out of bounds')
    a=csvrows(root/'annotations/review_A.csv');b=csvrows(root/'annotations/review_B.csv')
    aa={x['candidate_id']:x for x in a};bb={x['candidate_id']:x for x in b}
    if set(aa)!=ids or set(bb)!=ids:raise ValueError('Reviewer tables do not cover all candidates')
    for r in a+b:
        if r['paper_id']!=byid[r['candidate_id']]['paper_id'] or r['pdf_sha256']!=byid[r['candidate_id']]['pdf_sha256']:
            raise ValueError('Reviewer source mismatch')
    allowed={'accepted','rejected','needs_recheck'}
    decisions=[];agreed=[];conflicts=[]
    for cid in sorted(ids):
        ra,rb=aa[cid],bb[cid]
        da,db=normalized(ra['decision']),normalized(rb['decision'])
        if da and da not in allowed or db and db not in allowed:raise ValueError(f'Invalid reviewer decision for {cid}')
        if da and db:
            decisions.append(cid)
            fields=('correct_metric','correct_value','correct_dataset','correct_method','correct_baseline','correct_setting','correct_quote')
            if da==db and all(normalized(ra[f])==normalized(rb[f]) for f in fields):
                agreed.append(cid)
            else:conflicts.append(cid)
    gold=csvrows(root/'annotations/adjudicated_gold.csv')
    resolved={r['candidate_id']:r for r in gold if r['candidate_id'] in ids and normalized(r.get('decision')) in {'accepted','rejected'}}
    # Both reviewers selecting needs_recheck is NOT a completed judgment.
    # Only unanimous final A/B decisions or explicit adjudication become gold.
    final_decisions={}
    for cid in ids:
        if cid in resolved:
            final_decisions[cid]=normalized(resolved[cid]['decision'])
        elif cid in agreed and normalized(aa[cid]['decision']) in ('accepted','rejected'):
            final_decisions[cid]=normalized(aa[cid]['decision'])
    accepted=[cid for cid,decision in final_decisions.items() if decision=='accepted']
    annotated=len(decisions)
    verified=len(final_decisions)==len(ids)
    status={'status':'human_annotated_complete' if verified else 'pending_human_review',
            'papers':len(papers),'candidate_evidence':len(ids),'independently_double_reviewed':annotated,
            'review_agreement_items':len(agreed),'adjudication_needed':len(conflicts),
            'adjudication_resolved':sum(cid in resolved for cid in conflicts),
            'accepted_human_gold':len(set(accepted)),
            'model_precision':None,'model_recall':None,'model_f1':None}
    # Only the full six-field match against adjudicated expert gold represents
    # experiment extraction accuracy; candidate selection is a separate task.
    if predictions is not None and verified:
        prediction=json.loads(predictions.read_text(encoding='utf-8'))
        records=prediction.get('records')
        if not isinstance(records,list):
            raise ValueError('Prediction JSON must contain records list')
        def gold_row(cid):
            row=resolved.get(cid,aa[cid])
            original=byid[cid]
            def preferred(k,candidate):return str(row.get(k) or original.get(candidate) or '').strip()
            return {'paper_id':original['paper_id'],'page':original['page'],
                'method':preferred('correct_method','method_candidate'),
                'dataset':preferred('correct_dataset','dataset_candidate'),
                'metric':preferred('correct_metric','metric_candidate'),
                'value':preferred('correct_value','value_candidate'),
                'quote':preferred('correct_quote','source_quote')}
        truth_rows=[gold_row(cid) for cid in sorted(set(accepted))]
        # A scientific gold set requires the exact original PDF bytes and a
        # page-local quotation. A completed CSV alone cannot establish gold.
        try:
            import fitz
        except ImportError:
            status['scoring_blocker']='pymupdf_required_for_verifying_gold_quotes'
            return status
        for paper_id,source in papers.items():
            path=pdf_dir/(paper_id+source['version']+'.pdf')
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:
                status['scoring_blocker']='source_pdf_missing_or_changed'
                return status
            selected=[row for row in truth_rows if row['paper_id']==paper_id]
            if not selected:continue
            with fitz.open(path) as pdf:
                for row in selected:
                    page=int(row['page'])-1
                    if page<0 or page>=pdf.page_count or not normalized(row['quote']) in normalized(pdf[page].get_text('text',sort=True)):
                        status['scoring_blocker']='source_quote_not_found_on_gold_page'
                        return status
        fields=('paper_id','page','method','dataset','metric','value')
        if any(any(not str(g.get(f) or '').strip() for f in fields) for g in truth_rows):
            status['scoring_blocker']='accepted_human_gold_has_missing_fields'
            return status
        def key(rec):
            if not isinstance(rec,dict):return None
            if any(f not in rec for f in fields):return None
            return tuple(normalized(rec[f]) for f in fields)
        gold_keys=set(key(rec) for rec in truth_rows)
        pred_keys=[key(rec) for rec in records]
        # Identical duplicate predictions count in precision's denominator.
        true_positive=len(gold_keys.intersection(k for k in pred_keys if k is not None))
        precision=true_positive/len(pred_keys) if pred_keys else 0.0
        recall=true_positive/len(gold_keys) if gold_keys else None
        f1=2*precision*recall/(precision+recall) if recall is not None and precision+recall else None
        status.update(model_precision=round(precision,4),model_recall=round(recall,4) if recall is not None else None,
                      model_f1=round(f1,4) if f1 is not None else None,
                      model_true_positive=true_positive,model_output_records=len(pred_keys),
                      gold_extraction_records=len(gold_keys),
                      metric_definition='exact paper+page+method+dataset+metric+value, duplicate predictions penalized')
    return status

def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset',required=True,type=Path);p.add_argument('--predictions',type=Path);p.add_argument('--out',type=Path)
    a=p.parse_args();r=validate(a.dataset,predictions=a.predictions)
    output=json.dumps(r,ensure_ascii=False,indent=2)
    if a.out:a.out.write_text(output,encoding='utf-8')
    print(output)
if __name__=='__main__':main()
