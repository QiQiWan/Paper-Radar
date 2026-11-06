"""Paper-Radar v1.19.1: reliable task narrative, immutable reports and UX safety."""
from __future__ import annotations
import json
import sqlite3
from hashlib import sha256
from pathlib import Path
from threading import Thread
import time

import pytest
from paper_radar import __version__
from paper_radar.migrations import REGISTRIES, migrate, status
from paper_radar.portal_auth import db as auth_db, create_account
from paper_radar.portal_jobs import PortalJobs
from paper_radar.portal_reports import create_report, list_reports, read_report
from paper_radar.public_portal import Portal, make_server
from test_v116_portal_and_synthesis import Client, register

PASS = 'safe strong test account password 12345'


def account(portal, name='StudyA'):
    with auth_db(portal.auth_path) as db:
        return create_account(db,name,PASS)['id']


def seed(portal, uid, pid='paper-001', value='0.126'):
    with portal.workspace(uid) as db:
        db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',
            (pid,'A tested paper title','Reported validated metric','2026-10-20','https://arxiv.org/abs/2601.10001','','2026-11-05'))
        db.execute('INSERT INTO topic_papers VALUES (?,?)',('default',pid))
        quote=f'In dataset benchmark X, RMSE is {value} under the fixed split.'
        db.execute('''INSERT INTO evidence(id,paper_id,kind,dataset,metric,value,baseline,setting,page,quote,provenance,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (sha256((pid+value).encode()).hexdigest(),pid,'metric','X','RMSE',value,'Base','fixed split',3,
             quote,'user_import_unverified','2026-11-05'))


def test_version_and_sequential_schema(tmp_path):
    assert __version__==__import__('tools.packaging.release_version',fromlist=['read_version']).read_version()
    assert len(REGISTRIES['portal_auth'])==6
    assert len(REGISTRIES['portal_workspace'])==8
    p=Portal(tmp_path/'data')
    uid=account(p)
    with auth_db(p.auth_path) as db:
        assert status(db,'portal_auth')['current']
        assert migrate(db,'portal_auth')==6
        assert db.execute('SELECT name FROM sqlite_master WHERE name="portal_job_events"').fetchone()
    with p.workspace(uid) as db:
        assert status(db,'portal_workspace')['version']==8
        assert db.execute('SELECT name FROM sqlite_master WHERE name="research_reports"').fetchone()


def test_lease_fenced_phase_history(tmp_path):
    portal=Portal(tmp_path/'data');uid=account(portal)
    q=portal.job_queue
    jid=q.enqueue(uid,'default')['id']
    job=q._claim('worker-1');token=job['lease_token']
    assert not q.update_stage(jid,'discovery',lease_token='invalid-token')
    assert q.update_stage(jid,'discovery',lease_token=token)
    assert q.update_stage(jid,'synthesis',lease_token=token)
    assert q._finish(jid,'success',{'retrieved':4,'new':2},lease_token=token)
    phases=[(e['stage'],e['status']) for e in q.detail(uid,jid)['events']]
    assert phases==[('starting','started'),('starting','completed'),('discovery','started'),
                    ('discovery','completed'),('synthesis','started'),('synthesis','completed'),
                    ('task','completed')]
    assert q.latest(uid)['result']['retrieved']==4
    assert q.latest(uid)['events'][-2]['metrics']=={'retrieved':4,'new':2}
    assert not q.update_stage(jid,'fulltext',lease_token=token)


def test_finished_task_still_shows_failure_phase(tmp_path):
    p=Portal(tmp_path/'data');uid=account(p);q=p.job_queue
    jid=q.enqueue(uid,'default')['id'];claim=q._claim('worker')
    assert q.update_stage(jid,'fulltext',lease_token=claim['lease_token'])
    q._finish(jid,'failed',error='PDF parser timeout',lease_token=claim['lease_token'])
    report=q.detail(uid,jid)
    assert report['state']=='failed'
    assert ('fulltext','failed') in [(e['stage'],e['status']) for e in report['events']]
    assert not any('secret' in json.dumps(event) for event in report['events'])


def test_queued_cancel_has_terminal_event(tmp_path):
    p=Portal(tmp_path/'data');uid=account(p);q=p.job_queue
    jid=q.enqueue(uid,'default')['id']
    assert q.cancel(uid,jid)
    assert ('task','cancelled') in [(e['stage'],e['status']) for e in q.detail(uid,jid)['events']]


def test_old_lease_event_cannot_write(tmp_path):
    p=Portal(tmp_path/'data');uid=account(p);q=p.job_queue
    jid=q.enqueue(uid,'default')['id'];claim=q._claim('worker')
    with auth_db(p.auth_path) as db:
        db.execute('UPDATE portal_jobs SET lease_until=1 WHERE id=?',(jid,))
    q.recover()
    assert q.latest(uid)['state']=='queued'
    assert not q.update_stage(jid,'download',lease_token=claim['lease_token'])
    assert any(e['status']=='failed' for e in q.detail(uid,jid)['events'])


def test_job_detail_is_account_scoped(tmp_path):
    p=Portal(tmp_path/'data');first=account(p);other=account(p,'StudyB')
    jid=p.job_queue.enqueue(first,'default')['id']
    assert p.job_queue.detail(first,jid)['id']==jid
    assert p.job_queue.detail(other,jid) is None
    assert p.job_queue.events(other,jid)==[]


def test_report_snapshot_immutable_after_evidence_edit(tmp_path):
    p=Portal(tmp_path/'data');uid=account(p);seed(p,uid)
    saved=create_report(p,uid,'default',job_id=18)
    row=read_report(p,uid,saved['id'])
    assert row['job_id']==18 and row['paper_count']==1
    assert '0.126' in row['markdown']
    with p.workspace(uid) as db:db.execute('DELETE FROM evidence')
    same=read_report(p,uid,saved['id'])
    assert same['markdown']==row['markdown']
    newer=create_report(p,uid,'default')
    assert newer['source_digest']!=saved['source_digest']
    assert len(list_reports(p,uid,'default'))==2


def test_reports_dont_cross_account_boundaries(tmp_path):
    p=Portal(tmp_path/'data');first=account(p);second=account(p,'StudyB')
    seed(p,first);saved=create_report(p,first,'default')
    assert read_report(p,second,saved['id']) is None
    assert list_reports(p,second,'default')==[]
    assert read_report(p,first,'not-an-id') is None


def test_report_generation_occurs_in_worker_action(tmp_path):
    p=Portal(tmp_path/'data');uid=account(p);seed(p,uid)
    q=p.job_queue
    job=q.enqueue(uid,'default',kind='synthesize');claim=q._claim('worker')
    stages=[]
    result=p.research_job(uid,'default','synthesize','',{},lambda:False,stages.append,job_id=job['id'])
    assert stages==['synthesis']
    assert result['report_id']
    assert q._finish(job['id'],'success',result=result,lease_token=claim['lease_token'])
    assert q.latest(uid)['result']['report_id']==result['report_id']
    assert read_report(p,uid,result['report_id'])['job_id']==job['id']


def test_report_history_has_per_topic_filter(tmp_path):
    p=Portal(tmp_path/'data');uid=account(p)
    seed(p,uid);a=create_report(p,uid,'default')
    with p.workspace(uid) as db:
        db.execute('INSERT INTO topics VALUES (?,?,?,?)',('research2','Another',json.dumps(['operator']),'2026-11-05'))
    b=create_report(p,uid,'research2')
    assert [r['id'] for r in list_reports(p,uid,'default')]==[a['id']]
    assert [r['id'] for r in list_reports(p,uid,'research2')]==[b['id']]


@pytest.fixture
def service(tmp_path):
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    server=make_server('127.0.0.1',0,portal)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield portal,server.server_port
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def test_report_and_job_http_authorization(service):
    portal,port=service
    a=Client(port);b=Client(port)
    info=register(a,'ViewerOne');register(b,'ViewerTwo')
    seed(portal,info['user']['id']);saved=create_report(portal,info['user']['id'],'default')
    job=portal.job_queue.enqueue(info['user']['id'],'default')
    assert a.get('/api/public/reports?topic_id=default')[1]['reports'][0]['id']==saved['id']
    code,report=a.get('/api/public/reports/view?id='+saved['id'])
    assert code==200 and '0.126' in report['markdown']
    assert b.get('/api/public/reports/view?id='+saved['id'])[0]==404
    assert b.get('/api/public/reports?topic_id=default')[1]['reports']==[]
    assert b.get('/api/public/jobs/detail?id='+str(job['id']))[0]==404
    assert a.get('/api/public/jobs/detail?id='+str(job['id']))[0]==200
    assert Client(port).get('/api/public/reports?topic_id=default')[0]==401
    assert a.get('/api/public/reports/view?id=INVALID')[0]==404


def test_cached_report_get_does_not_generate(service,monkeypatch):
    portal,port=service;a=Client(port)
    info=register(a,'ViewerOne');uid=info['user']['id']
    seed(portal,uid);report=create_report(portal,uid,'default')
    import paper_radar.portal_research as research
    def forbidden(*_args,**_kwargs):raise AssertionError('GET generated a report')
    monkeypatch.setattr(research,'synthesis',forbidden)
    assert a.get('/api/public/reports?topic_id=default')[0]==200
    assert a.get('/api/public/reports/view?id='+report['id'])[0]==200


def test_ui_has_real_report_queue_and_safe_renderer():
    static=Path(__file__).resolve().parents[1]/'paper_radar'/'static'
    js=(static/'portal.js').read_text()
    html=(static/'portal.html').read_text()
    assert "submitResearch('/api/public/research/synthesize'" in js
    assert "api('/api/public/synthesis?topic_id='" not in js
    for endpoint in ('/api/public/jobs/detail','/api/public/reports/view','/api/public/reports?topic_id='):
        assert endpoint in js
    assert "renderMarkdownSafely" in js and "container.replaceChildren()" in js
    assert 'id="reportReader"' in html and 'id="jobStages"' in html
    assert 'id="refreshJob"' in html
    for field in ('newTopic','newKeywords','evidenceCsv','oldPassword','newPassword'):
        assert f'for="{field}"' in html
    # Attribute order is not semantic: HTML formatters may reorder aria-label and id.
    import re
    local=(static/'index.html').read_text()
    for field in ('librarySearch','libraryFilter','logSource','fulltextSearchInput'):
        element=re.search(r'<(?:input|select)[^>]*\bid="'+re.escape(field)+r'"[^>]*>',local)
        assert element is not None and 'aria-label=' in element.group(0),field


def _luminance(hexcolor):
    nums=[int(hexcolor[i:i+2],16)/255 for i in (1,3,5)]
    transformed=[x/12.92 if x<=0.04045 else ((x+.055)/1.055)**2.4 for x in nums]
    return sum(a*b for a,b in zip(transformed,(.2126,.7152,.0722)))


def test_readable_secondary_text_contrast():
    a,b=sorted((_luminance('#42586b'),_luminance('#ffffff')))
    assert (b+.05)/(a+.05)>=4.5
    a,b=sorted((_luminance('#4b6077'),_luminance('#ffffff')))
    assert (b+.05)/(a+.05)>=4.5


def test_legacy_desktop_routes_not_exposed_in_public(service):
    _,port=service;client=Client(port);register(client,'ViewerOne')
    assert client.get('/api/report-preview?name=anything.md')[0]==404
    assert client.get('/api/reports')[0]==404
    assert client.get('/api/logs')[0]==404


def test_isolated_worker_creates_reviewable_report(tmp_path):
    from paper_radar.portal_worker import IsolatedWorker
    portal=Portal(tmp_path/'data',embedded_workers=False)
    uid=account(portal);seed(portal,uid)
    job=portal.job_queue.enqueue(uid,'default',kind='synthesize')
    worker=IsolatedWorker(portal.root,lease_seconds=24,heartbeat_seconds=1,
                          job_timeout=35,memory_mb=1536,cpu_seconds=30)
    assert worker.run_one() is True
    record=portal.job_queue.detail(uid,job['id'])
    assert record['state']=='success',record
    assert record['result']['report_id']
    assert ('synthesis','completed') in [(item['stage'],item['status']) for item in record['events']]
    report=read_report(portal,uid,record['result']['report_id'])
    assert report and report['job_id']==job['id']
