#!/usr/bin/env python3
"""Prepare source-verified *non-human* review staging for frozen RealPDF30.

This tool MUST NOT populate human reviewer A/B, adjudication, or gold files.
It verifies PDF hashes and textual presence, then flags candidate numeric
relations requiring actual independent human inspection.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, unicodedata
from collections import Counter
from pathlib import Path
import fitz


def norm(text):
    text=unicodedata.normalize('NFKC',str(text or ''))
    text=text.replace('\u00ad','').replace('−','-').replace('\ufb01','fi').replace('\ufb02','fl')
    return re.sub(r'\s+',' ',text).strip().casefold()


def verify(root, pdf_dir, out):
    out.mkdir(parents=True,exist_ok=True)
    with (root/'PAPER_INDEX_30.csv').open(encoding='utf-8-sig',newline='') as file:
        index={r['paper_id']:r for r in csv.DictReader(file)}
    with (root/'annotations/candidate_evidence.csv').open(encoding='utf-8-sig',newline='') as file:
        candidates=list(csv.DictReader(file))
    geometry={}
    geometries=out.parent/'geometry_base/geometry/geometry_candidates.jsonl'
    if geometries.is_file():
        for line in geometries.read_text(encoding='utf8').splitlines():
            if line.strip():
                row=json.loads(line); geometry[row['candidate_id']]=row
    docs={}
    for pid, r in index.items():
        file=pdf_dir/r['local_filename']
        if not file.is_file():raise FileNotFoundError(f'Frozen PDF missing: {file}')
        digest=hashlib.sha256(file.read_bytes()).hexdigest()
        if digest != r['pdf_sha256']:raise ValueError(f'PDF hash mismatch: {pid}')
        docs[pid]=fitz.open(file)
    outputs=[];classes=Counter();text_missing=[]
    for row in candidates:
        pid=row['paper_id'];page=int(row['page']);assert row['pdf_sha256']==index[pid]['pdf_sha256']
        doc=docs[pid]
        if not (1<=page<=len(doc)):raise ValueError(f'Page invalid: {pid}:{page}')
        content=norm(doc[page-1].get_text('text',sort=True))
        quote=norm(row['source_quote'])
        # Allow layout whitespace and ligature normalization. The v1.45.2
        # coordinates are carried as separate evidence in geometry_candidates.
        contiguous=bool(quote and quote in content)
        result='needs_human_scientific_relation_review'
        metric=row['metric_candidate'].strip();value=row['value_candidate'].strip()
        if not metric and not value:
            result='context_only_no_numeric_claim'
        elif not metric:
            result='number_without_verified_metric'
        elif not value:
            result='metric_without_verified_value'
        else:
            if re.search(r'\[\s*'+re.escape(value)+r'\s*\]',row['source_quote']):
                result='citation_number_false_result_risk'
            elif (re.search(r'(?:section|subsection|fig(?:ure)?|table|eq(?:uation)?|theorem|appendix)\s*'+re.escape(value)+r'\b',row['source_quote'],re.I)
                  or (len(value)<5 and re.match(r'^\d+\.\d+$',value) and re.search(r'\b'+re.escape(value)+r'\.',row['source_quote']))):
                result='section_figure_equation_number_risk'
            elif 'RMSE' in metric.upper() and re.search(r'\b(?:\d{2,4}\s{2,}|\d{2,4}\n)',row['source_quote']):
                result='plot_axis_or_table_header_risk'
        geo=geometry.get(row['candidate_id'],{})
        if not contiguous and not bool(geo.get('text_matched')):
            text_missing.append(row['candidate_id'])
        classes[result]+=1
        outputs.append({
            **row,'pdf_sha_verified':'true','physical_page_valid':'true',
            'quote_in_sorted_text':'true' if contiguous else 'false',
            'quote_found_by_v1452_geometry':'true' if geo.get('text_matched') else 'false',
            'geometry_status':geo.get('geometry_status') or 'unknown',
            'pre_annotation_class':result,
            'metric_value_relation_confirmed':'false',
            'review_provenance':'automated_staging_only',
            'human_A_verified':'false','human_B_verified':'false',
            'eligible_for_formal_gold':'false',
            'review_instruction':'Verify the PDF table row/column, unit, method, data split and baseline before labelling a quantitative relation.'})
    if text_missing:raise ValueError('No quote match for '+','.join(text_missing))
    with (out/'candidate_staging_all_117.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=outputs[0].keys());w.writeheader();w.writerows(outputs)
    for doc in docs.values():doc.close()
    stats={'papers_hash_verified':len(index),'candidate_count':len(outputs),
           'text_contiguous_in_current_fitz_sorted':sum(r['quote_in_sorted_text']=='true' for r in outputs),
           'either_text_or_geometry_found':sum(r['quote_in_sorted_text']=='true' or r['quote_found_by_v1452_geometry']=='true' for r in outputs),
           'categories':dict(sorted(classes.items())),'human_double_verified':0,
           'scientific_numeric_relation_precision':None,
           'warning':'Automated pre-annotations are NOT human gold; source presence is not proof of row/column metric-value attribution.'}
    (out/'audit_summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(stats,ensure_ascii=False,indent=2))
    return stats

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--pdf-dir',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();verify(a.dataset,a.pdf_dir,a.out)
