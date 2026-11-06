# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Durable portal queue with leased, fenced claims.

A web process only enqueues/queries tasks. Separate worker processes may claim
jobs using SQLite BEGIN IMMEDIATE, renew ownership, and acknowledge completion
with a random lease token. Expired jobs are retried only if marked idempotent.
SQLite leases give *at-least-once* recovery, not exactly-once side effects.
The optional embedded worker is retained only for desktop/dev compatibility.
"""
from __future__ import annotations
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Callable
from .portal_auth import db as auth_db, throttle
from .portal_evidence_retrieval import CLOUD_CALL_DAILY_LIMIT

class QueueError(ValueError):pass
class JobCancelled(RuntimeError):pass

class PortalJobs:
    def __init__(self, db_path: Path, worker: Callable, *, max_workers=2,
                 max_pending=100, daily_limit=10, poll_seconds=.25,
                 special_worker=None, lease_seconds=90):
        self.path=Path(db_path)
        self.worker=worker
        self.special_worker=special_worker
        self.max_workers=max(1,min(4,int(max_workers)))
        self.max_pending=max(1,int(max_pending))
        self.daily_limit=max(1,int(daily_limit))
        self.poll_seconds=max(.05,float(poll_seconds))
        self.lease_seconds=max(6,int(lease_seconds))
        self.instance=uuid.uuid4().hex
        self._stop=threading.Event()
        self._wake=threading.Event()
        self._threads=[]
        self._last_auto_recovery=0.0
        self._worker_seen_cache={}
        with auth_db(self.path):pass

    def enqueue(self, account_id:str, topic_id:str, *, kind='search',paper_id='',options=None)->dict:
        if kind not in ('search','research','analyze','synthesize'):
            raise QueueError('Unsupported task type')
        if kind=='analyze' and not paper_id:
            raise QueueError('Paper ID required')
        options=options or {}
        if not isinstance(options,dict) or any(k not in ('cloud_analysis','fetch_pdf') or type(v) is not bool for k,v in options.items()):
            raise QueueError('Invalid task options')
        # Uncertain paid analyses and download-containing research tasks are NOT
        # automatically replayed after a crash (they require manual review).
        replay=int(kind in ('search','synthesize') or (kind=='analyze' and not options.get('cloud_analysis',False)))
        t=int(time.time())
        with auth_db(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("DELETE FROM portal_jobs WHERE state IN ('success','failed','cancelled') AND finished_at<?",(t-90*86400,))
            db.execute('DELETE FROM portal_job_events WHERE job_id NOT IN (SELECT id FROM portal_jobs)')
            if db.execute("SELECT 1 FROM portal_jobs WHERE account_id=? AND state IN ('queued','running')",(account_id,)).fetchone():
                raise QueueError('已有正在等待或执行的检索，请等待完成或取消后再提交')
            if db.execute("SELECT COUNT(*) FROM portal_jobs WHERE state IN ('queued','running')").fetchone()[0]>=self.max_pending:
                raise QueueError('任务队列已满，请稍后重试')
            # Search and full research share the source quota. Re-reading an
            # uploaded PDF or generating a report must not exhaust searches.
            quota_class = 'search' if kind in ('search','research') else kind
            quota_limit = (self.daily_limit if quota_class == 'search' else
                           max(12,self.daily_limit*3) if quota_class == 'analyze'
                           else max(30,self.daily_limit*6))
            if not throttle(db,quota_class+':'+account_id,limit=quota_limit,seconds=86400):
                raise QueueError('已达到过去 24 小时的'+{
                    'search':'文献检索','analyze':'单篇分析','synthesize':'报告生成'}[quota_class]+'配额')
            cur=db.execute('''INSERT INTO portal_jobs
                (account_id,topic_id,state,created_at,max_attempts,kind,paper_id,options_json,resume_allowed)
                VALUES (?,?,'queued',?,2,?,?,?,?)''',
                (account_id,topic_id,t,kind,paper_id,json.dumps(options),replay))
            job_id=cur.lastrowid
        self._wake.set()
        return {'id':job_id,'state':'queued','topic_id':topic_id,'kind':kind,'created_at':t}

    @staticmethod
    def _safe_retry(row) -> bool:
        if not row or row['state'] not in ('failed','cancelled'):
            return False
        if row['kind'] in ('search','synthesize'):
            return True
        if row['kind']=='analyze':
            try:options=json.loads(row['options_json'] or '{}')
            except (ValueError,TypeError):return False
            # 'analyze' operates on an already available PDF; its fetch_pdf
            # option is ignored by the execution branch.
            return not options.get('cloud_analysis',False)
        return False

    def retry_safe(self,account_id:str,job_id:int)->dict:
        """Explicit, quota-accounted retry of only non-billable failed work.

        A full research task can fetch PDF and incur model charges; it must
        always be resubmitted via the visible research options instead.
        """
        with auth_db(self.path) as db:
            row=db.execute('''SELECT id,account_id,topic_id,kind,paper_id,options_json,state
                FROM portal_jobs WHERE id=? AND account_id=?''',(job_id,account_id)).fetchone()
        if not row:raise QueueError('任务不存在或无权访问')
        if not self._safe_retry(row):
            raise QueueError('此任务需人工确认选项后重新提交，禁止自动重复收费分析')
        return self.enqueue(account_id,row['topic_id'],kind=row['kind'],paper_id=row['paper_id'],
                            options=json.loads(row['options_json'] or '{}'))

    def quota_status(self,account_id:str)->dict:
        """Separate 24 h task class limits; never report global provider quotas."""
        t=int(time.time()); classes={
            'search':self.daily_limit,
            'analyze':max(12,self.daily_limit*3),
            'synthesize':max(30,self.daily_limit*6),
            'model_calls':CLOUD_CALL_DAILY_LIMIT}
        with auth_db(self.path) as db:
            rows=db.execute("""SELECT identifier,COUNT(*) n FROM auth_attempts
                WHERE occurred_at>=? AND identifier IN (?,?,?,?) GROUP BY identifier""",
                (t-86400,*[(('public_llm' if c=='model_calls' else c)+':'+account_id) for c in classes])).fetchall()
        usage={r['identifier'].split(':',1)[0]:r['n'] for r in rows}
        return {c:{'limit':lim,
                   'used':usage.get('public_llm' if c=='model_calls' else c,0),
                   'remaining':max(0,lim-usage.get('public_llm' if c=='model_calls' else c,0))}
                for c,lim in classes.items()}

    def recover(self,*,force=True)->None:
        """Reconcile expired claims; background pollers avoid SQLite write churn."""
        if not force and time.monotonic()-self._last_auto_recovery<5:
            return
        t=int(time.time())
        with auth_db(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            expired=db.execute('''SELECT id,stage,state,attempts,max_attempts,resume_allowed,cancel_requested
                FROM portal_jobs WHERE state='running' AND lease_until<=?''',(t,)).fetchall()
            db.execute('''UPDATE portal_jobs SET state='cancelled',finished_at=?,error='已取消',
                lease_token='',lease_until=0,worker_id=NULL
                WHERE state IN ('running','queued') AND cancel_requested=1
                AND (state='queued' OR lease_until<=?)''',(t,t))
            db.execute('''UPDATE portal_jobs SET state='failed',finished_at=?,
                error=CASE WHEN resume_allowed=0 THEN '工作进程中断；可能已发生外部写入或计费，请人工确认后重新提交'
                  ELSE '中断恢复次数已达到上限' END,
                worker_id=NULL,lease_token='',lease_until=0
                WHERE state='running' AND lease_until<=?
                AND (attempts>=max_attempts OR resume_allowed=0)''',(t,t))
            db.execute('''UPDATE portal_jobs SET state='queued',started_at=NULL,
                worker_id=NULL,lease_token='',lease_until=0,heartbeat_at=0,
                error='执行租约过期，等待重试'
                WHERE state='running' AND lease_until<=? AND attempts<max_attempts
                AND resume_allowed=1 AND cancel_requested=0''',(t,))
            for job in expired:
                status='cancelled' if job['cancel_requested'] else (
                    'failed' if job['attempts']>=job['max_attempts'] or not job['resume_allowed'] else 'cancelled')
                if status=='cancelled' and not job['cancel_requested']:
                    # A lost lease may retry; record attempt ended, not task cancellation.
                    status='failed'
                db.execute('INSERT INTO portal_job_events(job_id,stage,status,recorded_at) VALUES (?,?,?,?)',
                           (job['id'],job['stage'] or 'starting',status,t))
            db.execute('DELETE FROM portal_workers WHERE last_seen<?',(t-7*86400,))
        self._last_auto_recovery=time.monotonic()

    def _claim(self,worker_id=None):
        owner=worker_id or self.instance
        with auth_db(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('''SELECT j.id,j.account_id,j.topic_id,j.kind,j.paper_id,j.options_json
                FROM portal_jobs j JOIN accounts a ON a.id=j.account_id AND a.is_active=1
                WHERE j.state='queued' ORDER BY j.id LIMIT 1''').fetchone()
            if not row:return None
            token=secrets.token_hex(24)
            t=int(time.time())
            changed=db.execute('''UPDATE portal_jobs SET state='running',attempts=attempts+1,
                started_at=?,stage='starting',error='',worker_id=?,lease_token=?,lease_until=?,heartbeat_at=?
                WHERE id=? AND state='queued' ''',(t,owner,token,t+self.lease_seconds,t,row['id'])).rowcount
            if changed!=1:return None
            db.execute('INSERT INTO portal_job_events(job_id,stage,status,recorded_at) VALUES (?,?,?,?)',
                       (row['id'],'starting','started',t))
            return {**dict(row),'lease_token':token,'worker_id':owner}

    def owns_lease(self,job_id:int,token:str)->bool:
        if not token:return False
        with auth_db(self.path) as db:
            row=db.execute('''SELECT 1 FROM portal_jobs WHERE id=? AND lease_token=? AND
                state='running' AND lease_until>? AND cancel_requested=0''',
                (job_id,token,int(time.time()))).fetchone()
            return bool(row)

    def renew(self,job_id:int,token:str,*,worker_id:str|None=None)->bool:
        if not token:return False
        t=int(time.time())
        with auth_db(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            cur=db.execute('''UPDATE portal_jobs SET lease_until=?,heartbeat_at=?
                WHERE id=? AND lease_token=? AND state='running' AND lease_until>?
                AND cancel_requested=0''',(t+self.lease_seconds,t,job_id,token,t))
            if cur.rowcount and worker_id:
                db.execute('''INSERT INTO portal_workers(id,last_seen,state,active_job,process_id)
                    VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,
                    state=excluded.state,active_job=excluded.active_job,process_id=excluded.process_id''',
                    (worker_id,t,'running',job_id,os.getpid()))
            return bool(cur.rowcount)

    def worker_seen(self,worker_id:str,*,active_job=0,state='idle',force=False):
        if state not in ('idle','running','stopping'):raise ValueError('Invalid worker state')
        t=int(time.time())
        previous=self._worker_seen_cache.get(worker_id)
        if not force and previous and previous[:2]==(state,int(active_job)) and t-previous[2]<10:
            return
        with auth_db(self.path) as db:
            db.execute('''INSERT INTO portal_workers(id,last_seen,state,active_job,process_id)
                VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,
                state=excluded.state,active_job=excluded.active_job,process_id=excluded.process_id''',
                (worker_id,t,state,int(active_job),os.getpid()))
        self._worker_seen_cache[worker_id]=(state,int(active_job),t)

    def is_cancelled(self,job_id:int)->bool:
        with auth_db(self.path) as db:
            row=db.execute('SELECT cancel_requested FROM portal_jobs WHERE id=?',(job_id,)).fetchone()
            return bool(row and row[0])

    def _finish(self,job_id:int,state:str,result=None,error='',*,lease_token:str='')->bool:
        if state not in ('success','failed','cancelled'):raise ValueError('Invalid completion')
        if not lease_token:return False
        with auth_db(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('''SELECT cancel_requested,state,lease_token,lease_until FROM portal_jobs
                WHERE id=?''',(job_id,)).fetchone()
            if not row or row['state']!='running' or row['lease_token']!=lease_token or row['lease_until']<=int(time.time()):
                return False
            if row['cancel_requested']:
                state='cancelled';result=None;error='已取消'
            # Reject oversized results rather than truncating JSON and corrupting history.
            encoded=json.dumps(result,ensure_ascii=False,default=str) if result is not None else None
            if encoded is not None and len(encoded.encode('utf-8'))>100000:
                state='failed';error='Task result exceeds 100 kB limit';encoded=None
            # No output from an old claimant can overwrite a newer result.
            now=int(time.time())
            stage=db.execute('SELECT stage FROM portal_jobs WHERE id=?',(job_id,)).fetchone()['stage'] or 'starting'
            changed=db.execute('''UPDATE portal_jobs SET state=?,result_json=?,error=?,
                finished_at=?,worker_id=NULL,lease_token='',lease_until=0,heartbeat_at=0
                WHERE id=? AND lease_token=? AND state='running' ''',
                (state,encoded,str(error)[:300],now,job_id,lease_token)).rowcount
            if changed:
                final_status={'success':'completed','failed':'failed','cancelled':'cancelled'}[state]
                metrics=self._public_metrics(result if state=='success' else None)
                db.execute('INSERT INTO portal_job_events(job_id,stage,status,recorded_at,metrics_json) VALUES (?,?,?,?,?)',
                           (job_id,stage,final_status,now,json.dumps(metrics)))
                db.execute('INSERT INTO portal_job_events(job_id,stage,status,recorded_at) VALUES (?,?,?,?)',
                           (job_id,'task',final_status,now))
            return bool(changed)

    @staticmethod
    def _public_metrics(result):
        """Expose only bounded numeric counters, never user PDFs or prompts."""
        if not isinstance(result,dict):return {}
        counters={}
        for k in ('retrieved','new','analyzed','finding_count','paper_count','comparison_count'):
            if type(result.get(k)) is int and 0<=result[k]<=100000:
                counters[k]=result[k]
        if isinstance(result.get('discovery'),dict):
            for k in ('retrieved','new'):
                value=result['discovery'].get(k)
                if type(value) is int and 0<=value<=100000:counters[k]=value
        if isinstance(result.get('synthesis'),dict):
            for k in ('finding_count','paper_count','comparison_count'):
                value=result['synthesis'].get(k)
                if type(value) is int and 0<=value<=100000:counters[k]=value
        return counters

    def update_stage(self,job_id:int,stage:str,*,lease_token:str='',metrics=None)->bool:
        if stage not in ('discovery','download','fulltext','evidence','synthesis'):
            raise ValueError('Invalid stage')
        # Stage events are fenced by the same lease as the job result.
        with auth_db(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT stage,state,lease_token,lease_until FROM portal_jobs WHERE id=?',(job_id,)).fetchone()
            if not row or row['state']!='running':return False
            if lease_token and (row['lease_token']!=lease_token or row['lease_until']<=int(time.time())):return False
            now=int(time.time())
            old=row['stage'] or 'starting'
            if old!=stage:
                db.execute('INSERT INTO portal_job_events(job_id,stage,status,recorded_at) VALUES (?,?,?,?)',
                           (job_id,old,'completed',now))
                db.execute('UPDATE portal_jobs SET stage=? WHERE id=?',(stage,job_id))
                db.execute('INSERT INTO portal_job_events(job_id,stage,status,recorded_at) VALUES (?,?,?,?)',
                           (job_id,stage,'started',now))
            if metrics:
                sanitized={k:v for k,v in metrics.items() if k in ('retrieved','new','analyzed','finding_count')
                           and type(v) is int and 0<=v<=100000}
                if sanitized:
                    db.execute('UPDATE portal_jobs SET progress_json=? WHERE id=?',(json.dumps(sanitized),job_id))
            return True

    def events(self,account_id:str,job_id:int,*,limit=100)->list[dict]:
        with auth_db(self.path) as db:
            rows=db.execute("""SELECT e.stage,e.status,e.recorded_at,e.metrics_json
                FROM portal_job_events e JOIN portal_jobs j ON j.id=e.job_id
                WHERE j.account_id=? AND j.id=? ORDER BY e.id ASC LIMIT ?""",
                (account_id,int(job_id),max(1,min(100,int(limit))))).fetchall()
        result=[]
        for row in rows:
            record=dict(row)
            try:record['metrics']=json.loads(record.pop('metrics_json'))
            except (ValueError,TypeError):record['metrics']={}
            result.append(record)
        return result

    def detail(self,account_id:str,job_id:int)->dict|None:
        with auth_db(self.path) as db:
            row=db.execute('SELECT * FROM portal_jobs WHERE id=? AND account_id=?',(job_id,account_id)).fetchone()
        if not row:return None
        data=self._format(row)
        data['events']=self.events(account_id,job_id)
        return data

    def _execute_embedded(self,job):
        jid=job['id']; token=job['lease_token']
        cancelled=lambda:self._stop.is_set() or not self.owns_lease(jid,token)
        try:
            if cancelled():raise JobCancelled('Cancelled')
            if job['kind']=='search':
                self.update_stage(jid,'discovery',lease_token=token)
                res=self.worker(job['account_id'],job['topic_id'],cancelled)
            elif self.special_worker:
                res=self.special_worker(job['account_id'],job['topic_id'],job['kind'],job['paper_id'],
                    json.loads(job['options_json'] or '{}'),cancelled,
                    lambda stage:self.update_stage(jid,stage,lease_token=token),job_id=jid)
            else:raise QueueError('No research task handler')
        except JobCancelled:self._finish(jid,'cancelled',error='已取消',lease_token=token)
        except Exception as exc:self._finish(jid,'failed',error=str(exc),lease_token=token)
        else:self._finish(jid,'success',result=res,lease_token=token)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.recover(force=False)
                job=self._claim()
                if job:self._execute_embedded(job);continue
            except (sqlite3.Error,OSError):pass
            self._wake.wait(self.poll_seconds);self._wake.clear()

    def start(self):
        """Legacy in-process execution; production HTTP server must disable it."""
        if self._threads:return
        self.recover();self._stop.clear()
        for i in range(self.max_workers):
            th=threading.Thread(target=self._loop,name=f'portal-embedded-{i+1}',daemon=True)
            self._threads.append(th);th.start()
        self._wake.set()

    def stop(self,*,timeout=3.0):
        self._stop.set();self._wake.set()
        for th in self._threads:th.join(timeout)
        self._threads.clear()

    def cancel(self,account_id:str,job_id:int|None=None)->bool:
        with auth_db(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            if job_id is None:
                row=db.execute("SELECT id,state FROM portal_jobs WHERE account_id=? AND state IN ('queued','running') ORDER BY id DESC LIMIT 1",(account_id,)).fetchone()
            else:
                row=db.execute("SELECT id,state FROM portal_jobs WHERE id=? AND account_id=? AND state IN ('queued','running')",(job_id,account_id)).fetchone()
            if not row:return False
            if row['state']=='queued':
                now=int(time.time())
                db.execute("UPDATE portal_jobs SET state='cancelled',cancel_requested=1,error='已取消',finished_at=? WHERE id=?",(now,row['id']))
                db.execute('INSERT INTO portal_job_events(job_id,stage,status,recorded_at) VALUES (?,?,?,?)',
                           (row['id'],'task','cancelled',now))
            else:db.execute('UPDATE portal_jobs SET cancel_requested=1 WHERE id=?',(row['id'],))
        self._wake.set();return True

    def cancel_account(self,account_id):
        with auth_db(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''UPDATE portal_jobs SET cancel_requested=1,
                state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,
                finished_at=CASE WHEN state='queued' THEN ? ELSE finished_at END
                WHERE account_id=? AND state IN ('queued','running')''',(int(time.time()),account_id))

    def stats(self)->dict:
        t=int(time.time())
        with auth_db(self.path) as db:
            counts={r['state']:r['n'] for r in db.execute('SELECT state,COUNT(*) n FROM portal_jobs GROUP BY state')}
            last=db.execute('''SELECT COUNT(*) total,
                SUM(CASE WHEN state='success' THEN 1 ELSE 0 END) successful,
                SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END) failed
                FROM portal_jobs WHERE created_at>=?''',(t-86400,)).fetchone()
            live=db.execute('SELECT COUNT(*) FROM portal_workers WHERE last_seen>=? AND state<>"stopping"',(t-60,)).fetchone()[0]
            expired=db.execute("SELECT COUNT(*) FROM portal_jobs WHERE state='running' AND lease_until<=?",(t,)).fetchone()[0]
            oldest=db.execute("SELECT MIN(created_at) FROM portal_jobs WHERE state='queued'").fetchone()[0]
        return {'states':{name:counts.get(name,0) for name in ('queued','running','success','failed','cancelled')},
                'last_24h':{'total':last['total'] or 0,'successful':last['successful'] or 0,'failed':last['failed'] or 0},
                'workers':self.max_workers,'live_workers':live,'expired_leases':expired,
                'oldest_queued_seconds':max(0,t-oldest) if oldest else 0}

    def _format(self,row):
        if not row:return {'state':'idle'}
        record_retryable=self._safe_retry(row)
        record=dict(row)
        raw=record.pop('result_json',None)
        for private in ('options_json','lease_token','lease_until','worker_id','heartbeat_at'):
            record.pop(private,None)
        try:record['result']=json.loads(raw) if raw else None
        except (ValueError,TypeError):record['result']=None
        record['started_at']=record.get('started_at') or None
        record['finished_at']=record.get('finished_at') or None
        record['retryable']=record_retryable
        if record['state']=='queued':
            with auth_db(self.path) as db:
                record['queue_position']=db.execute("SELECT COUNT(*) FROM portal_jobs WHERE state='queued' AND id<=?",(record['id'],)).fetchone()[0]
        return record

    def latest(self,account_id):
        with auth_db(self.path) as db:
            row=db.execute('SELECT * FROM portal_jobs WHERE account_id=? ORDER BY id DESC LIMIT 1',(account_id,)).fetchone()
        data=self._format(row)
        data['events']=self.events(account_id,row['id']) if row else []
        return data

    def history(self,account_id,*,limit=10):
        limit=max(1,min(30,int(limit)))
        with auth_db(self.path) as db:
            rows=db.execute('SELECT * FROM portal_jobs WHERE account_id=? ORDER BY id DESC LIMIT ?',(account_id,limit)).fetchall()
        return [self._format(row) for row in rows]
