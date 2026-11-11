#!/usr/bin/env python3
"""Check historical six seed labels against pinned local PDFs; illustrative only."""
from pathlib import Path
import hashlib,json,re,fitz
BASE=Path(__file__).resolve().parents[1]
ROOT=BASE/'docs'/'historical_seed_evidence.json'

def main():
    m=json.loads((BASE/'manifest.json').read_text(encoding='utf-8'))
    byid={p['paper_id']:p for p in m['papers']}
    seeds=json.loads(ROOT.read_text(encoding='utf-8'))['fields']
    rows=[]
    for seed in seeds:
        p=byid[seed['paper_id']]
        path=BASE/'pdfs'/(p['paper_id']+p['version']+'.pdf')
        assert hashlib.sha256(path.read_bytes()).hexdigest()==p['sha256']
        with fitz.open(path) as pdf:
            source=' '.join(pdf[seed['page_index']].get_text().split()).lower()
        method_present=seed['context_anchor'].casefold() in source
        value_present=str(seed['value']).casefold() in source
        quote_present=' '.join(seed['quote'].split()).casefold() in source
        rows.append({'seed_id':seed['id'],'paper_id':seed['paper_id'],'version':p['version'],
                     'page':seed['page_index']+1,'pdf_sha256':p['sha256'],
                     'method':seed['method'],'dataset':seed['dataset'],'metric':seed['metric'],
                     'value':seed['value'],'source_table':seed['reference'],
                     'method_and_value_present_on_page':method_present and value_present,
                     'text_quote_contiguous':quote_present,
                     'layout_pilot_review':'visually checked in rendered page, single-check illustrative only',
                     'formal_gold':False})
    (BASE/'pilot_seed_page_checks.json').write_text(json.dumps({'status':'six_existing_historical_seed_rows_rechecked_single_review_not_double_gold',
        'rows':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    print('pilot_seeds',len(rows),'page_anchors_found',sum(x['method_and_value_present_on_page'] for x in rows))
if __name__=='__main__':main()
