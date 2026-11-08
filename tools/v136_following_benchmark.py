# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Offline 1,000-paper following API benchmark, including sustained HTTP polling.

Usage: python -m tools.v136_following_benchmark --requests 240 --soak 15 --output evidence/following.json
Metadata and change events are synthetic. No public source API or user data is touched.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import json
from pathlib import Path
from time import perf_counter,sleep

from paper_radar import __version__
from paper_radar.core import DEFAULTS
from paper_radar.following import plan_windows,record_cycle,status
from paper_radar.models import Paper
from tools.v131_workspace_benchmark import workspace_server,response_http,pct


def run(*,paper_count=1000,requests=240,workers=8,soak_seconds=10):
    if paper_count<1000 or requests<16 or workers<1 or workers>24 or soak_seconds<0:
        raise ValueError('Need >=1,000 papers and >=16 requests, workers 1..24, soak >=0')
    with workspace_server(papers=paper_count) as env:
        portal,uid=env['portal'],env['user']['id']
        now=datetime.now(timezone.utc)
        cfg=dict(DEFAULTS)
        cfg.update(active_topic_id='default',sources=['arxiv'],keywords=['Neural Operator'])
        papers=[]
        for i in range(80):
            pid=f'synthetic:v131:{i:06d}'
            papers.append(Paper(pid,f'Synthetic Neural Operator change {i}',
                'Synthetic benchmark metadata only','Benchmark author',
                '2026-11-07','2026-11-08','cs.LG','','',source='arxiv'))
        with portal.workspace(uid) as db:
            cfg['_following_windows']=plan_windows(db,cfg,now)
            seeded=record_cycle(db,cfg,papers,papers,
                [{'name':'arxiv','ok':True,'partial':False,'count':80,
                  'queries':[{'ok':True,'count':80,'saturated':False}]}],
                new_to_topic={p.paper_id for p in papers},now=now)
            before=status(db,'default')
        assert before['unread']==80 and seeded['events']==80
        cases=['/api/public/following?topic_id=default',
               '/api/public/dashboard?topic_id=default',
               '/api/public/papers?topic_id=default&limit=30&sort=recent',
               '/api/public/topics']
        duration=[];failures=[]
        def request_case(i):
            path=cases[i%len(cases)]
            response=response_http(env['base'],env['token'],path)
            if not response['ok']:
                return response['duration_ms'],f"{response.get('status')} {response.get('error','')}"
            data=response['body']
            valid=bool(data.get('events')) if i%len(cases)==0 else True
            return response['duration_ms'],'' if valid else 'missing_follow_events'
        wall_start=perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for result in as_completed([pool.submit(request_case,i) for i in range(requests)]):
                elapsed,error=result.result();duration.append(elapsed)
                if error:failures.append(error)
        wall=perf_counter()-wall_start
        soak_start=perf_counter();soak_count=0;soak_errors=0;soak_durations=[]
        while perf_counter()-soak_start<soak_seconds:
            elapsed,error=request_case(soak_count)
            soak_errors+=int(bool(error));soak_count+=1;soak_durations.append(elapsed)
            sleep(.025)
        with portal.workspace(uid) as db:
            persisted=status(db,'default')['unread']
            integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
    passed=not failures and soak_errors==0 and persisted==80 and integrity=='ok'
    return {'version':__version__,'kind':'isolated_synthetic_following_http',
        'metadata_papers':paper_count,'real_pdfs':0,'synthetic_events':80,
        'requests':requests,'concurrency':workers,'completed':requests-len(failures),
        'completion_rate':round((requests-len(failures))/requests,4),
        'error_rate':round(len(failures)/requests,5),
        'p50_ms':pct(duration,50),'p95_ms':pct(duration,95),
        'elapsed_seconds':round(wall,3),'throughput_rps':round(requests/max(.001,wall),2),
        'soak':{'seconds':soak_seconds,'requests':soak_count,'errors':soak_errors,
                'p95_ms':pct(soak_durations,95)},
        'persisted_unread_after_reopen':persisted,'sqlite_integrity':integrity,
        'failure_examples':failures[:10], 'gate_passed':passed}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--papers',type=int,default=1000)
    ap.add_argument('--requests',type=int,default=240)
    ap.add_argument('--workers',type=int,default=8)
    ap.add_argument('--soak',type=int,default=10)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args(argv)
    report=run(paper_count=args.papers,requests=args.requests,workers=args.workers,soak_seconds=args.soak)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return 0 if report['gate_passed'] else 1


if __name__=='__main__':raise SystemExit(main())
