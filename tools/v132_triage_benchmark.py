# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Authenticated 1,000-paper triage benchmark using the actual HTTP + SQLite routes.

All records are synthetic metadata. This does not benchmark PDF parsing, LLM
inference, real human interaction, or an OS installer.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import json
from pathlib import Path
import platform
from statistics import median
from time import perf_counter
from urllib import request

from paper_radar import __version__
from tools.v131_workspace_benchmark import workspace_server,pct,response_http


def _send(env,route,*,method='GET',body=None):
    return response_http(env['base'],env['token'],route,
                         method=method,body=body,csrf=env['csrf'])


def _download(env,route):
    t=perf_counter()
    req=request.Request(env['base']+route,headers={'Cookie':'PRSession='+env['token']})
    try:
        with request.urlopen(req,timeout=12) as res:
            raw=res.read(6_000_000)
            status=res.status
            type_=res.headers.get('Content-Type','')
        return {'ok':status==200,'status':status,'size':len(raw),'mime':type_,
                'duration_ms':(perf_counter()-t)*1000,'raw':raw}
    except Exception as exc:
        return {'ok':False,'status':0,'size':0,'error':type(exc).__name__,
                'duration_ms':(perf_counter()-t)*1000}


def run(*,papers=1000,note_writes=80,batches=10,concurrency=8,p95_budget_ms=1500,enforce=False):
    if not (100<=papers<=5000 and 10<=note_writes<=min(200,papers//3)
            and 1<=batches<=min(30,papers//30) and 1<=concurrency<=20):
        raise ValueError('Benchmark parameters out of range')
    metrics=[]
    with workspace_server(papers=papers) as env:
        # Strictly keep write-ID sets disjoint to detect cross-step partial changes.
        def note_job(i):
            pid=f'synthetic:v131:{i:06d}'
            body={'topic_id':'default','paper_id':pid,'note':'Human test decision '+str(i),
                  'next_action':'read' if i%2 else 'replicate','expected_revision':0}
            return ('note',i,_send(env,'/api/public/papers/note',method='POST',body=body))
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures=[pool.submit(note_job,i) for i in range(note_writes)]
            for future in as_completed(futures):
                kind,i,result=future.result();metrics.append((kind,result))
        # Distinct 10-paper batches exercise consistent writes across one SQLite transaction.
        for i in range(batches):
            ids=[f'synthetic:v131:{note_writes+i*10+j:06d}' for j in range(10)]
            result=_send(env,'/api/public/papers/bulk',method='POST',body={
                'topic_id':'default','paper_ids':ids,'action':'star'})
            metrics.append(('batch',result))
        scenarios=[
            '/api/public/dashboard?topic_id=default',
            '/api/public/papers?topic_id=default&filter=inbox&limit=30',
            '/api/public/papers?topic_id=default&filter=actionable&limit=30',
            '/api/public/papers?topic_id=default&filter=starred&limit=30',
            '/api/public/papers?topic_id=default&filter=all&offset=840&limit=30',
        ]
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures=[pool.submit(_send,env,scenarios[i%len(scenarios)]) for i in range(180)]
            for fut in as_completed(futures):metrics.append(('read',fut.result()))
        exports={}
        for scope,format_ in [('actionable','md'),('actionable','csv'),('starred','md'),('starred','csv')]:
            key=scope+'_'+format_
            r=_download(env,f'/api/public/reading/export?topic_id=default&scope={scope}&format={format_}')
            metrics.append(('export',r));exports[key]={'ok':r['ok'],'size':r['size'],
                'ms':round(r['duration_ms'],2),'mime':r.get('mime','')}
        # Must reopen the same user SQLite database and check persisted state explicitly.
        with env['portal'].workspace(env['user']['id']) as db:
            present_notes=db.execute("SELECT COUNT(*) FROM paper_notes WHERE topic_id='default' AND next_action<>'none'").fetchone()[0]
            star_count=db.execute("SELECT COUNT(*) FROM topic_paper_flags WHERE topic_id='default' AND is_starred=1").fetchone()[0]
            integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        groups={}
        for kind in ('note','batch','read','export'):
            rows=[r for group,r in metrics if group==kind]
            ms=[r['duration_ms'] for r in rows]
            errors=[r for r in rows if not r['ok']]
            groups[kind]={'count':len(rows),'ok':len(rows)-len(errors),
                'error_rate':round(len(errors)/len(rows),5),'p50_ms':pct(ms,50),
                'p95_ms':pct(ms,95),'p99_ms':pct(ms,99),
                'max_ms':round(max(ms),2),
                'error_statuses':dict(Counter(str(r.get('status')) for r in errors))}
        reasons=[]
        if any(r['error_rate']>0 for r in groups.values()):reasons.append('http_errors')
        if present_notes!=note_writes:reasons.append('notes_not_persisted')
        if star_count < batches*10:reasons.append('batch_state_not_persisted')
        if integrity!='ok':reasons.append('sqlite_integrity')
        if any(r['p95_ms']>p95_budget_ms for r in groups.values()):reasons.append('p95_budget_exceeded')
        return {'version':__version__,'mode':'synthetic_1000_papers_authenticated_http_sqlite',
            'at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'environment':{'platform':platform.platform(),'python':platform.python_version()},
            'data':{'paper_metadata':papers,'pdfs':0,'user_accounts':1,
                    'human_participants':0},'settings':{'concurrency':concurrency,
                    'note_writes':note_writes,'batches':batches,'batch_size':10,
                    'p95_budget_ms':p95_budget_ms},'groups':groups,'exports':exports,
            'persisted':{'notes':present_notes,'starred_total':star_count,
                         'integrity':integrity},
            'gate':{'passed':not reasons,'reasons':reasons}}


def evaluate_repeated_trials(trials: list[dict], p95_budget_ms: float) -> dict:
    """Require every trial to preserve data, and typical P95 to meet the budget.

    CI runners share resources, so one sporadic high-latency trial should
    remain visible in evidence without turning a healthy Release red.
    Persistent performance degradation still fails the three-trial median.
    """
    if not trials:
        raise ValueError('At least one benchmark trial is required')
    data_errors = sorted({reason for trial in trials
                          for reason in trial['gate']['reasons']
                          if reason != 'p95_budget_exceeded'})
    p95_by_group={kind:[float(trial['groups'][kind]['p95_ms']) for trial in trials]
                  for kind in ('note','batch','read','export')}
    median_group_p95_ms={kind:round(median(vals),2) for kind,vals in p95_by_group.items()}
    reasons=list(data_errors)
    if any(value > p95_budget_ms for value in median_group_p95_ms.values()):
        reasons.append('p95_budget_exceeded')
    return {'passed':not reasons, 'reasons':reasons,
            'trial_count':len(trials), 'median_group_p95_ms':median_group_p95_ms,
            'trial_group_p95_ms':p95_by_group}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--papers',type=int,default=1000)
    parser.add_argument('--note-writes',type=int,default=80)
    parser.add_argument('--batches',type=int,default=10)
    parser.add_argument('--concurrency',type=int,default=8)
    parser.add_argument('--p95-budget-ms',type=float,default=1500)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--enforce',action='store_true')
    parser.add_argument('--repeat',type=int,default=1,choices=(1,3),help='Independent trials (3 for noisy CI performance gates)')
    a=parser.parse_args(argv)
    trials=[run(papers=a.papers,note_writes=a.note_writes,batches=a.batches,
                concurrency=a.concurrency,p95_budget_ms=a.p95_budget_ms,enforce=a.enforce)
            for _ in range(a.repeat)]
    if a.repeat==1:
        report=trials[0]
        summary={'groups':report['groups'],'persisted':report['persisted'],'gate':report['gate']}
    else:
        aggregate=evaluate_repeated_trials(trials,a.p95_budget_ms)
        report={'version':__version__,'mode':'repeatable_synthetic_http_sqlite_ci',
                'settings':{'repeat':a.repeat,'p95_budget_ms':a.p95_budget_ms,
                            'concurrency':a.concurrency,'note_writes':a.note_writes,'batches':a.batches},
                'gate':{'passed':aggregate['passed'],'reasons':aggregate['reasons']},
                'aggregation':aggregate,'trials':trials}
        summary={'gate':report['gate'],'aggregation':aggregate}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return int(a.enforce and not report['gate']['passed'])

if __name__=='__main__':raise SystemExit(main())
