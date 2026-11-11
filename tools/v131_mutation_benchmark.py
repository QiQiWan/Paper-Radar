# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Authenticated concurrent write/reading-state persistence in 1,000-paper workspace.

Writes are isolated in a temporary SQLite workspace; no real data is modified.
Use in addition to read-heavy v131_workspace_benchmark.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from pathlib import Path
import platform
from time import perf_counter
from paper_radar import __version__
from tools.v131_workspace_benchmark import workspace_server,response_http,pct


def run(*,papers:int=1000,writes:int=100,concurrency:int=8,enforce:bool=False):
    if not 100<=papers<=5000 or not 1<=writes<=min(papers,500) or not 1<=concurrency<=32:
        raise ValueError('Use >=100 papers, 1..paper_count writes, and 1..32 workers')
    with workspace_server(papers=papers) as env:
        start=perf_counter()
        def mutate(i:int):
            paper_id=f'synthetic:v131:{i:06d}'
            value=response_http(env['base'],env['token'],'/api/public/papers/flags',
                 method='POST',csrf=env['csrf'],body={
                  'topic_id':'default','paper_id':paper_id,'is_starred':True,'reading_status':'done'})
            ok=value['status']==200 and isinstance(value.get('body'),dict) and (
                value['body'].get('paper_id')==paper_id and
                value['body'].get('is_starred') is True and
                value['body'].get('reading_status')=='done')
            return {'paper_id':paper_id,'duration_ms':value['duration_ms'],
                    'ok':ok,'status':value['status']}
        with ThreadPoolExecutor(max_workers=concurrency) as workers:
            rows=list(workers.map(mutate,range(writes)))
        wall=perf_counter()-start
        with env['portal'].workspace(env['user']['id']) as db:
            ids=[f'synthetic:v131:{i:06d}' for i in range(writes)]
            count=db.execute('''SELECT count(*) FROM topic_paper_flags
                WHERE topic_id='default' AND is_starred=1 AND reading_status='done'
                AND paper_id IN ('''+','.join('?' for _ in ids)+')',ids).fetchone()[0]
    passed=sum(row['ok'] for row in rows)
    result={'schema_version':1,'version':__version__,'mode':'authenticated_isolated_concurrent_writes',
        'host':platform.platform(),'paper_count':papers,'pdf_count':0,'writes':writes,
        'concurrency':concurrency,'completed':passed,'persisted_after_reopen':count,
        'completion_rate':round(passed/writes,4),'error_rate':round(1-passed/writes,4),
        'p50_ms':pct([row['duration_ms'] for row in rows],50),
        'p95_ms':pct([row['duration_ms'] for row in rows],95),
        'p99_ms':pct([row['duration_ms'] for row in rows],99),
        'wall_ms':round(wall*1000,2),
        'failures':[{'paper_id':r['paper_id'],'status':r['status']} for r in rows if not r['ok']][:10],
        'gate':{'enforced':enforce,'passed':passed==writes and count==writes},
        'at':datetime.now(timezone.utc).isoformat(timespec='seconds')}
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--papers',type=int,default=1000)
    parser.add_argument('--writes',type=int,default=100)
    parser.add_argument('--concurrency',type=int,default=8)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--enforce',action='store_true')
    args=parser.parse_args(argv)
    result=run(papers=args.papers,writes=args.writes,concurrency=args.concurrency,enforce=args.enforce)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('writes','completed','error_rate','p95_ms','persisted_after_reopen')},ensure_ascii=False))
    return int(args.enforce and not result['gate']['passed'])

if __name__=='__main__':raise SystemExit(main())
