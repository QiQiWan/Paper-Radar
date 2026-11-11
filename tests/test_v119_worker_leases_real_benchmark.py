"""v1.19 web/worker separation, lease fencing and real-public-paper seed benchmark."""
from __future__ import annotations
from pathlib import Path
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from tempfile import TemporaryDirectory
import pytest

from paper_radar.portal_auth import db as auth_db,create_account
from paper_radar.portal_jobs import PortalJobs
from paper_radar.public_portal import Portal,make_server
from paper_radar.portal_worker import IsolatedWorker,_terminate,encode_bounded_result
from paper_radar.migrations import status
from tools.eval_real_papers import load,evaluate_synthesis,run,evaluate_predictions

PASS='correct length integration testing password'

def account(path,name):
    with auth_db(path) as db:return create_account(db,name,PASS)['id']


def test_schema_v5_preserves_legacy_identity(tmp_path):
    from paper_radar.migrations import REGISTRIES,migrate
    from paper_radar.portal_auth import create_session,authenticate
    p=tmp_path/'accounts.sqlite3'
    import paper_radar.migrations as mod
    old=mod.REGISTRIES['portal_auth']
    try:
        mod.REGISTRIES['portal_auth']=old[:4]
        with sqlite3.connect(p) as db:
            db.row_factory=sqlite3.Row
            migrate(db,'portal_auth')
            uid=create_account(db,'ExistingUser',PASS)['id']
            token,csrf=create_session(db,uid)
    finally:mod.REGISTRIES['portal_auth']=old
    with auth_db(p) as db:
        assert status(db,'portal_auth')['version']==len(REGISTRIES['portal_auth'])
        assert authenticate(db,token)['id']==uid
        assert {'lease_token','lease_until','heartbeat_at','resume_allowed'} <= {
            row[1] for row in db.execute('PRAGMA table_info(portal_jobs)')}


def test_two_claimants_cannot_own_one_task(tmp_path):
    p=tmp_path/'auth.sqlite3';u=account(p,'Alice')
    q1=PortalJobs(p,lambda *_:None);q2=PortalJobs(p,lambda *_:None)
    q1.enqueue(u,'default')
    got=[]
    t1=threading.Thread(target=lambda:got.append(q1._claim('w1')))
    t2=threading.Thread(target=lambda:got.append(q2._claim('w2')))
    t1.start();t2.start();t1.join();t2.join()
    assert sum(x is not None for x in got)==1


def test_healthy_lease_not_recovered_and_stale_completion_rejected(tmp_path):
    p=tmp_path/'auth.sqlite3';u=account(p,'Alice')
    q=PortalJobs(p,lambda *_:None,lease_seconds=30)
    job_id=q.enqueue(u,'default')['id'];job=q._claim('worker-a')
    q.recover()
    assert q.latest(u)['state']=='running'
    assert q.owns_lease(job_id,job['lease_token'])
    assert not q._finish(job_id,'success',{'hacked':True},lease_token='old-worker')
    assert q._finish(job_id,'success',{'ok':True},lease_token=job['lease_token'])
    assert q.latest(u)['result']=={'ok':True}


def test_expired_lease_requeues_safe_search_but_not_paid_analysis(tmp_path):
    p=tmp_path/'auth.sqlite3';u=account(p,'Alice')
    q=PortalJobs(p,lambda *_:None)
    q.enqueue(u,'default',kind='search');a=q._claim('worker-a')
    with auth_db(p) as db:
        db.execute('UPDATE portal_jobs SET lease_until=1 WHERE id=?',(a['id'],))
    q.recover();assert q.latest(u)['state']=='queued'
    assert not q._finish(a['id'],'success',{'wrong':'version'},lease_token=a['lease_token'])
    q.cancel(u)
    q.enqueue(u,'default',kind='analyze',paper_id='abc',options={'cloud_analysis':True})
    b=q._claim('worker-b')
    with auth_db(p) as db:db.execute('UPDATE portal_jobs SET lease_until=1 WHERE id=?',(b['id'],))
    q.recover()
    assert q.latest(u)['state']=='failed'
    assert '人工确认' in q.latest(u)['error']


def test_cancel_prevents_renew_and_acknowledge(tmp_path):
    p=tmp_path/'auth.sqlite3';u=account(p,'Alice')
    q=PortalJobs(p,lambda *_:None)
    jid=q.enqueue(u,'default')['id'];job=q._claim()
    assert q.cancel(u,jid)
    assert not q.renew(jid,job['lease_token'])
    assert q._finish(jid,'success',{},lease_token=job['lease_token'])
    assert q.latest(u)['state']=='cancelled'


def test_web_only_mode_does_not_consume_jobs(tmp_path):
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    uid=account(portal.auth_path,'Alice')
    server=make_server('127.0.0.1',0,portal)
    try:
        portal.research_async(uid,'default',kind='synthesize')
        time.sleep(.08)
        assert portal.job(uid)['state']=='queued'
    finally:server.server_close()


def test_standalone_worker_executes_in_separate_child(tmp_path):
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    uid=account(portal.auth_path,'Alice')
    portal.research_async(uid,'default',kind='synthesize')
    worker=IsolatedWorker(portal.root,lease_seconds=10,heartbeat_seconds=1,job_timeout=20)
    assert worker.run_one()
    row=portal.job(uid)
    assert row['state']=='success',row
    assert row['result']['kind']=='synthesize'
    assert row['attempts']==1
    assert worker.queue.stats()['live_workers']>=1
    assert 'lease_token' not in json.dumps(row)


def test_workers_share_durable_queue_without_double_claim(tmp_path):
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    a=account(portal.auth_path,'Alice');b=account(portal.auth_path,'Bob')
    portal.research_async(a,'default',kind='synthesize')
    portal.research_async(b,'default',kind='synthesize')
    workers=[IsolatedWorker(portal.root,lease_seconds=10,heartbeat_seconds=1,job_timeout=20) for _ in range(2)]
    done=[]
    def consume(w):done.append(w.run_one())
    threads=[threading.Thread(target=consume,args=(w,)) for w in workers]
    for t in threads:t.start()
    for t in threads:t.join(timeout=30)
    assert all(not t.is_alive() for t in threads)
    assert done==[True,True]
    assert portal.job(a)['state']=='success' and portal.job(b)['state']=='success'


def test_force_kill_subprocess_group(tmp_path):
    flags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0) if os.name=='nt' else 0
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],
                           start_new_session=os.name!='nt',creationflags=flags)
    try:
        _terminate(child)
        assert child.poll() is not None
    finally:
        if child.poll() is None:child.kill()


def test_real_paper_gold_is_provenanced_and_no_false_crosspaper_comparison():
    manifest,gold=load()
    assert len(manifest['papers'])==3 and len(gold['fields'])==6
    assert {x['paper_id'] for x in manifest['papers']}=={'2010.08895','2109.03697','2111.03794'}
    _,r=evaluate_synthesis(manifest,gold)
    assert r['unsafe_candidates']==0 and r['pairwise_cases']==5
    assert r['positive_pairs']==0  # Cannot claim positive-pair recall.


def test_real_pdf_evaluation_does_not_fake_results_when_offline(tmp_path):
    result=run(tmp_path/'empty_pdfs',None,tmp_path/'eval')
    assert result['pdf_extraction']['page_anchor_recall'] is None
    assert result['pdf_extraction']['measured_on_local_real_pdf'] is False
    assert result['model_extraction']['measured'] is False
    assert (tmp_path/'eval'/'real_paper_eval.md').is_file()


def test_scoring_model_predictions_reports_false_claims(tmp_path):
    _,gold=load()
    predictions=[dict(gold['fields'][0]),dict(gold['fields'][1])]
    predictions.append({'paper_id':'2010.08895','page_index':7,'metric':'error','value':'99.999'})
    path=tmp_path/'predictions.json';path.write_text(json.dumps(predictions))
    r=evaluate_predictions(path,gold)
    assert r['true_positive']==2 and r['prediction_count']==3
    assert r['precision']==round(2/3,4) and r['recall']==round(2/6,4)


def test_worker_deployment_scripts_separate_http_and_science():
    root=Path(__file__).resolve().parents[1]
    launcher=(root/'paper_radar/cli_launcher.py').read_text(encoding='utf-8')
    assert 'paper_radar.public_portal' in launcher
    assert 'paper_radar.portal_worker' in launcher
    assert '--public' in launcher and '--worker' in launcher
    assert '--data-root' in launcher and '--port' in launcher
    assert (root/'deploy/paperradar-worker@.service').is_file()

def test_bounded_worker_output_is_always_valid_json():
    small={'state':'success','result':{'name':'ok'}}
    assert json.loads(encode_bounded_result(small))==small
    huge={'state':'success','result':{'text':'large'*40000}}
    failure=json.loads(encode_bounded_result(huge))
    assert failure['state']=='failed' and 'limit' in failure['error']


def test_stale_or_giant_result_never_corrupts_job_json(tmp_path):
    p=tmp_path/'auth.sqlite3';u=account(p,'Alice')
    q=PortalJobs(p,lambda *_:None)
    jid=q.enqueue(u,'default')['id'];job=q._claim('my-worker')
    assert q._finish(jid,'success',{'result':'x'*200000},lease_token=job['lease_token'])
    result=q.latest(u)
    assert result['state']=='failed' and result['result'] is None
    assert 'limit' in result['error']


def test_worker_secret_not_in_process_arguments():
    import inspect
    source=inspect.getsource(IsolatedWorker.run_one)
    assert 'stdin=subprocess.PIPE' in source
    assert "'--token',token" not in source
    assert "'result_'+token" not in source


def test_real_synthesis_writes_honestly_labeled_evidence_report(tmp_path):
    result=run(tmp_path/'no_pdf',None,tmp_path/'report')
    report=(tmp_path/'report'/'real_paper_synthesis.md').read_text(encoding='utf-8')
    assert 'FNO' in report or 'Fourier Neural Operator' in report
    assert '用户导入' in report
    assert result['synthesis_report_structure']['gold_rows_retained']==6
    assert result['synthesis_report_structure']['gold_rows_with_source_page_and_quote']==6


def test_zero_max_jobs_is_diagnostic_only(tmp_path):
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    u=account(portal.auth_path,'Alice')
    portal.research_async(u,'default',kind='synthesize')
    w=IsolatedWorker(portal.root,lease_seconds=10,heartbeat_seconds=1)
    assert w.run_forever(max_jobs=0)==0
    assert portal.job(u)['state']=='queued'
