"""v1.21.2 public source isolation, cancellation and DB polling regression."""
import sqlite3
import time
import pytest
from pathlib import Path
from paper_radar.public_portal import Portal,PortalError
from paper_radar.portal_jobs import JobCancelled,PortalJobs
from paper_radar.portal_auth import db as auth_db,create_account,authenticate,create_session
from paper_radar.sources.supervisor import SourceTimeout


def user(portal):
    with auth_db(portal.auth_path) as db:
        return create_account(db,'NetworkUser','long enough password test 1234')['id']


def test_public_default_source_uses_subprocess(monkeypatch,tmp_path):
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    uid=user(portal)
    seen=[]
    def isolated(name,cfg,**kwargs):
        seen.append((name,kwargs['timeout_seconds'],bool(kwargs['checkpoint'])))
        return []
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',isolated)
    result=portal.search(uid,'default')
    assert result['retrieved']==0
    assert seen==[('arxiv',portal.source_timeout_seconds,True)]
    assert result['source_results'][0]['mode']=='isolated_process'
    with auth_db(portal.auth_path) as db:
        assert db.execute("SELECT COUNT(*) FROM auth_attempts WHERE identifier='source:arxiv_public'").fetchone()[0]==1


def test_public_offline_fails_fast_with_clean_message(monkeypatch,tmp_path):
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    uid=user(portal)
    def timeout(*args,**kwargs):raise SourceTimeout('secret URL or key')
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',timeout)
    with pytest.raises(PortalError,match='来源连接超时') as e:
        portal.search(uid,'default')
    assert 'secret' not in str(e.value)


def test_public_cancellation_is_not_an_offline_failure(monkeypatch,tmp_path):
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    uid=user(portal)
    checks=[]
    def blocked(name,cfg,**kw):
        checks.append(1)
        kw['checkpoint']()
        raise AssertionError('should never reach here')
    monkeypatch.setattr('paper_radar.sources.supervisor.fetch_isolated',blocked)
    stop=[False]
    def cancel():
        if checks:stop[0]=True
        return stop[0]
    with pytest.raises(JobCancelled,match='取消'):
        portal.search(uid,'default',cancel)
    assert len(checks)==1


def test_authentication_read_does_not_rewrite_recent_session(tmp_path,monkeypatch):
    path=tmp_path/'auth.sqlite3'
    with auth_db(path) as db:
        uid=create_account(db,'RecentSession','long enough password test 1234')['id']
        token,_=create_session(db,uid)
        before=db.execute('SELECT last_seen FROM sessions').fetchone()[0]
    with auth_db(path) as db:
        assert authenticate(db,token)['id']==uid
        assert db.execute('SELECT last_seen FROM sessions').fetchone()[0]==before
    # Upgrade/downgrade prevention remains covered directly by migration tests.


def test_worker_background_poll_recovery_is_rate_limited(tmp_path,monkeypatch):
    path=tmp_path/'auth.sqlite3'
    with auth_db(path) as db:create_account(db,'RecoveryUser','long enough password test 1234')
    q=PortalJobs(path,lambda *_:None)
    q.recover(force=False)
    original=q._last_auto_recovery
    q.recover(force=False)
    assert q._last_auto_recovery==original
    q.recover() # Explicit reconciliation must still always run.
    assert q._last_auto_recovery>=original


def test_workspace_initializes_once_and_survives_reopening(tmp_path,monkeypatch):
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    uid=user(portal)
    with portal.workspace(uid) as db:
        db.execute("UPDATE topics SET name='保存的主题' WHERE id='default'")
    with portal.workspace(uid) as db:
        assert db.execute("SELECT name FROM topics WHERE id='default'").fetchone()[0]=='保存的主题'
    assert uid in portal._workspace_initialized
