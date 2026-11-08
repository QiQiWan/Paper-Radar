# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""33 real arXiv metadata records, optional verified PDFs, expert review stats."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from paper_radar.benchmark_quality import summary
from tools import eval_real_papers as legacy

BENCH=ROOT/'benchmarks'/'real_papers_v2'

def main(argv=None):
    parser=argparse.ArgumentParser(description='V1.30 REAL paper evaluation: does not claim unmeasured accuracy')
    parser.add_argument('command',choices=('coverage','fetch','score','template'))
    parser.add_argument('--pdf-dir',type=Path,default=BENCH/'pdfs')
    parser.add_argument('--predictions',type=Path,default=None)
    parser.add_argument('--output',type=Path,default=ROOT/'reports'/'real_papers_v2')
    args=parser.parse_args(argv)
    # Legacy evaluator is read-only except explicit fetch/output; its 3 seed
    # evidence rows stay distinct from the other 30 unreviewed papers.
    legacy.BENCH=BENCH
    if args.command=='fetch':
        result={'download_receipts':legacy.fetch(args.pdf_dir),'status':'Download only; not human reviewed'}
    elif args.command=='template':
        result=legacy.create_annotation_template(args.output)
    else:
        result=summary(BENCH,args.predictions)
        if args.command=='score':
            result['seed_diagnostics']=legacy.run(args.pdf_dir,args.predictions,args.output)
            result['note']='Seed diagnostics use 6 records in 3 original papers. Official model F1 requires adjudicated gold.'
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'evaluation_v130.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__':raise SystemExit(main())
