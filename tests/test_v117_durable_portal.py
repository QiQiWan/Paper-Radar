"""v1.17.0 persistent portal work, quota, isolation and synthesis tests."""
from __future__ import annotations

import http.client
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from paper_radar.portal_auth import db as auth_db, create_account, throttle
from paper_radar.portal_jobs import PortalJobs, JobCancelled, QueueError
from paper_radar.public_portal import Portal, make_server
from paper_radar.synthesis import compare_records, to_markdown
from paper_radar.migrations import migrate, status

SECRET = 'safely-sized test passphrase for a new account'


def user(path: Path, name: str) -> str:
    with auth_db(path) as db:
        return create_account(db,name,SECRET)['id']


def wait_for(fn, timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        result=fn()
        if result:return result
        time.sleep(.025)
    raise AssertionError('Async operation did not finish')


def test_portal_migration_v3_contains_durable_jobs(tmp_path):
    path=tmp_path/'accounts.sqlite3'
    with auth_db(path) as db:
        assert status(db,'portal_auth')['version']>=3
        cols={r[1] for r in db.execute('PRAGMA table_info(portal_jobs)')}
        assert {'account_id','topic_id','cancel_requested','attempts','result_json'}<=cols


def test_upgrade_existing_v116_accounts_preserves_users_and_sessions(tmp_path,monkeypatch):
    from paper_radar import migrations
    from paper_radar.portal_auth import create_session, authenticate, check_login
    path=tmp_path/'old_accounts.sqlite3'
    original=migrations.REGISTRIES['portal_auth']
    with monkeypatch.context() as m:
        m.setitem(migrations.REGISTRIES,'portal_auth',original[:2])
        with sqlite3.connect(path) as db:
            db.row_factory=sqlite3.Row
            assert migrate(db,'portal_auth')==2
            uid=create_account(db,'AliceResearch',SECRET)['id']
            token,csrf=create_session(db,uid)
            db.commit()
    with auth_db(path) as db:
        assert status(db,'portal_auth')['version']>=3
        assert authenticate(db,token)['id']==uid
        assert check_login(db,'AliceResearch',SECRET)['id']==uid
        assert db.execute('SELECT COUNT(*) FROM portal_jobs').fetchone()[0]==0


def test_queue_aggregate_stats_exclude_account_details(tmp_path):
    path=tmp_path/'auth.sqlite3';uid=user(path,'AliceResearch')
    queue=PortalJobs(path,lambda *_:None)
    queue.enqueue(uid,'default')
    stats=queue.stats()
    assert stats['states']['queued']==1 and stats['workers']==2
    assert stats['last_24h']['total']==1
    assert uid not in json.dumps(stats)


def test_queue_enqueue_is_durable_without_workers(tmp_path):
    path=tmp_path/'auth.sqlite3'; uid=user(path,'AliceResearch')
    jobs=PortalJobs(path,lambda *_:None)
    first=jobs.enqueue(uid,'default')
    assert first['state']=='queued'
    another=PortalJobs(path,lambda *_:None)
    assert another.latest(uid)['id']==first['id']
    assert another.latest(uid)['state']=='queued'
    assert another.latest(uid)['queue_position']==1


def test_only_one_active_job_per_account(tmp_path):
    path=tmp_path/'auth.sqlite3'; uid=user(path,'AliceResearch')
    jobs=PortalJobs(path,lambda *_:None)
    jobs.enqueue(uid,'default')
    with pytest.raises(QueueError,match='等待或执行'):
        jobs.enqueue(uid,'second')


def test_rejected_queue_request_does_not_charge_quota(tmp_path):
    path=tmp_path/'auth.sqlite3'; uid=user(path,'AliceResearch')
    jobs=PortalJobs(path,lambda *_:None,daily_limit=2)
    jobs.enqueue(uid,'default')
    with pytest.raises(QueueError):jobs.enqueue(uid,'second')
    assert jobs.cancel(uid)
    assert jobs.enqueue(uid,'second')['state']=='queued'
    jobs.cancel(uid)
    with pytest.raises(QueueError,match='24 小时'):
        jobs.enqueue(uid,'third')


def test_daily_throttle_retains_events_more_than_one_hour_old(tmp_path):
    path=tmp_path/'auth.sqlite3'; user(path,'AliceResearch')
    with auth_db(path) as db:
        db.execute('INSERT INTO auth_attempts(identifier,occurred_at) VALUES (?,?)',
                   ('search:test',int(time.time())-7200))
        assert not throttle(db,'search:test',limit=1,seconds=86400)
        assert throttle(db,'search:test',limit=1,seconds=3600)


def test_global_pending_queue_limit(tmp_path):
    path=tmp_path/'auth.sqlite3'
    a=user(path,'AliceResearch');b=user(path,'BobResearch')
    jobs=PortalJobs(path,lambda *_:None,max_pending=1)
    jobs.enqueue(a,'default')
    with pytest.raises(QueueError,match='队列已满'):
        jobs.enqueue(b,'default')


def test_cancel_only_own_job_and_preserve_history(tmp_path):
    path=tmp_path/'auth.sqlite3';a=user(path,'AliceResearch');b=user(path,'BobResearch')
    jobs=PortalJobs(path,lambda *_:None)
    j=jobs.enqueue(a,'default')
    assert not jobs.cancel(b,j['id'])
    assert jobs.cancel(a,j['id'])
    assert jobs.latest(a)['state']=='cancelled'
    assert jobs.latest(b)['state']=='idle'
    assert jobs.history(a)[0]['id']==j['id']


def test_worker_completes_with_result_and_attempt(tmp_path):
    path=tmp_path/'auth.sqlite3'; uid=user(path,'AliceResearch')
    jobs=PortalJobs(path,lambda u,t,stop:{'retrieved':3,'new':2,'topic_id':t},poll_seconds=.06)
    jobs.enqueue(uid,'default');jobs.start()
    try:
        result=wait_for(lambda:jobs.latest(uid)['state']=='success' and jobs.latest(uid))
        assert result['attempts']==1
        assert result['result']['new']==2
    finally:jobs.stop()


def test_worker_failure_keeps_error_and_does_not_autoretry_paid_side_effect(tmp_path):
    path=tmp_path/'auth.sqlite3'; uid=user(path,'AliceResearch')
    calls=[]
    def fail(*args):
        calls.append(1)
        raise RuntimeError('Source unavailable')
    jobs=PortalJobs(path,fail,poll_seconds=.06)
    jobs.enqueue(uid,'default');jobs.start()
    try:
        result=wait_for(lambda:jobs.latest(uid)['state']=='failed' and jobs.latest(uid))
        assert result['attempts']==1 and len(calls)==1
        assert 'Source unavailable' in result['error']
    finally:jobs.stop()


def test_worker_running_can_be_cancelled_cooperatively(tmp_path):
    path=tmp_path/'auth.sqlite3';uid=user(path,'AliceResearch')
    entering=threading.Event();proceed=threading.Event()
    def work(uid,topic,cancelled):
        entering.set()
        assert proceed.wait(3)
        if cancelled():raise JobCancelled()
        return {'new':1}
    jobs=PortalJobs(path,work,poll_seconds=.05)
    jid=jobs.enqueue(uid,'default')['id'];jobs.start()
    try:
        assert entering.wait(3)
        assert jobs.cancel(uid,jid)
        proceed.set()
        record=wait_for(lambda:jobs.latest(uid)['state']=='cancelled' and jobs.latest(uid))
        assert record['result'] is None
    finally:proceed.set();jobs.stop()


def test_restart_requeues_running_bounded_attempts(tmp_path):
    path=tmp_path/'auth.sqlite3';uid=user(path,'AliceResearch')
    queue=PortalJobs(path,lambda *_:None)
    jid=queue.enqueue(uid,'default')['id']
    with auth_db(path) as db:
        db.execute("UPDATE portal_jobs SET state='running',attempts=1 WHERE id=?",(jid,))
    restarted=PortalJobs(path,lambda u,t,c:{'new':1},poll_seconds=.05)
    restarted.start()
    try:
        record=wait_for(lambda:restarted.latest(uid)['state']=='success' and restarted.latest(uid))
        assert record['attempts']==2
    finally:restarted.stop()


def test_restart_exhausted_job_marked_failed(tmp_path):
    path=tmp_path/'auth.sqlite3';uid=user(path,'AliceResearch')
    queue=PortalJobs(path,lambda *_:None)
    jid=queue.enqueue(uid,'default')['id']
    with auth_db(path) as db:
        db.execute("UPDATE portal_jobs SET state='running',attempts=2 WHERE id=?",(jid,))
    queue.recover()
    assert queue.latest(uid)['state']=='failed'
    assert '中断恢复次数' in queue.latest(uid)['error']


def test_cancel_account_cancels_queued_tasks(tmp_path):
    path=tmp_path/'auth.sqlite3';uid=user(path,'AliceResearch')
    queue=PortalJobs(path,lambda *_:None)
    queue.enqueue(uid,'default');queue.cancel_account(uid)
    assert queue.latest(uid)['state']=='cancelled'


def test_concurrent_enqueue_is_atomic(tmp_path):
    path=tmp_path/'auth.sqlite3';uid=user(path,'AliceResearch')
    queue=PortalJobs(path,lambda *_:None)
    results=[]
    def enter():
        try:results.append(('ok',queue.enqueue(uid,'default')))
        except QueueError:results.append(('rejected',None))
    threads=[threading.Thread(target=enter) for _ in range(6)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert [x[0] for x in results].count('ok')==1
    assert [x[0] for x in results].count('rejected')==5


def test_http_persists_and_exposes_only_own_history(tmp_path):
    portal=Portal(tmp_path/'portal',fetcher=lambda cfg,logger:[])
    server=make_server('127.0.0.1',0,portal)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    from tests.test_v116_portal_and_synthesis import Client,register
    try:
        a=Client(server.server_port);b=Client(server.server_port)
        register(a,'AliceResearch');register(b,'BobResearch')
        code,info=a.post('/api/public/search',{'topic_id':'default'})
        assert code==202 and info['job_id']>0
        result=wait_for(lambda:a.get('/api/public/jobs')[1].get('state')=='success')
        assert result
        history=a.get('/api/public/jobs/history')[1]['jobs']
        assert len(history)==1 and history[0]['id']==info['job_id']
        assert b.get('/api/public/jobs/history')[1]['jobs']==[]
        assert b.post('/api/public/jobs/cancel',{'job_id':info['job_id']})[1]['cancel_requested'] is False
        assert a.get('/api/public/jobs/history?limit=0')[0]==200
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_public_job_state_survives_new_portal_instance(tmp_path):
    root=tmp_path/'data';portal=Portal(root)
    with auth_db(portal.auth_path) as db:uid=create_account(db,'AliceResearch',SECRET)['id']
    portal.search_async(uid,'default')
    new=Portal(root)
    assert new.job(uid)['state']=='queued'


def test_synthesis_diagnostics_marks_unverified_and_incomplete():
    base={'paper_id':'a','title':'Study A','page':3,'kind':'metric','dataset':'Dataset X',
          'metric':'RMSE','value':'0.12','setting':'split A','baseline':'FNO',
          'quote':'The RMSE is 0.12 on Dataset X', 'provenance':'user_import_unverified'}
    records=[base,{**base,'paper_id':'b','title':'Study B','value':'0.10','baseline':'CNN'},
             {**base,'paper_id':'c','title':'Study C','setting':''},base]
    result=compare_records(records)
    assert result['diagnostics']['unverified']==3
    assert result['diagnostics']['incomplete']==1
    assert result['diagnostics']['duplicates']==1
    assert any('对照基线不一致' in flag for flag in result['comparisons'][0]['flags'])
    assert '未在服务器核验' in ' '.join(result['comparisons'][0]['flags'])
    assert '数据完整性' in to_markdown(result,'Methods',imported=True)


def test_synthesis_rejects_incompatible_settings_and_finds_conflicts():
    base={'paper_id':'a','title':'A','page':3,'kind':'metric','dataset':'X',
          'metric':'MAE','value':'0.1','setting':'fold 1','baseline':'FNO','quote':'MAE 0.1'}
    out=compare_records([base,{**base,'value':'0.2','quote':'MAE 0.2'},
                         {**base,'paper_id':'b','title':'B','setting':'fold 2'}])
    assert out['comparisons']==[]
    assert out['diagnostics']['conflicting_values']==1
    assert out['diagnostics']['incompatible_settings']==1


def test_synthesis_no_cross_paper_unqualified_percentage_scores():
    base={'paper_id':'a','title':'A','page':3,'kind':'metric','dataset':'X',
          'metric':'MAE','value':'9%','setting':'fold 1','baseline':'FNO','quote':'MAE 9%'}
    result=compare_records([base,{**base,'paper_id':'b','title':'B','value':'0.1'}])
    assert any('百分号' in flag for flag in result['comparisons'][0]['flags'])
    assert '提升' not in to_markdown(result,'Methods').split('## 四、使用边界')[0]


def test_public_frontend_has_job_resume_and_cancel_controls():
    from paper_radar.public_portal import STATIC
    html=(STATIC/'portal.html').read_text(encoding='utf-8')
    js=(STATIC/'portal.js').read_text(encoding='utf-8')
    assert 'id="cancelJob"' in html and 'id="jobHistory"' in html
    assert 'refreshJobState' in js and '/api/public/jobs/cancel' in js
    assert '/api/public/jobs/history' in js


def test_http_papers_page_is_scoped_and_bounded(tmp_path):
    portal=Portal(tmp_path/'data')
    server=make_server('127.0.0.1',0,portal)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    from tests.test_v116_portal_and_synthesis import Client,register
    try:
        a=Client(server.server_port);b=Client(server.server_port)
        info=register(a,'AliceResearch');register(b,'BobResearch')
        with portal.workspace(info['user']['id']) as db:
            for i in range(65):
                pid=f'paper-{i:03}'
                db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',
                    (pid,pid,'abstract','2026-11-04','','','now'))
                db.execute('INSERT INTO topic_papers VALUES (?,?)',('default',pid))
        code,page=a.get('/api/public/papers?topic_id=default&limit=30&offset=30')
        assert code==200
        assert page['total']==65 and len(page['papers'])==30
        assert len(a.get('/api/public/papers?topic_id=default&limit=30&offset=60')[1]['papers'])==5
        assert b.get('/api/public/papers?topic_id=default')[1]['papers']==[]
        assert a.get('/api/public/papers?limit=10000')[0]==400
        assert a.get('/api/public/papers?offset=-1')[0]==400
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_admin_queue_stats_do_not_expose_user_history(tmp_path):
    portal=Portal(tmp_path/'data')
    with auth_db(portal.auth_path) as db:
        create_account(db,'SystemOwner',SECRET,role='admin')
    server=make_server('127.0.0.1',0,portal)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    from tests.test_v116_portal_and_synthesis import Client,register
    try:
        user_client=Client(server.server_port);admin=Client(server.server_port)
        info=register(user_client,'AliceResearch')
        assert user_client.get('/api/public/admin/jobs')[0]==403
        code,_=admin.post('/api/public/login',{'username':'SystemOwner','password':SECRET},csrf=False)
        assert code==200
        code,stats=admin.get('/api/public/admin/jobs')
        assert code==200
        assert stats['workers']==2 and 'queued' in stats['states']
        assert info['user']['id'] not in json.dumps(stats)
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_forwarded_ip_must_be_opted_in_and_must_be_valid(tmp_path):
    from types import SimpleNamespace
    from email.message import Message
    from paper_radar.public_portal import Handler
    portal=Portal(tmp_path/'portal')
    handler=object.__new__(Handler)
    handler.client_address=('127.0.0.1',10000)
    header=Message();header['X-Real-IP']='203.0.113.9';header['X-Forwarded-For']='evil.example'
    handler.headers=header
    handler.server=SimpleNamespace(portal=portal)
    assert handler.client_ip()=='127.0.0.1'
    portal.trust_proxy_ip=True
    assert handler.client_ip()=='203.0.113.9'
    header.replace_header('X-Real-IP','not-an-ip')
    from paper_radar.public_portal import PortalError
    with pytest.raises(PortalError,match='反向代理'):
        handler.client_ip()


def test_disable_account_cancels_running_search_before_database_write(tmp_path):
    from paper_radar.portal_auth import set_active
    from paper_radar.models import Paper
    entering=threading.Event();proceed=threading.Event()
    def fetch(cfg,logger):
        entering.set()
        assert proceed.wait(5)
        return [Paper(paper_id='arxiv-001',title='Test Paper',abstract='x',published='2026-11-04',
            updated='2026-11-04',url='https://arxiv.org/abs/test',authors='author',category='cs.LG',pdf_url='')]
    portal=Portal(tmp_path/'data',fetcher=fetch)
    with auth_db(portal.auth_path) as db:
        admin=create_account(db,'SystemOwner',SECRET,role='admin')['id']
        member=create_account(db,'AliceResearch',SECRET)['id']
    portal.search_async(member,'default');portal.job_queue.start()
    try:
        assert entering.wait(4)
        with auth_db(portal.auth_path) as db:
            set_active(db,admin,member,False)
        portal.job_queue.cancel_account(member)
        proceed.set()
        wait_for(lambda:portal.job(member)['state']=='cancelled')
        with portal.workspace(member) as db:
            assert db.execute('SELECT COUNT(*) FROM papers').fetchone()[0]==0
    finally:proceed.set();portal.job_queue.stop()


def test_completed_history_retention_cleans_only_old_finished_jobs(tmp_path):
    path=tmp_path/'auth.sqlite3';uid=user(path,'AliceResearch')
    jobs=PortalJobs(path,lambda *_:None)
    older=jobs.enqueue(uid,'default')['id'];jobs.cancel(uid,older)
    with auth_db(path) as db:
        db.execute('UPDATE portal_jobs SET finished_at=? WHERE id=?',
                   (int(time.time())-91*86400,older))
    newer=jobs.enqueue(uid,'default')['id']
    assert newer>older
    with auth_db(path) as db:
        assert db.execute('SELECT COUNT(*) FROM portal_jobs WHERE id=?',(older,)).fetchone()[0]==0


def test_real_arxiv_adapter_serial_gate_persists_last_request(tmp_path,monkeypatch):
    from paper_radar.sources import supervisor
    root=tmp_path/'portal'
    portal=Portal(root)
    with auth_db(portal.auth_path) as db:
        uid=create_account(db,'AliceResearch',SECRET)['id']
    seen=[]
    def pretend(name,cfg,**kwargs):
        seen.append((name,cfg['max_results']))
        return []
    monkeypatch.setattr(supervisor,'fetch_isolated',pretend)
    assert portal.search(uid,'default')['retrieved']==0
    assert seen==[('arxiv',20)]
    with auth_db(portal.auth_path) as db:
        row=db.execute("SELECT occurred_at FROM auth_attempts WHERE identifier='source:arxiv_public'").fetchone()
        assert row and row[0]>=int(time.time())-2
