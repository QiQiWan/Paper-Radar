# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Isolated standalone worker service for public research jobs.

The Web server does not execute scientific tasks. A worker process owns a
fenced SQLite lease, launches one bounded subprocess per job, renews the lease,
and terminates its child on cancellation, timeout, or lost ownership.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from .portal_jobs import JobCancelled, PortalJobs


def _terminate(proc:subprocess.Popen):
    if proc.poll() is not None:return
    try:
        if os.name=='nt':
            subprocess.run(['taskkill','/T','/F','/PID',str(proc.pid)],
                           capture_output=True,timeout=5,check=False)
        else:os.killpg(proc.pid,signal.SIGKILL)
    except (OSError,subprocess.TimeoutExpired):
        try:proc.kill()
        except OSError:pass
    try:proc.wait(timeout=5)
    except subprocess.TimeoutExpired:pass


def _limits(memory_mb:int,cpu_seconds:int):
    if os.name=='nt':return
    if sys.platform.startswith('linux'):
        # If the supervisor is killed (OOM/SIGKILL), kill its task child too.
        # This complements lease fencing; it does not establish exactly-once IO.
        try:
            import ctypes
            ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0)
            if os.getppid()==1:os.kill(os.getpid(),signal.SIGKILL)
        except (OSError,AttributeError):pass
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(memory_mb*1024*1024,memory_mb*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(cpu_seconds,cpu_seconds+2))
        resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024*1024,64*1024*1024))
        soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE,(min(128,soft),hard))
    except (ImportError,OSError,ValueError):pass


def encode_bounded_result(payload:dict,limit:int=150000)->bytes:
    encoded=json.dumps(payload,ensure_ascii=False,default=str).encode('utf-8')
    if len(encoded)>limit:
        encoded=json.dumps({'state':'failed','error':'Result payload exceeds size limit'}).encode('utf-8')
    return encoded


def execute_child(data_root:Path,job_id:int,token:str,output:Path,*,memory_mb=1536,cpu_seconds=240):
    """Only used by the worker process; output is a private JSON result file."""
    _limits(memory_mb,cpu_seconds)
    from .public_portal import Portal
    from .portal_auth import db as auth_db
    portal=Portal(data_root,embedded_workers=False)
    queue=portal.job_queue
    payload={'state':'failed','error':'Unknown worker error'}
    try:
        if not queue.owns_lease(job_id,token):raise JobCancelled('Lease not owned')
        with auth_db(portal.auth_path) as db:
            row=db.execute('SELECT * FROM portal_jobs WHERE id=? AND lease_token=?',(job_id,token)).fetchone()
        if not row:raise JobCancelled('Job no longer exists')
        cancelled=lambda:not queue.owns_lease(job_id,token)
        if row['kind']=='search':
            queue.update_stage(job_id,'discovery',lease_token=token)
            result=portal.search(row['account_id'],row['topic_id'],cancelled)
        else:
            result=portal.research_job(row['account_id'],row['topic_id'],row['kind'],
                row['paper_id'],json.loads(row['options_json'] or '{}'),cancelled,
                lambda stage:queue.update_stage(job_id,stage,lease_token=token),job_id=job_id)
        if cancelled():raise JobCancelled('Worker lease lost before result commit')
        payload={'state':'success','result':result}
    except (JobCancelled,InterruptedError):payload={'state':'cancelled','error':'Stopped or lease lost'}
    except BaseException as exc:
        payload={'state':'failed','error':f'{type(exc).__name__}: {str(exc)[:180]}'}
    # Atomic bounded output so a crashed process cannot leave a partial result.
    encoded=encode_bounded_result(payload)
    temp=output.with_suffix('.pending')
    with temp.open('wb') as f:
        f.write(encoded);f.flush();os.fsync(f.fileno())
    os.replace(temp,output)
    return 0 if payload['state']=='success' else 1


def child_command(data_root: Path, job_id: int, output: Path, memory_mb: int, cpu_seconds: int) -> list[str]:
    """Spawn a bounded research child in either source or native app mode.

    A PyInstaller executable is not a Python interpreter and cannot accept
    ``-m paper_radar.portal_worker``. The bundled entry point dispatches the
    explicit ``--portal-worker`` switch without opening the desktop GUI.
    """
    prefix = ([sys.executable, '--portal-worker', '_execute']
              if getattr(sys, 'frozen', False)
              else [sys.executable, '-m', 'paper_radar.portal_worker', '_execute'])
    return prefix + ['--data-root', str(data_root), '--job-id', str(job_id),
                     '--output', str(output), '--memory-mb', str(memory_mb),
                     '--cpu-seconds', str(cpu_seconds)]


class IsolatedWorker:
    def __init__(self,data_root:Path,*,lease_seconds=45,heartbeat_seconds=5,
                 job_timeout=480,memory_mb=1536,cpu_seconds=240,poll_seconds=.5,
                 worker_id=None):
        if not 6<=lease_seconds<=3600 or not 1<=heartbeat_seconds<lease_seconds/2:
            raise ValueError('Heartbeat must be shorter than half the lease')
        if not 10<=job_timeout<=7200 or not 256<=memory_mb<=16384:
            raise ValueError('Invalid subprocess resource budget')
        from .public_portal import Portal
        self.portal=Portal(Path(data_root).resolve(),embedded_workers=False)
        self.queue=self.portal.job_queue
        self.queue.lease_seconds=int(lease_seconds)
        self.heartbeat_seconds=float(heartbeat_seconds)
        self.job_timeout=float(job_timeout)
        self.memory_mb=int(memory_mb)
        self.cpu_seconds=int(cpu_seconds)
        self.poll_seconds=float(poll_seconds)
        self.worker_id=worker_id or 'scientific-'+uuid.uuid4().hex
        self._stop=threading.Event()

    def stop(self):self._stop.set()

    def run_one(self):
        self.queue.recover(force=False)
        self.queue.worker_seen(self.worker_id,state='idle')
        job=self.queue._claim(self.worker_id)
        if not job:return False
        jid=job['id'];token=job['lease_token']
        private=self.portal.root/'worker_results'
        private.mkdir(parents=True,exist_ok=True,mode=0o700)
        if private.is_symlink():raise RuntimeError('Unsafe worker results directory')
        run_key=uuid.uuid4().hex
        out=private/('result_'+run_key+'.json')
        err=private/('error_'+run_key+'.log')
        argv=child_command(self.portal.root,jid,out,self.memory_mb,self.cpu_seconds)
        started=time.monotonic();reason=''
        try:
            with err.open('wb') as err_stream:
                flags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0) if os.name=='nt' else 0
                proc=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
                    stderr=err_stream,close_fds=True,start_new_session=os.name!='nt',
                    creationflags=flags,cwd=str(Path(__file__).resolve().parent.parent))
                # Send the secret over a private OS pipe, never in process argv.
                try:
                    proc.stdin.write((token+'\n').encode('ascii'))
                    proc.stdin.flush()
                finally:
                    proc.stdin.close()
                try:
                    while proc.poll() is None:
                        if self._stop.is_set() or self.queue.is_cancelled(jid):
                            reason='已取消';break
                        if time.monotonic()-started>self.job_timeout:
                            reason='任务执行超过时间预算';break
                        if not self.queue.renew(jid,token,worker_id=self.worker_id):
                            reason='租约丢失或取消';break
                        proc.wait(timeout=self.heartbeat_seconds) if proc.poll() is not None else time.sleep(self.heartbeat_seconds)
                    if reason:
                        _terminate(proc)
                        self.queue._finish(jid,'cancelled' if '取消' in reason else 'failed',
                                           error=reason,lease_token=token)
                    else:
                        if out.is_file() and out.stat().st_size<=160000:
                            try:payload=json.loads(out.read_text(encoding='utf-8'))
                            except (ValueError,UnicodeError):payload={'state':'failed','error':'Invalid child output'}
                        else:payload={'state':'failed','error':'Child exited without valid result'}
                        state=payload.get('state','failed')
                        if state not in ('success','failed','cancelled'):state='failed'
                        self.queue._finish(jid,state,result=payload.get('result') if state=='success' else None,
                             error=payload.get('error','') if state!='success' else '',lease_token=token)
                finally:
                    if proc.poll() is None:_terminate(proc)
        except (OSError,subprocess.SubprocessError) as exc:
            self.queue._finish(jid,'failed',error=f'Worker spawn error: {type(exc).__name__}',lease_token=token)
        finally:
            out.unlink(missing_ok=True)
            err.unlink(missing_ok=True)
            self.queue.worker_seen(self.worker_id,state='idle',force=True)
        return True

    def run_forever(self,*,max_jobs=None):
        count=0
        if max_jobs==0:
            self.queue.worker_seen(self.worker_id,state='idle')
            return count
        try:
            while not self._stop.is_set():
                if self.run_one():
                    count+=1
                    if max_jobs is not None and count>=max_jobs:break
                else:
                    self.queue.worker_seen(self.worker_id,state='idle')
                    if max_jobs == 0:break
                    self._stop.wait(self.poll_seconds)
        finally:self.queue.worker_seen(self.worker_id,state='stopping')
        return count


def main(argv=None):
    parser=argparse.ArgumentParser(description='Paper-Radar separate scientific Agent worker')
    parser.add_argument('--data-root',type=Path,default=Path(os.getenv('PAPERRADAR_PORTAL_DATA','storage/portal')))
    parser.add_argument('--lease-seconds',type=int,default=45)
    parser.add_argument('--heartbeat-seconds',type=int,default=5)
    parser.add_argument('--job-timeout',type=int,default=480)
    parser.add_argument('--memory-mb',type=int,default=1536)
    parser.add_argument('--cpu-seconds',type=int,default=240)
    parser.add_argument('--poll-seconds',type=float,default=.5)
    parser.add_argument('--max-jobs',type=int,default=-1,help='-1 means continuous; 0 means idle check only')
    parser.add_argument('command',nargs='?',default='run',choices=['run','_execute'])
    parser.add_argument('--job-id',type=int,default=0)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    if args.command=='_execute':
        # Claim token is passed only by the parent through stdin, never argv.
        token=sys.stdin.buffer.readline(100).strip().decode('ascii',errors='ignore')
        if not args.job_id or len(token)!=48 or any(c not in '0123456789abcdef' for c in token) or args.output is None:return 2
        return execute_child(args.data_root,args.job_id,token,args.output,
                             memory_mb=args.memory_mb,cpu_seconds=args.cpu_seconds)
    w=IsolatedWorker(args.data_root,lease_seconds=args.lease_seconds,
         heartbeat_seconds=args.heartbeat_seconds,job_timeout=args.job_timeout,
         memory_mb=args.memory_mb,cpu_seconds=args.cpu_seconds,poll_seconds=args.poll_seconds)
    try:w.run_forever(max_jobs=None if args.max_jobs<0 else args.max_jobs)
    except KeyboardInterrupt:w.stop()
    return 0

if __name__=='__main__':raise SystemExit(main())
