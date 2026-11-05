"""v1.18.0 public research pipeline: isolation, upgrades, PDF evidence and queue."""
import io
import json
import sqlite3
import time
from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
from paper_radar.public_portal import Portal,make_server
from paper_radar.models import Paper
from paper_radar.migrations import status
from paper_radar.portal_auth import db as auth_db,create_account
from paper_radar.portal_research import save_pdf,analyze,records,synthesis,state
from paper_radar.portal_jobs import PortalJobs
from test_v116_portal_and_synthesis import Client,register
import pytest
from threading import Thread

QUOTE='Benchmark on DatasetA with Accuracy 91% versus BaselineX under split1.'

def pdf_bytes(text=QUOTE):
    w=PdfWriter();p=w.add_blank_page(width=595,height=842)
    font=w._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
    p[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    contents=DecodedStreamObject();contents.set_data(('BT /F1 11 Tf 30 750 Td ('+text+') Tj ET').encode())
    p[NameObject('/Contents')]=w._add_object(contents)
    b=io.BytesIO();w.write(b);return b.getvalue()

def paper(pid):
    return Paper(paper_id=pid,title='Paper '+pid,abstract='Neural operator evaluation',authors='A',
        published='2026-10-31',updated='2026-10-31',category='cs.LG',url='https://arxiv.org/abs/'+pid,
        pdf_url='https://arxiv.org/pdf/'+pid)

@pytest.fixture
def service(tmp_path):
    p=Portal(tmp_path/'portal');p.fetcher=lambda cfg,logger:[paper('2601.12345'),paper('2601.12346')]
    p.pdf_downloader=lambda pid:pdf_bytes()
    p.model_callable=lambda rows:json.dumps({'findings':[{'chunk_id':rows[0]['chunk_id'],'kind':'metric',
        'dataset':'DatasetA','metric':'Accuracy','value':'91%','baseline':'BaselineX','setting':'split1',
        'quote':QUOTE}]})
    server=make_server('127.0.0.1',0,p);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield p,server.server_port
    finally:server.shutdown();server.server_close();thread.join(timeout=5)

def finished(p,uid):
    for _ in range(750):
        job=p.job(uid)
        if job['state'] in ('success','failed','cancelled'):return job
        time.sleep(.04)
    raise AssertionError('Task timeout '+str(p.job(uid)))

def test_migration_and_existing_account_compatibility(service):
    p,_=service
    with auth_db(p.auth_path) as db:
        assert status(db,'portal_auth')['version']>=4
    with p.workspace('abcdef1234567890abcdef1234567890') as db:
        assert status(db,'portal_workspace')['version']==len(__import__('paper_radar.migrations',fromlist=['REGISTRIES']).REGISTRIES['portal_workspace'])
        assert db.execute("SELECT name FROM sqlite_master WHERE name='verified_evidence'").fetchone()

def test_full_multitenant_research_pipeline(service):
    p,port=service;a=Client(port);b=Client(port)
    u=register(a,'PipelineUser')['user']['id'];register(b,'AnotherUser')
    code,data=a.post('/api/public/research/start',{'topic_id':'default','fetch_pdf':True,'cloud_analysis':True})
    assert code==202,data
    j=finished(p,u);assert j['state']=='success',j
    assert j['result']['analyzed']==2,j
    assert j['result']['synthesis']['comparison_count']==1
    assert b.get('/api/public/research/report?topic_id=default')[1]['summary']['paper_count']==0
    assert a.get('/api/public/research/report?topic_id=default')[1]['summary']['finding_count']==2
    assert a.get('/api/public/fulltext?paper_id=2601.12345&topic_id=default')[1]['document']['state']=='indexed'
    assert b.get('/api/public/fulltext?paper_id=2601.12345&topic_id=default')[0]==400

def test_model_hallucination_rejected(service):
    p,_=service;u=create_user(p,'HallucinationUser')
    p.search(u,'default');save_pdf(p,u,'default','2601.12345',pdf_bytes())
    p.model_callable=lambda rows:json.dumps({'findings':[{'chunk_id':1,'kind':'metric',
        'dataset':'DatasetA','metric':'Accuracy','value':'99%','baseline':'BaselineX','setting':'split1',
        'quote':'Unrelated fabricated text 99%'}]})
    result=analyze(p,u,'default','2601.12345',cloud=True)
    assert result['evidence_count']==0 and not records(p,u,'default')

def create_user(portal,name):
    with auth_db(portal.auth_path) as db:return create_account(db,name,'a sufficiently long test password')['id']

def test_pdf_replacement_invalidates_evidence(service):
    p,_=service;u=create_user(p,'ReplaceUser')
    p.search(u,'default');save_pdf(p,u,'default','2601.12345',pdf_bytes())
    assert analyze(p,u,'default','2601.12345',cloud=True)['evidence_count']==1
    assert len(records(p,u,'default'))==1
    save_pdf(p,u,'default','2601.12345',pdf_bytes('Changed paper content'))
    assert not records(p,u,'default')
    assert synthesis(p,u,'default')['summary']['finding_count']==0

def test_model_cache_and_no_key_fallback(service):
    p,_=service;u=create_user(p,'CacheTester');p.search(u,'default')
    save_pdf(p,u,'default','2601.12345',pdf_bytes())
    calls=[];orig=p.model_callable
    p.model_callable=lambda rows:(calls.append(1) or orig(rows))
    assert analyze(p,u,'default','2601.12345',cloud=True)['cloud_analysis']=='verified'
    assert analyze(p,u,'default','2601.12345',cloud=True)['cloud_analysis']=='cached'
    assert len(calls)==1

def test_report_keeps_unverified_user_import(service):
    p,port=service;c=Client(port);register(c,'ImportTester')
    csv='论文ID,论文标题,评价指标,原文数值,PDF页码,原文引文,类型,数据集,实验条件\n'
    csv+='p1,Paper A,Accuracy,91%,1,"Benchmark results present Accuracy 91% for DatasetA split1",metric,DatasetA,split1\n'
    assert c.post('/api/public/import',{'topic_id':'default','csv':csv})[0]==200
    r=c.get('/api/public/research/report?topic_id=default')[1]['summary']
    assert r['diagnostics']['unverified']==1

def test_single_service_lock(service):
    p,_=service
    from paper_radar.public_portal import PortalError
    with pytest.raises(PortalError,match='already running'):
        make_server('127.0.0.1',0,Portal(p.root))

def test_queue_has_scoped_task_kind(service):
    p,_=service;u=create_user(p,'QueueTester')
    j=p.research_async(u,'default',kind='synthesize')
    assert j['kind']=='synthesize'
    item=finished(p,u)
    assert item['state']=='success' and item['result']['kind']=='synthesize'
    assert 'options_json' not in item

def test_pdf_rejects_invalid_bytes_and_cross_topic(service):
    p,_=service;u=create_user(p,'PdfGuard');p.search(u,'default')
    with pytest.raises(ValueError):save_pdf(p,u,'default','2601.12345',b'not a pdf'*500)
    with pytest.raises(ValueError):save_pdf(p,u,'default','unowned',pdf_bytes())


def test_public_acceptance_http_smoke(service):
    from tools.public_acceptance import inspect
    _,port=service
    report=inspect(f'http://127.0.0.1:{port}')
    assert report['ok'],report
    assert report['passed']==report['total']==7


def test_portal_backup_integrity(service,tmp_path):
    from tools.public_backup import backup,verify
    p,port=service
    u=create_user(p,'BackUser')
    p.search(u,'default')
    save_pdf(p,u,'default','2601.12345',pdf_bytes())
    result=backup(p.root,tmp_path/'backup_out')
    folder=Path(result['directory'])
    assert result['files']>=3
    assert verify(folder)['ok']
    pdf=next(folder.rglob('*.pdf'))
    pdf.write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='Checksum'):verify(folder)
