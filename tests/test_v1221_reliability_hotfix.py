"""v1.22.1: real regressions for proxy, metadata, row attribution and restart."""
from __future__ import annotations
import http.client
import json
import os
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
from paper_radar import __version__
from paper_radar import migrations, public_portal
from paper_radar.evidence_relations import attribution_assessment
from paper_radar.experiments import validate_experiments
from paper_radar.models import Paper
from paper_radar.portal_auth import db as auth_db, create_account
from paper_radar.public_portal import Portal, make_server
from paper_radar.synthesis import evidence_identity, compare_records
from paper_radar.portal_metadata import merge_arxiv_paper

PASSWORD='test password for portal safety 12345'

def make_paper(title='Original Title',updated='2026-09-05T12:00:00Z',abstract='Study'):
    return Paper('2601.12345',title,abstract,'Someone','2026-01-02',updated,'cs.LG',
                 'https://arxiv.org/abs/2601.12345','https://arxiv.org/pdf/2601.12345',
                 doi='10.1000/a')

def account(p):
    with auth_db(p.auth_path) as db:
        return create_account(db,'HotfixUser',PASSWORD)['id']

def test_metadata_refresh_keeps_reading_status_and_cannot_rollback(tmp_path):
    current=[make_paper()]
    p=Portal(tmp_path/'data',fetcher=lambda cfg,logger:current,embedded_workers=False)
    uid=account(p)
    assert p.search(uid,'default')['new']==1
    with p.workspace(uid) as db:
        db.execute("INSERT INTO topic_paper_flags VALUES ('default','2601.12345',1,'done',1,'2026-11-05')")
    current[:]=[make_paper(title='Revised Title',updated='2026-10-20T12:00:00Z',abstract='Revised abstract')]
    assert p.search(uid,'default')['updated']==1
    current[:]=[make_paper(title='Stale Title',updated='2026-08-01T12:00:00Z')]
    assert p.search(uid,'default')['stale_skipped']==1
    current[:]=[make_paper(title='Unknown timestamp',updated='')]
    assert p.search(uid,'default')['stale_skipped']==1
    with p.workspace(uid) as db:
        assert db.execute('SELECT title FROM papers').fetchone()[0]=='Revised Title'
        assert db.execute('SELECT abstract FROM papers').fetchone()[0]=='Revised abstract'
        flags=db.execute('SELECT is_starred,reading_status,feedback FROM topic_paper_flags').fetchone()
        assert tuple(flags)==(1,'done',1)
        assert db.execute('SELECT COUNT(*) FROM paper_metadata_history').fetchone()[0]==1
        assert db.execute('SELECT source_updated FROM paper_metadata_versions').fetchone()[0]=='2026-10-20T12:00:00Z'

def test_missing_fields_do_not_erase_existing_metadata(tmp_path):
    p=Portal(tmp_path/'data',embedded_workers=False)
    uid=account(p)
    with p.workspace(uid) as db:
        first=make_paper()
        assert merge_arxiv_paper(db,first,'2026-11-05')=='inserted'
        partial=make_paper(title='Corrected Title',updated='2026-11-03T12:00:00Z')
        partial.doi='';partial.abstract='';partial.url=''
        assert merge_arxiv_paper(db,partial,'2026-11-05')=='updated'
        row=db.execute('SELECT * FROM papers').fetchone()
        assert row['doi']=='10.1000/a' and row['abstract']=='Study'
        assert row['title']=='Corrected Title'

def test_append_only_migration_from_v4_preserves_workspace(tmp_path,monkeypatch):
    original=migrations.REGISTRIES['portal_workspace']
    monkeypatch.setitem(migrations.REGISTRIES,'portal_workspace',original[:4])
    p=Portal(tmp_path/'data',embedded_workers=False)
    uid=account(p)
    with p.workspace(uid) as db:
        assert migrations.status(db,'portal_workspace')['version']==4
        db.execute("INSERT INTO papers VALUES ('x','X','','','','','2026-11-05')")
        db.execute("INSERT INTO topic_papers VALUES ('default','x')")
        db.execute("INSERT INTO topic_paper_flags VALUES ('default','x',1,'reading',1,'2026-11-05')")
    monkeypatch.setitem(migrations.REGISTRIES,'portal_workspace',original)
    with p.workspace(uid) as db:
        assert migrations.status(db,'portal_workspace')['version']==8
        assert db.execute('SELECT title FROM papers WHERE id="x"').fetchone()[0]=='X'
        assert tuple(db.execute('SELECT is_starred,reading_status,feedback FROM topic_paper_flags').fetchone())==(1,'reading',1)
    monkeypatch.setitem(migrations.REGISTRIES,'portal_workspace',original[:4])
    with pytest.raises(migrations.MigrationError,match='newer'):
        with p.workspace(uid):pass

def test_cross_row_mismatch_is_rejected_and_same_row_retained():
    quote='Table 1: Dataset L2 error comparison\nDataset L2 Error\nDarcy 0.99\nNavier 0.01'
    context={7:{'page':13,'content':quote}}
    def raw(dataset,value):
        return json.dumps({'findings':[{'chunk_id':7,'kind':'metric', 'dataset':dataset,
            'metric':'L2','value':value,'baseline':'','setting':'','quote':quote}]})
    assert validate_experiments(raw('Darcy','0.01'),context)==[]
    accepted=validate_experiments(raw('Darcy','0.99'),context)
    assert len(accepted)==1 and accepted[0]['value']=='0.99'
    assert attribution_assessment('Darcy 0.99; Navier 0.01',dataset='Darcy',value='0.01')['accepted'] is False
    assert attribution_assessment('This paragraph reports Darcy error 0.01',dataset='Darcy',value='0.01')['relation_verified'] is False

def test_synthesis_never_claims_row_level_verification():
    data=evidence_identity('user_pdf_quote_verified')
    assert data['quote_verified_against_pdf'] is True
    assert data['relation_verified'] is False and data['relation_review_required'] is True
    one={'paper_id':'p1','title':'Paper1','kind':'metric','dataset':'Darcy', 'metric':'L2',
         'value':'0.1','baseline':'Base','setting':'splitA', 'quote':'Darcy L2 0.1 Base splitA',
         'page':1,'provenance':'pdf_sha256_verified'}
    result=compare_records([one,{**one,'paper_id':'p2','title':'Paper2','value':'0.2'}])
    assert result['comparisons'][0]['review_status']=='manual_review_required'
    assert '行列归属' in result['comparisons'][0]['flags'][0]

class Request:
    def __init__(self,port):self.port=port
    def post(self,route,ip=None):
        headers={'Origin':f'http://127.0.0.1:{self.port}', 'Host':f'127.0.0.1:{self.port}',
                 'Content-Type':'application/json','X-Portal-Action':'1'}
        if ip is not None:headers['X-Real-IP']=ip
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        conn.request('POST',route,body=json.dumps({'username':'HotfixUser','password':'bad-password'}),headers=headers)
        response=conn.getresponse();code=response.status
        data=json.loads(response.read());conn.close()
        return code,data

def test_proxy_ip_is_fail_closed_and_separated_per_client(tmp_path):
    p=Portal(tmp_path/'data',trust_proxy_ip=True,embedded_workers=False)
    server=make_server('127.0.0.1',0,p)
    t=Thread(target=server.serve_forever,daemon=True);t.start()
    try:
        client=Request(server.server_port)
        assert client.post('/api/public/login')[0]==400
        assert client.post('/api/public/login','not an ip')[0]==400
        assert client.post('/api/public/login','198.51.100.11')[0]==401
        assert client.post('/api/public/login','198.51.100.12')[0]==401
        with auth_db(p.auth_path) as db:
            rows={r[0] for r in db.execute('SELECT DISTINCT identifier FROM auth_attempts')}
        assert {'login_ip:198.51.100.11','login_ip:198.51.100.12'}<=rows
        assert 'login_ip:127.0.0.1' not in rows
    finally:
        server.shutdown();server.server_close();t.join(timeout=5)

def test_no_proxy_header_trust_by_default(tmp_path):
    p=Portal(tmp_path/'data',trust_proxy_ip=False,embedded_workers=False)
    fake=SimpleNamespace(server=SimpleNamespace(portal=p),client_address=('127.0.0.1',12345),
                         headers={'X-Real-IP':'203.0.113.5'})
    assert public_portal.Handler.client_ip(fake)=='127.0.0.1'

def test_runtime_marker_writes_only_under_data_root(tmp_path,monkeypatch):
    install=tmp_path/'read_only_install'
    install.mkdir();(install/'.paper_radar_updates').mkdir()
    (install/'.paper_radar_updates'/'public_service.active').write_text('obsolete marker')
    monkeypatch.setattr(public_portal,'ROOT',install)
    class Server:
        server_port=8899
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def serve_forever(self,poll_interval=.5):
            assert (root/'runtime'/'public_service.active').is_file()
            assert not (install/'runtime').exists()
    root=tmp_path/'data'
    monkeypatch.setattr(public_portal,'make_server',lambda *args:Server())
    assert public_portal.cli(['--data-root',str(root),'serve','--origin','http://127.0.0.1:8899'])==0
    assert not (root/'runtime'/'public_service.active').exists()
    assert (install/'.paper_radar_updates'/'public_service.active').read_text()=='obsolete marker'
    # A stale data-root marker from a force kill must be safely overwritten.
    (root/'runtime'/'public_service.active').write_text('stale')
    assert public_portal.cli(['--data-root',str(root),'serve','--origin','http://127.0.0.1:8899'])==0

def test_public_internal_error_returns_id_and_redacted_audit(tmp_path):
    from test_v116_portal_and_synthesis import Client,register
    p=Portal(tmp_path/'data',embedded_workers=False)
    server=make_server('127.0.0.1',0,p)
    t=Thread(target=server.serve_forever,daemon=True);t.start()
    try:
        c=Client(server.server_port);register(c,'ErrorProbe')
        def crash(uid):raise RuntimeError('private user paper title secret')
        p.job=crash
        code,data=c.get('/api/public/jobs')
        assert code==500
        assert len(data['request_id'])==16 and data['error_code']=='E_INTERNAL'
        assert 'secret' not in json.dumps(data)
        audit=(p.root/'audit.jsonl').read_text()
        assert data['request_id'] in audit and 'private user paper title' not in audit
    finally:server.shutdown();server.server_close();t.join(timeout=5)

def test_frontend_contains_retry_jitter_reconnect_and_closed_signup():
    content=(Path(__file__).parent.parent/'paper_radar/static/portal.js').read_text()
    for expression in ('retryJobPoll()',"addEventListener('online'", "addEventListener('visibilitychange'",
                       'if(jobRefreshInFlight)return;', 'registrationEnabled=health.registration_enabled!==false',
                       'if(err.status===401)'):
        assert expression in content
    assert 'scheduleJobPoll(busy?2300:30000)' in content


def test_service_templates_match_closed_proxy_and_writable_state():
    root=Path(__file__).parent.parent
    web=(root/'deploy/paperradar-public.service').read_text()
    worker=(root/'deploy/paperradar-worker@.service').read_text()
    nginx=(root/'deploy/nginx-paper-radar.conf').read_text()
    assert '--trust-proxy-ip --disable-registration' in web
    assert 'StateDirectory=paperradar' in web and 'StateDirectory=paperradar' in worker
    assert 'ReadWritePaths=/var/lib/paperradar' in web
    assert nginx.count('proxy_set_header X-Real-IP $remote_addr;')==2
    assert 'location = /api/public/pdf' in nginx

def test_public_provider_ledger_writes_to_data_root(monkeypatch,tmp_path):
    from paper_radar.provider_usage import record_usage, read_usage
    root=tmp_path/'public'
    monkeypatch.setenv('PAPERRADAR_PORTAL_DATA',str(root))
    record_usage('TestModel',19,8)
    assert (root/'metrics'/'api_usage.json').is_file()
    assert read_usage(root/'metrics'/'api_usage.json')['TestModel']=={
        'requests':1,'input_tokens':19,'output_tokens':8}


def test_provider_usage_increments_are_cross_process_atomic(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    import subprocess,sys
    path=tmp_path/'api_usage.json'
    script='from pathlib import Path;from paper_radar.provider_usage import record_usage\nfor _ in range(12):record_usage("Model",3,2,path=Path(__import__("sys").argv[1]))'
    subprocesses=[subprocess.Popen([sys.executable,'-c',script,str(path)],
                                   cwd=str(Path(__file__).parent.parent),stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE) for _ in range(4)]
    for process in subprocesses:
        _,stderr=process.communicate(timeout=15)
        assert process.returncode==0,stderr.decode()
    from paper_radar.provider_usage import read_usage
    assert read_usage(path)['Model']=={'requests':48,'input_tokens':144,'output_tokens':96}

def test_cli_can_provision_user_while_public_registration_disabled(tmp_path, monkeypatch, capsys):
    from paper_radar.portal_auth import check_login
    monkeypatch.setattr('getpass.getpass', lambda prompt: PASSWORD)
    root=tmp_path/'production'
    assert public_portal.cli(['--data-root',str(root),'user-create','--username','ProvisionedUser'])==0
    with auth_db(root/'accounts.sqlite3') as db:
        user=check_login(db,'ProvisionedUser',PASSWORD)
        assert user is not None and user['role']=='user'
    assert '账号已建立' in capsys.readouterr().out
    disabled=Portal(root, allow_registration=False, embedded_workers=False)
    assert disabled.allow_registration is False

def test_updater_detects_new_portal_runtime_marker_and_blocks_network(tmp_path,monkeypatch):
    from paper_radar.startup_update import StartupUpdater
    root=tmp_path/'installation';(root/'paper_radar').mkdir(parents=True)
    (root/'paper_radar/__init__.py').write_text('__version__ = "1.22.1"\n')
    marker=root/'storage'/'portal'/'runtime'/'public_service.active'
    marker.parent.mkdir(parents=True)
    marker.write_text('{"pid": 99999}')
    calls=[]
    def no_network(*args,**kwargs):calls.append(1);raise AssertionError('Unexpected update network call')
    updater=StartupUpdater(root,opener=no_network)
    assert updater.public_service_running()
    assert updater.run()['state']=='running' and calls==[]
    marker.unlink()
    extra=tmp_path/'external-portal'/'runtime'/'public_service.active'
    extra.parent.mkdir(parents=True);extra.write_text('service active')
    monkeypatch.setenv('PAPERRADAR_PORTAL_DATA',str(extra.parent.parent))
    assert updater.public_service_running()
    assert updater.run()['state']=='running' and calls==[]

def test_linux_updater_detects_live_service_with_external_data_root(tmp_path):
    import socket, subprocess, sys, time
    from paper_radar.startup_update import StartupUpdater
    if not sys.platform.startswith('linux') or not Path('/proc').exists():
        pytest.skip('Linux /proc service detection')
    root=tmp_path/'installation';root.mkdir()
    data_root=tmp_path/'external'/'portal'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=dict(os.environ)
    env.pop('PAPERRADAR_PORTAL_DATA',None)
    env['PYTHONPATH']=str(Path(__file__).parent.parent)
    worker=subprocess.Popen([sys.executable,'-m','paper_radar.public_portal',
        '--data-root',str(data_root),'serve','--host','127.0.0.1',
        '--port',str(port),'--origin',f'http://127.0.0.1:{port}',
        '--disable-registration'],cwd=root,env=env,
        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        for _ in range(90):
            if (data_root/'runtime'/'public_service.active').exists():break
            if worker.poll() is not None:raise AssertionError(worker.stderr.read().decode())
            time.sleep(.05)
        else:raise AssertionError('live public service failed to start')
        # data root is external, not discoverable from desktop startup environment.
        updater=StartupUpdater(root,opener=lambda *_args,**_kwargs: (_ for _ in ()).throw(AssertionError('network')))
        assert updater.public_service_running() is True
        assert updater.run()['state']=='running'
    finally:
        worker.terminate()
        try:worker.communicate(timeout=4)
        except subprocess.TimeoutExpired:worker.kill();worker.communicate(timeout=4)
