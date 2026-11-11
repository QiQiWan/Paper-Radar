"""RealPDF30 double-blind review, authentication and audited gold gate."""
import csv
import io
import json
import os
import sqlite3
import threading
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path

import pytest

from paper_radar import review_store as store
from paper_radar.review_webapp import ReviewServer

ROOT=Path(__file__).resolve().parents[1]
DATASET=ROOT/'benchmarks/real_pdf30'


@pytest.fixture
def frozen(tmp_path):
    """256 original tasks, but replace one PDF with a tiny SHA-pinned fixture."""
    import fitz
    src=DATASET
    dest=tmp_path/'dataset'
    (dest/'annotations').mkdir(parents=True)
    (dest/'tasks').mkdir()
    for rel in ('tasks/single_and_cross_paper_qa.jsonl','tasks/method_and_conflict_pairs.csv'):
        (dest/rel).write_bytes((src/rel).read_bytes())
    manifest=json.loads((src/'manifest.json').read_text(encoding='utf-8'))
    paper=manifest['papers'][0]
    text='The numerical RMSE on Dataset A was 0.0086 for FNO, evaluated at 64 by 64 grid resolution.'
    pdf=fitz.open()
    for i in range(4):
        page=pdf.new_page()
        page.insert_text((72,92),text if i==2 else f'Page {i+1} of the synthetic immutable fixture')
    target=tmp_path/'originals'/Path(paper['pdf_file']).name
    target.parent.mkdir()
    pdf.save(target)
    pdf.close()
    import hashlib
    paper.update(pdf_bytes=target.stat().st_size,sha256=hashlib.sha256(target.read_bytes()).hexdigest(),pages=4)
    (dest/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    with (src/'annotations/candidate_evidence.csv').open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f));fields=list(rows[0])
    for row in rows:
        if row['paper_id']==paper['paper_id']:
            row['pdf_sha256']=paper['sha256']
    with (dest/'annotations/candidate_evidence.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    creds=store.initialize(tmp_path/'audit.sqlite',dest)['credentials_one_time']
    return dest,target.parent,tmp_path/'audit.sqlite',paper['paper_id'],creds,text


def label(text):
    return dict(label='valid_experiment',reason='Verified the corresponding method, metric and value directly from the original PDF.',
                citation_page=3,citation_quote=text,metric='RMSE',value='0.0086',method='FNO',
                dataset='Dataset A',setting='64 by 64 grid',unit='',baseline='')


def task_id(db):
    return db.execute("SELECT task_id FROM tasks WHERE kind='evidence' AND json_extract(payload,'$.paper_a')='2210.07182' ORDER BY task_id LIMIT 1").fetchone()[0]


def test_frozen_30_paper_corpus_contains_256_distinct_tasks():
    papers,digest,tasks=store.load_frozen_dataset(DATASET)
    assert len(papers['papers'])==30
    assert len(tasks)==256 and len({t[0] for t in tasks})==256
    assert {kind:sum(x[1]==kind for x in tasks) for kind in ('evidence','question','method','conflict')}=={
        'evidence':117,'question':88,'method':34,'conflict':17}
    assert len(digest)==64
    cross=[payload for _,kind,payload in tasks if kind=='method']
    assert sum(x['suggested_page_a']>0 and x['suggested_page_b']>0 for x in cross)>0


def test_double_blind_submissions_are_immutable_and_adjudicated(frozen,tmp_path):
    dataset,pdf_dir,path,pid,credentials,quote=frozen
    with closing(store.connect(path)) as db:
        assert store.verify_frozen_corpus(db,dataset)
        assert store.progress(db)['tasks']==256
        assert not store.progress(db)['official_gold_complete']
        assert store.verify_password(db,'A',credentials['A'])
        assert not store.verify_password(db,'B',credentials['A'])
        key=task_id(db)
        assert 'reviews' not in store.view_task(db,'A',key)
        with pytest.raises(PermissionError):store.view_task(db,'unknown',key)
        assert 'metric_candidate' not in store.view_task(db,'A',key)['task']['candidate']
        with pytest.raises(ValueError,match='Two independent'):
            store.adjudicate(db,'adjudicator',key,label(quote),dataset=dataset,pdf_dir=pdf_dir)
        store.submit(db,'A',key,label(quote),dataset=dataset,pdf_dir=pdf_dir)
        assert store.view_task(db,'B',key)['my_submission'] is None
        with pytest.raises(ValueError,match='already submitted'):
            store.submit(db,'A',key,label(quote),dataset=dataset,pdf_dir=pdf_dir)
        with pytest.raises(ValueError,match='Two independent'):
            store.adjudicate(db,'adjudicator',key,label(quote),dataset=dataset,pdf_dir=pdf_dir)
        store.submit(db,'B',key,label(quote),dataset=dataset,pdf_dir=pdf_dir)
        assert store.view_task(db,'adjudicator',key)['ready_for_adjudication'] is True
        assert set(store.view_task(db,'adjudicator',key)['reviews'])=={'A','B'}
        assert 'reviews' not in store.view_task(db,'A',key)
        store.adjudicate(db,'adjudicator',key,label(quote),dataset=dataset,pdf_dir=pdf_dir)
        with pytest.raises(ValueError,match='immutable'):
            store.adjudicate(db,'adjudicator',key,label(quote),dataset=dataset,pdf_dir=pdf_dir)
        assert store.progress(db)['adjudicated']==1
        assert store.check_storage_integrity(db)['audit_entries']==4
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            db.execute('UPDATE submissions SET label_json=? WHERE task_id=?',('{}',key))
        report=store.export_audit(db,tmp_path/'report.json',dataset=dataset,pdf_dir=pdf_dir)
        assert report['adjudicated']==1 and not report['official_gold_complete']
        with pytest.raises(ValueError,match='not complete'):
            store.export_gold_csv(db,dataset,tmp_path/'formal_gold',pdf_dir=pdf_dir)



def test_direct_database_inserts_cannot_fake_audited_human_reviews(frozen):
    dataset,pdf_dir,path,pid,creds,quote=frozen
    with closing(store.connect(path)) as db:
        key=task_id(db)
        raw=store._json(store.validate_label('evidence',{'paper_b':''},label(quote)))
        db.execute('INSERT INTO submissions VALUES (?,?,?,?,?)',
                   (key,'A',raw,store._sha(raw),store._utc()))
        with pytest.raises(ValueError,match='chained audit event'):
            store.check_storage_integrity(db)
        with pytest.raises(ValueError,match='chained audit event'):
            store.export_audit(db,path.with_suffix('.audit.json'),dataset=dataset,pdf_dir=pdf_dir)


def test_original_pdf_sha_and_page_quotes_are_required(frozen):
    dataset,pdf_dir,path,pid,credentials,quote=frozen
    with closing(store.connect(path)) as db:
        key=task_id(db)
        invalid=label(quote);invalid['citation_quote']='Another paper claims a fabricated improvement of 99.99 percent.'
        with pytest.raises(ValueError,match='does not match'):
            store.submit(db,'A',key,invalid,dataset=dataset,pdf_dir=pdf_dir)
        invalid=label(quote);invalid['citation_page']=5
        with pytest.raises(ValueError,match='outside'):
            store.submit(db,'A',key,invalid,dataset=dataset,pdf_dir=pdf_dir)
        pdf=(pdf_dir/(pid+'v1.pdf'))
        pdf.write_bytes(pdf.read_bytes()+b'changed')
        with pytest.raises(ValueError,match='SHA-256'):
            store.submit(db,'A',key,label(quote),dataset=dataset,pdf_dir=pdf_dir)


def test_review_http_role_isolation_cookie_and_csrf(frozen):
    dataset,pdf_dir,path,pid,creds,quote=frozen
    server=ReviewServer(('127.0.0.1',0),dataset=dataset,db_file=path,pdf_dir=pdf_dir)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def send(method,url,data=None,cookie=None,extra=None):
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=15)
        headers={'Host':f'127.0.0.1:{server.server_port}'}
        if data is not None:
            headers.update({'Content-Type':'application/json','X-PaperRadar-Review':'1'})
        if cookie:headers['Cookie']=cookie
        if extra:headers.update(extra)
        conn.request(method,url,body=json.dumps(data).encode('utf-8') if data is not None else None,headers=headers)
        r=conn.getresponse();status=r.status;blob=r.read();cookies=r.getheader('Set-Cookie');conn.close()
        try:payload=json.loads(blob)
        except ValueError:payload=blob
        return status,payload,cookies
    try:
        assert send('GET','/api/tasks')[0]==401
        assert send('GET','/api/pdf?paper_id='+pid)[0]==401
        assert send('POST','/api/login',{'username':'A','password':creds['A']},extra={'X-PaperRadar-Review':'0'})[0]==403
        assert send('POST','/api/login',{'username':'A','password':creds['B']})[0]==401
        st,response,cookie=send('POST','/api/login',{'username':'A','password':creds['A']})
        assert st==200 and 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
        key='E:P221007182-1'
        assert send('GET','/api/task?id='+key,cookie=cookie)[0]==200
        st,body,_=send('POST','/api/submit',{'task_id':key,'label':label(quote)},cookie=cookie)
        assert st==201,(st,body)
        assert send('POST','/api/adjudicate',{'task_id':key,'label':label(quote)},cookie=cookie)[0]==422
        assert send('POST','/api/submit',{'task_id':key,'label':label(quote)},cookie=cookie,
                    extra={'Origin':'https://example.net'})[0]==403
        assert send('GET','/api/pdf?paper_id='+pid,cookie=cookie)[0]==200
        st,body,other=send('POST','/api/login',{'username':'B','password':creds['B']})
        assert st==200
        assert send('GET','/api/task?id='+key,cookie=other)[1]['my_submission'] is None
        assert send('POST','/api/submit',{'task_id':key,'label':label(quote)},cookie=other)[0]==201
        st,body,admin=send('POST','/api/login',{'username':'adjudicator','password':creds['adjudicator']})
        assert st==200
        assert set(send('GET','/api/task?id='+key,cookie=admin)[1]['reviews'])=={'A','B'}
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def test_workbench_static_scripts_and_root_launch_contract():
    root=ROOT
    scripts=sorted(x.name for x in root.iterdir() if x.suffix in ('.bat','.sh'))
    assert scripts==['start_macos_linux.sh','start_windows.bat']
    for name in ('review.html','review.css','review.js'):
        assert (root/'paper_radar/static'/name).is_file()
    source=(root/'paper_radar/cli_launcher.py').read_text(encoding='utf-8')
    assert all(arg in source for arg in ('--review-init','--review','--review-status','--review-gold','--public','--worker'))


def test_legacy_root_cleanup_preserves_user_edits(tmp_path):
    from tools.cleanup_legacy_root_scripts import cleanup,KNOWN_SHA256
    import zipfile
    original=Path('/mnt/data/Paper-Radar_v1.45.4_Complete_GitHub_Root.zip')
    # The actual release is not required on a fresh CI runner; synthesize
    # only the exact bytes that match the shipped fixed SHA.
    if original.exists():
        with zipfile.ZipFile(original) as z:
            name='CHECK_RETRIEVAL_WINDOWS.bat'
            good=z.read(name)
        assert __import__('hashlib').sha256(good).hexdigest()==KNOWN_SHA256[name]
        (tmp_path/name).write_bytes(good)
        name2='start_public_windows.bat'
        (tmp_path/name2).write_text('REM locally modified by user',encoding='utf-8')
        before=cleanup(tmp_path)
        assert name in before['would_delete']
        assert name2 in before['modified_preserved']
        after=cleanup(tmp_path,apply=True)
        assert name in after['deleted']
        assert name2 in after['modified_preserved']
        assert (tmp_path/name2).is_file()
    else:
        assert len(KNOWN_SHA256)>=10
        assert cleanup(tmp_path,apply=True)['deleted']==[]


def test_uniform_cli_preserves_original_desktop_instance_launcher():
    from paper_radar.launcher import health,main
    from paper_radar.cli_launcher import main as cli_main
    assert callable(health) and callable(main)
    assert cli_main(['--help']) == 0
    assert cli_main(['--unsupported']) == 2


def test_pdf_worker_limits_apply_to_subprocess_entrypoint_only(monkeypatch, tmp_path):
    from paper_radar import portal_pdf_worker, desktop_app
    fitz=__import__('fitz')
    document=fitz.open()
    document.new_page().insert_text((72,72),'PDF worker subprocess isolation safety test')
    path=tmp_path/'test.pdf'
    document.save(path)
    document.close()
    def forbid_limit():
        raise AssertionError('parse() changed limits of the calling process')
    monkeypatch.setattr(portal_pdf_worker,'_limit_process',forbid_limit)
    assert portal_pdf_worker.parse(path)['total_pages']==1
    # Desktop frozen executable's --pdf-child must apply limits before parse.
    calls=[]
    monkeypatch.setattr(portal_pdf_worker,'_limit_process',lambda:calls.append('limit'))
    monkeypatch.setattr(portal_pdf_worker,'parse',lambda path:({'total_pages':1,'pages':[]}))
    assert desktop_app.main(['--pdf-child',str(path)])==0
    assert calls==['limit']
