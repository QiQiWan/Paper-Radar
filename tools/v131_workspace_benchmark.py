# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Offline, reproducible HTTP workload for an isolated 1,000-paper public workspace.

All records are synthetic and generated into a TemporaryDirectory. No third-party
API, private account, Zotero library, or user database is ever contacted.
The HTTP server and SQLite are real; this is an integration/performance test,
not a claim about human task completion or 1,000 real PDF analyses.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import statistics
import tempfile
from threading import Thread
from time import perf_counter, sleep
from urllib import error, parse, request

from paper_radar.portal_auth import create_account, create_session, db as auth_db
from paper_radar.public_portal import Portal, make_server, utc_now
from paper_radar import __version__


def seed_workspace(portal: Portal, uid: str, count: int = 1000) -> None:
    """Deterministic dataset, 250 discoverable 'Neural Operator' titles per 1,000."""
    if not 1 <= count <= 50000:
        raise ValueError('The synthetic benchmark supports 1..50,000 papers')
    with portal.workspace(uid) as db:
        batch=[]
        memberships=[]
        flags=[]
        date=utc_now()
        for i in range(count):
            pid=f'synthetic:v131:{i:06d}'
            keyword='Neural Operator' if i%4==0 else 'Industrial Forecast'
            batch.append((pid,f'Synthetic {keyword} Study {i:06d}',
                f'Isolated benchmark document {i}. Synthetic {keyword} metadata. No actual paper or PDF.',
                f'2026-{(i%12)+1:02d}-{(i%27)+1:02d}',
                '',f'10.9999/synthetic.{i:06d}',date))
            memberships.append(('default',pid))
            if i%10==0 or i%11==0:
                flags.append(('default',pid,int(i%10==0), 'done' if i%11==0 else 'unread',0,date))
        db.executemany('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',batch)
        db.executemany('INSERT INTO topic_papers VALUES (?,?)',memberships)
        db.executemany('''INSERT INTO topic_paper_flags
            (topic_id,paper_id,is_starred,reading_status,feedback,updated_at)
            VALUES (?,?,?,?,?,?)''',flags)


@contextmanager
def workspace_server(*, papers=1000):
    with tempfile.TemporaryDirectory(prefix='paper-radar-v131-perf-') as directory:
        portal=Portal(Path(directory)/'portal',embedded_workers=False)
        with auth_db(portal.auth_path) as db:
            account=create_account(db,'benchmark_user','v131-benchmark-password-long-enough')
            token,csrf=create_session(db,account['id'])
        seed_workspace(portal,account['id'],papers)
        server=make_server('127.0.0.1',0,portal)
        thread=Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            yield {'portal':portal,'user':account,'token':token,'csrf':csrf,
                'base':f'http://127.0.0.1:{server.server_port}', 'data':directory}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def pct(values, percentage):
    if not values:return None
    ordered=sorted(values)
    index=max(0,math.ceil(percentage/100*len(ordered))-1)
    return round(ordered[index],2)


def response_http(base,token,path,*,method='GET',body=None,csrf=''):
    headers={'Cookie':'PRSession='+token}
    blob=None
    if method!='GET':
        headers.update({'Content-Type':'application/json','X-Portal-Action':'1',
                        'X-CSRF-Token':csrf,'Origin':base})
        blob=json.dumps(body or {}).encode('utf-8')
    req=request.Request(base+path,headers=headers,method=method,data=blob)
    started=perf_counter()
    try:
        with request.urlopen(req,timeout=12) as r:
            status=r.status
            raw=r.read(1_000_000)
    except error.HTTPError as exc:
        status=exc.code
        raw=exc.read(4096)
    except (OSError,TimeoutError,error.URLError) as exc:
        return {'ok':False,'duration_ms':(perf_counter()-started)*1000,
                'status':0,'error':type(exc).__name__}
    duration=(perf_counter()-started)*1000
    try:data=json.loads(raw)
    except (UnicodeError,ValueError):return {'ok':False,'duration_ms':duration,'status':status,'error':'invalid_json'}
    return {'ok':status==200,'status':status,'duration_ms':duration,'body':data}


def scenario(i, papers):
    cases=[
      ('dashboard','/api/public/dashboard?topic_id=default'),
      ('recent','/api/public/papers?topic_id=default&limit=30&sort=recent'),
      ('paged','/api/public/papers?topic_id=default&limit=30&offset=630&sort=oldest'),
      ('search','/api/public/papers?topic_id=default&limit=30&q=Neural%20Operator'),
      ('starred','/api/public/papers?topic_id=default&limit=30&filter=starred'),
      ('done','/api/public/papers?topic_id=default&limit=30&filter=done'),
      ('title_sort','/api/public/papers?topic_id=default&limit=30&sort=title'),
      ('paper_detail','/api/public/papers?topic_id=default&paper_id=synthetic%3Av131%3A000011'),
    ]
    # Paged offset is valid only for >= 661 papers.
    if papers < 661:cases=[c for c in cases if c[0]!='paged']
    return cases[i%len(cases)]


def verify_result(name, value, count):
    if not value['ok']:return False,value.get('error') or f'HTTP {value.get("status")}'
    data=value['body']
    n_star=(count-1)//10+1
    n_done=(count-1)//11+1
    if name=='dashboard':
        valid=data.get('papers')==count and data.get('starred')==n_star and data.get('read')==n_done
    else:
        total={'search':(count-1)//4+1,'starred':n_star,'done':n_done,
               'paper_detail':1}.get(name,count)
        valid=data.get('total')==total and len(data.get('papers',[]))==min(
          30, max(0,total-(630 if name=='paged' else 0)))
    return (valid, '' if valid else 'incorrect_result')


def _rss_mb():
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss/1048576,2)
    except ImportError:
        return None


def run_local_workspace_benchmark(*, papers=1000, repetitions=100):
    """Measure actual desktop core SQLite listing/filters and persisted flags."""
    from paper_radar.core import connect,save_papers,list_library,count_library,update_paper_flags
    from paper_radar.models import Paper
    with tempfile.TemporaryDirectory(prefix='paper-radar-v131-local-') as directory:
        path=Path(directory)/'papers.sqlite3'
        opened=perf_counter()
        db=connect(path)
        cold_open_ms=(perf_counter()-opened)*1000
        rows=[Paper(paper_id=f'local:v131:{i:06}',
                    title=f'Synthetic {"Neural Operator" if i%4==0 else "Industrial Forecast"} Study {i:06}',
                    abstract='Synthetic metadata only; paper content is not real.',
                    authors='Benchmark',published=f'2026-{(i%12)+1:02d}-01',updated='',
                    category='cs.LG',url='',pdf_url='',doi=f'10.9999/v131.{i:06}',score=i%20)
             for i in range(papers)]
        seed_start=perf_counter()
        save_papers(db,rows,topic_id='default')
        seed_seconds=perf_counter()-seed_start
        assert count_library(db,topic_id='default',topic_only=True)==papers
        samples=[]; errors=[]; by_case=defaultdict(list)
        for i in range(repetitions):
            name=['recent','relevance','search','count','starred','status'][i%6]
            start=perf_counter()
            try:
                if name=='count':
                    response=count_library(db,topic_id='default',topic_only=True)
                    assert response==papers
                elif name=='search':
                    response=list_library(db,topic_id='default',topic_only=True,query='Neural Operator',limit=40)
                    assert len(response)==min(40,(papers-1)//4+1)
                else:
                    response=list_library(db,topic_id='default',topic_only=True,limit=40,
                        sort=name if name in ('recent','relevance') else 'default',
                        only_starred=name=='starred',reading_status='done' if name=='status' else '')
                    if name in ('recent','relevance'):assert len(response)==min(40,papers)
                duration=(perf_counter()-start)*1000
                samples.append(duration);by_case[name].append(duration)
            except Exception as exc:
                errors.append({'name':name,'error':type(exc).__name__})
        target=rows[0].paper_id
        updated=update_paper_flags(db,target,starred=True,status='done',topic_id='default')
        db.commit();db.close()
        db=connect(path)
        persisted=bool(updated and any(p.paper_id==target and p.is_starred and p.reading_status=='done'
            for p in list_library(db,topic_id='default',topic_only=True,query='Study 000000',limit=3)))
        db.close()
    return {'paper_count':papers,'kind':'isolated_desktop_core_sqlite',
        'cold_open_ms':round(cold_open_ms,2),'seed_seconds':round(seed_seconds,2),
        'requests':repetitions,'completed':repetitions-len(errors),'error_rate':round(len(errors)/repetitions,4),
        'p50_ms':pct(samples,50),'p95_ms':pct(samples,95),'max_ms':round(max(samples),2) if samples else None,
        'queries':{k:{'p95_ms':pct(v,95),'requests':len(v)} for k,v in by_case.items()},
        'persisted_flags_after_reopen':persisted,'errors':errors[:10]}


def run(*,papers=1000,requests=240,concurrency=8,soak_seconds=10,
        p95_budget_ms=1500, enforce=False):
    if requests<8 or not 1<=concurrency<=32:
        raise ValueError('requests must be >=8 and concurrency 1..32')
    start=perf_counter()
    local=run_local_workspace_benchmark(papers=papers,repetitions=max(60,requests//2))
    with workspace_server(papers=papers) as env:
        base,token=env['base'],env['token']
        initial_rss=_rss_mb()
        for i in range(8):
            name,path=scenario(i,papers)
            response_http(base,token,path)
        series=[]
        group=defaultdict(list)
        problems=[]
        def do_one(i):
            name,path=scenario(i,papers)
            result=response_http(base,token,path)
            ok,reason=verify_result(name,result,papers)
            return (name,result['duration_ms'],ok,reason,result['status'])
        bench_start=perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            pending=[pool.submit(do_one,i) for i in range(requests)]
            for future in as_completed(pending):
                name,elapsed,ok,reason,status=future.result()
                series.append(elapsed)
                group[name].append((elapsed,ok))
                if not ok:problems.append({'scenario':name,'status':status,'reason':reason})
        wall_ms=(perf_counter()-bench_start)*1000
        after_load_rss=_rss_mb()
        # A smaller sustained steady-state poll probes leaks/crashes/SQLite lockups.
        soak_n=0; soak_errors=0; soak_samples=[];rss_peak=after_load_rss
        soak_start=perf_counter()
        while perf_counter()-soak_start < soak_seconds:
            name,path=scenario(soak_n,papers)
            result=response_http(base,token,path)
            ok,_=verify_result(name,result,papers)
            soak_n+=1
            soak_errors+=int(not ok)
            soak_samples.append(result['duration_ms'])
            if soak_n%40==0:
                rss=_rss_mb()
                if rss is not None:rss_peak=max(rss_peak or rss,rss)
            sleep(.025)
        last_rss=_rss_mb()
        if last_rss is not None:rss_peak=max(rss_peak or last_rss,last_rss)
    breakdown={name:{'requests':len(rows),'completion_rate':round(sum(1 for _,ok in rows if ok)/len(rows),4),
          'p50_ms':pct([v for v,_ in rows],50),'p95_ms':pct([v for v,_ in rows],95)}
        for name,rows in sorted(group.items())}
    passed=not problems and soak_errors==0 and pct(series,95)<=p95_budget_ms and local['error_rate']==0 and local['persisted_flags_after_reopen'] and (
        after_load_rss is None or last_rss is None or last_rss-after_load_rss<=50.0)
    result={'schema_version':1,'version':__version__,'mode':'isolated_synthetic_http',
        'dataset':{'paper_count':papers,'kind':'deterministic_synthetic_metadata','pdf_count':0},
        'host':{'os':platform.platform(),'python':platform.python_version(),
                'cpu_count':os.cpu_count()},
        'load':{'concurrency':concurrency,'requests':requests,
                'completed':requests-len(problems),'completion_rate':round((requests-len(problems))/requests,4),
                'error_rate':round(len(problems)/requests,4),'elapsed_ms':round(wall_ms,2),
                'throughput_rps':round(requests/(wall_ms/1000),2),
                'p50_ms':pct(series,50),'p95_ms':pct(series,95),'p99_ms':pct(series,99),
                'max_ms':round(max(series),2),'breakdown':breakdown,'failures':problems[:30]},
        'local_workspace':local,
        'stability':{'soak_seconds':soak_seconds,'requests':soak_n,'errors':soak_errors,
            'error_rate':round(soak_errors/max(1,soak_n),4),'p95_ms':pct(soak_samples,95),
            'rss_initial_mb':initial_rss,'rss_after_load_mb':after_load_rss,
            'rss_end_mb':last_rss,'rss_peak_observed_mb':rss_peak},
        'gate':{'enforced':enforce,'p95_budget_ms':p95_budget_ms,
                'passed':passed,'reason':'PASS' if passed else 'error_rate_or_latency_budget'},
        'total_elapsed_seconds':round(perf_counter()-start,2),
        'at':datetime.now(timezone.utc).isoformat(timespec='seconds')}
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--papers',type=int,default=1000)
    p.add_argument('--requests',type=int,default=240)
    p.add_argument('--concurrency',type=int,default=8)
    p.add_argument('--soak-seconds',type=int,default=10)
    p.add_argument('--p95-budget-ms',type=int,default=1500)
    p.add_argument('--enforce',action='store_true')
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args(argv)
    result=run(papers=args.papers,requests=args.requests,concurrency=args.concurrency,
               soak_seconds=args.soak_seconds,p95_budget_ms=args.p95_budget_ms,enforce=args.enforce)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'saved':str(args.out),'load':result['load']['completion_rate'],
          'error_rate':result['load']['error_rate'],'p95_ms':result['load']['p95_ms'],
          'rps':result['load']['throughput_rps'],'stability':result['stability']['error_rate'],
          'gate':result['gate']},ensure_ascii=False))
    return 1 if args.enforce and not result['gate']['passed'] else 0

if __name__=='__main__':raise SystemExit(main())
