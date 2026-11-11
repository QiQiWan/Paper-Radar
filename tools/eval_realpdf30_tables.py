"""Build three PDF-table cell-coordinate demonstrations from real immutable PDFs.

Geometric table cells are candidates; merged layout cells may contain several
methods/numbers. This tool does not assert scientific row-to-value relations.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import json
import hashlib
from paper_radar.pdf_geometry import page_tables

PAGES=[
    ('2010.08895',8,'Fourier Neural Operator / Table 1'),
    ('2109.03697',19,'U-FNO / Table 2'),
    ('2111.03794',13,'PINO / Tables 2-3')
]

def run(dataset:Path,pdf_dir:Path,output:Path):
    manifest=json.loads((dataset/'manifest.json').read_text(encoding='utf-8'))
    source={p['paper_id']:p for p in manifest['papers']}
    output.mkdir(parents=True,exist_ok=True)
    records=[]
    for paper_id,page,description in PAGES:
        paper=source[paper_id]
        file=pdf_dir/(paper['paper_id']+paper['version']+'.pdf')
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=paper['sha256']:
            raise ValueError('Frozen PDF missing or changed: '+file.name)
        result=page_tables(file,paper['sha256'],page)
        records.append({'paper_id':paper_id,'version':paper['version'],
                        'title':paper['title'],'page':page,'description':description,
                        'detected_ruled_tables':len(result['tables']),
                        'tables':result['tables'],
                        'relation_gold_verified':False})
    (output/'tables_geometry_review.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    summary={'pages':len(records),'pages_with_table_cells':sum(bool(r['tables']) for r in records),
             'total_rule_grid_tables':sum(len(r['tables']) for r in records),
             'total_row_column_cells':sum(len(t['cells']) for r in records for t in r['tables']),
             'scientific_numeric_relation_verified':0,
             'note':'Detected table cells are layout geometry, not verified method/value pairs.'}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--dataset',type=Path,default=Path('benchmarks/real_pdf30'))
    ap.add_argument('--pdf-dir',required=True,type=Path)
    ap.add_argument('--output',type=Path,default=Path('realpdf30_table_probes_v1452'))
    args=ap.parse_args()
    print(json.dumps(run(args.dataset,args.pdf_dir,args.output),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
