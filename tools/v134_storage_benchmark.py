# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Reproducible local SQLite connection benchmark using SYNTHETIC metadata.

No network, no PDFs, no real user records. Output records measurement environment.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import tempfile
import time
from paper_radar.core import connect


def percentile(values:list[float],p:float)->float:
    values=sorted(values)
    if not values:return 0.0
    return values[min(len(values)-1,max(0,int((len(values)-1)*p)))]


def run(papers=1000,requests=400)->dict:
    with tempfile.TemporaryDirectory(prefix='paper-radar-v134-bench-') as tmp:
        path=Path(tmp)/'papers.sqlite3'
        start=time.perf_counter()
        db=connect(path)
        initial_ms=(time.perf_counter()-start)*1000
        db.executemany('''INSERT INTO papers
            (paper_id,title,abstract,first_seen,source) VALUES (?,?,?,?,?)''',
            ((f'synthetic:{i}',f'Paper {i}',f'Abstract about mechanics {i}',
              '2026-11-08T12:00:00','arxiv') for i in range(papers)))
        db.commit();db.close()
        values=[];errors=[]
        start=time.perf_counter()
        for idx in range(requests):
            tic=time.perf_counter()
            try:
                with connect(path) as conn:
                    count=conn.execute('SELECT COUNT(*) FROM papers').fetchone()[0]
                    if count!=papers:raise ValueError('data not retained')
            except Exception as exc: errors.append(type(exc).__name__)
            values.append((time.perf_counter()-tic)*1000)
        total=(time.perf_counter()-start)
        with sqlite3.connect(path) as conn:
            integrity=conn.execute('PRAGMA quick_check').fetchone()[0]
        return {'version':'v1.34.0','synthetic_papers':papers,'connection_reads':requests,
          'successful':requests-len(errors),'errors':errors,
          'initialization_ms':round(initial_ms,3),'read_p50_ms':round(percentile(values,.5),3),
          'read_p95_ms':round(percentile(values,.95),3),
          'read_p99_ms':round(percentile(values,.99),3),
          'reads_per_second':round(requests/total,1),
          'sqlite_quick_check':integrity,
          'environment':{'os':platform.platform(),'python':platform.python_version(),
                         'sqlite':sqlite3.sqlite_version,'pid':os.getpid()},
          'note':'Synthetic SQLite metadata reads in an isolated container; not PDF throughput or Windows disk tests'}


def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument('--papers',type=int,default=1000)
    ap.add_argument('--requests',type=int,default=400)
    ap.add_argument('--out',type=Path)
    args=ap.parse_args(argv)
    data=run(papers=args.papers,requests=args.requests)
    raw=json.dumps(data,indent=2,ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(raw+'\n',encoding='utf-8')
    print(raw)
    return 0 if not data['errors'] and data['sqlite_quick_check']=='ok' else 1

if __name__=='__main__':raise SystemExit(main())
