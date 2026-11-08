"""v1.32: account-scoped human reading, atomic batch, CAS, and migration regression."""
from __future__ import annotations

import csv
from io import StringIO
import json
import sqlite3
from pathlib import Path
from threading import Thread

import pytest

from paper_radar.core import connect, save_papers, list_library, count_library
from paper_radar.local_triage import (bulk_flags as local_bulk, read_notes, save_note as local_save,
                                      export_reading as local_export)
from paper_radar.portal_triage import (NoteConflict, bulk_flags as public_bulk,
                                       save_note as public_save, export_reading as public_export,
                                       _safe_link)
from paper_radar.migrations import REGISTRIES, migrate, MigrationError, status
from paper_radar.models import Paper
from paper_radar.public_portal import Portal, make_server
from test_v116_portal_and_synthesis import Client, register


@pytest.fixture
def web(tmp_path):
    p=Portal(tmp_path/'data',embedded_workers=False)
    server=make_server('127.0.0.1',0,p)
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:yield p,server.server_port
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def seed(p,uid,topic='default',titles=None):
    titles=titles or ('First study','Second study','Third study')
    with p.workspace(uid) as db:
        for i,title in enumerate(titles):
            pid=f'v132:{i}'
            db.execute('''INSERT OR IGNORE INTO papers (id,title,abstract,published,doi,url,imported_at)
                VALUES(?,?,?,?,?,?,?)''',(pid,title,'Abstract','2026-11-04',
                '10.9999/test'+str(i),'https://arxiv.org/abs/1234.'+str(1000+i),'2026-11-04'))
            db.execute('INSERT OR IGNORE INTO topic_papers(topic_id,paper_id) VALUES (?,?)',(topic,pid))


def note_payload(pid,revision=0,note='研究笔记',action='read',topic='default'):
    return {'topic_id':topic,'paper_id':pid,'note':note,
            'next_action':action,'expected_revision':revision}


def test_public_end_to_end_inbox_notes_action_export_and_persistence(web):
    p,port=web;u=Client(port);user=register(u,'TriageReaderOne')
    uid=user['user']['id'];seed(p,uid)
    r=u.get('/api/public/dashboard?topic_id=default')
    assert r[0]==200,r
    assert r[1]['inbox']==3 and r[1]['next_actions']==0
    assert u.get('/api/public/papers?topic_id=default&filter=inbox')[1]['total']==3
    code,result=u.post('/api/public/papers/note',note_payload('v132:0',note='读过一遍\n关注方法',action='replicate'))
    assert code==200,result
    assert result['note_revision']==1
    assert u.get('/api/public/dashboard?topic_id=default')[1]['inbox']==2
    assert u.get('/api/public/dashboard?topic_id=default')[1]['next_actions']==1
    code,result=u.post('/api/public/papers/note',note_payload('v132:0',revision=1,note='更新为二稿',action='cite'))
    assert code==200 and result['note_revision']==2
    code,data=u.post('/api/public/papers/note',note_payload('v132:0',revision=1,note='过期稿'))
    assert code==409 and data['code']=='NOTE_CONFLICT'
    assert u.get('/api/public/papers?topic_id=default&filter=actionable')[1]['total']==1
    code,result=u.post('/api/public/papers/bulk',{'topic_id':'default','paper_ids':['v132:1','v132:2'],'action':'star'})
    assert code==200 and result['updated']==2
    code,result=u.post('/api/public/papers/bulk',{'topic_id':'default','paper_ids':['v132:2'],'action':'skip'})
    assert code==200 and result['updated']==1
    assert u.get('/api/public/papers?topic_id=default&filter=starred')[1]['total']==1
    assert u.get('/api/public/papers?topic_id=default&filter=skipped')[1]['total']==1
    code,md=u.get('/api/public/reading/export?topic_id=default&scope=actionable&format=md')
    assert code==200
    assert '更新为二稿' in md['raw'].decode('utf-8')
    assert '准备引用' in md['raw'].decode('utf-8')
    code,csv_data=u.get('/api/public/reading/export?topic_id=default&scope=starred&format=csv')
    assert code==200
    rows=list(csv.reader(StringIO(csv_data['raw'].decode('utf-8-sig'))))
    assert len(rows)==2 and rows[1][0]=='v132:1'
    with p.workspace(uid) as db:
        assert db.execute('SELECT note,next_action,revision FROM paper_notes WHERE paper_id=?',('v132:0',)).fetchone()[:]==('更新为二稿','cite',2)
    assert u.get('/api/public/papers?topic_id=default&paper_id=v132%3A0')[1]['papers'][0]['note_revision']==2


def test_public_isolation_and_batch_atomicity(web):
    p,port=web;a=Client(port);b=Client(port)
    ai=register(a,'IsolatedWriter1');bi=register(b,'IsolatedWriter2')
    seed(p,ai['user']['id']);seed(p,bi['user']['id'],titles=('Other study',))
    code,topic=a.post('/api/public/topics',{'name':'另一组','keywords':['physics']})
    assert code==201
    other=topic['id']
    assert a.post('/api/public/papers/bulk',{'topic_id':other,'paper_ids':['v132:0'],'action':'done'})[0]==400
    assert b.post('/api/public/papers/bulk',{'topic_id':other,'paper_ids':['v132:0'],'action':'done'})[0]==400
    assert b.post('/api/public/papers/note',note_payload('v132:0',topic=other))[0]==400
    assert b.get('/api/public/reading/export?topic_id='+other)[0]==400
    code,_=a.post('/api/public/papers/bulk',{'topic_id':'default','paper_ids':['v132:0','inexistent'],'action':'done'})
    assert code==400
    assert a.get('/api/public/papers?topic_id=default')[1]['papers'][0]['reading_status']=='unread'
    assert a.post('/api/public/papers/bulk',{'topic_id':'default','paper_ids':['v132:0','v132:0'],'action':'star'})[0]==400
    assert a.post('/api/public/papers/bulk',{'topic_id':'default','paper_ids':['v132:0']*101,'action':'star'})[0]==400
    assert a.post('/api/public/papers/note',note_payload('v132:0',note='x'*1601))[0]==400
    assert a.post('/api/public/papers/note',note_payload('v132:0',action='unknown'))[0]==400
    assert a.post('/api/public/papers/note',note_payload('v132:0'),csrf=False)[0]==403
    assert a.post('/api/public/papers/note',note_payload('v132:0'),origin='https://malicious.test')[0]==403
    assert a.post('/api/public/papers/note',note_payload('v132:0'))[0]==200
    assert b.get('/api/public/papers?topic_id=default&paper_id=v132%3A0')[1]['papers'][0]['note']==''


def test_csv_formula_injection_and_md_link_sanitized(web):
    p,port=web;a=Client(port);acc=register(a,'TriageInjectionTester')
    seed(p,acc['user']['id'],titles=('=HYPERLINK("https://evil.example")',))
    with p.workspace(acc['user']['id']) as db:
        db.execute('UPDATE papers SET url=? WHERE id=?',('https://example.org/safe\n]evil(link)','v132:0'))
    assert a.post('/api/public/papers/note',note_payload('v132:0',note='@SUM(1,1)\nnext line'))[0]==200
    code,data=a.get('/api/public/reading/export?topic_id=default&scope=all&format=csv')
    assert code==200
    parsed=list(csv.reader(StringIO(data['raw'].decode('utf-8-sig'))))
    assert parsed[1][1].startswith("'=")
    assert parsed[1][-2].startswith("'@")
    code,data=a.get('/api/public/reading/export?topic_id=default&scope=all&format=md')
    assert code==200
    text=data['raw'].decode('utf-8')
    assert '[查看论文原文]' not in text
    assert 'HYPERLINK' in text
    assert _safe_link('javascript:alert(1)')==''
    assert _safe_link('https://example.org\n]attack')==''


def test_local_full_workflow_and_topic_filter(tmp_path):
    path=tmp_path/'local.sqlite3'
    def paper(i):
        return Paper(paper_id=f'loc:{i}',title=f'Local {i}',abstract='abstract',authors='A',
            published='2026-10-01',updated='2026-10-02',category='cs.LG',
            url='https://example.org',pdf_url='',doi='')
    with connect(path) as db:
        save_papers(db,[paper(i) for i in range(5)])
        assert count_library(db,topic_id='default',topic_only=True,triage_filter='inbox')==5
        x=local_save(db,'default','loc:0','Human notes','replicate',0)
        assert x['note_revision']==1
        assert count_library(db,topic_id='default',topic_only=True,triage_filter='inbox')==4
        assert [x.paper_id for x in list_library(db,topic_id='default',topic_only=True,triage_filter='actionable')]==['loc:0']
        with pytest.raises(NoteConflict):local_save(db,'default','loc:0','Old draft','read',0)
        local_bulk(db,'default',['loc:1','loc:2'],'star')
        with pytest.raises(ValueError):local_bulk(db,'default',['loc:2','absent'],'skip')
        assert count_library(db,topic_id='default',topic_only=True,only_starred=True)==2
        assert count_library(db,topic_id='default',topic_only=True,triage_filter='skipped')==0
        local_bulk(db,'default',['loc:2'],'skip')
        assert count_library(db,topic_id='default',topic_only=True,triage_filter='skipped')==1
        raw,_,_=local_export(db,'default','actionable','md')
        assert 'Human notes' in raw.decode('utf-8')
        with pytest.raises(ValueError):count_library(db,topic_id='default',topic_only=False,triage_filter='inbox')
        with pytest.raises(ValueError):local_save(db,'default','ghost','', 'none',0)
    with connect(path) as db:
        assert read_notes(db,'default',['loc:0'])['loc:0']['note_revision']==1
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert status(db,'research')['version']==len(REGISTRIES['research'])


def test_v9_to_v10_migration_keeps_flags_notes_and_no_downgrade(tmp_path,monkeypatch):
    full=REGISTRIES['portal_workspace']
    path=tmp_path/'from_v9.sqlite3'
    monkeypatch.setitem(REGISTRIES,'portal_workspace',full[:9])
    with sqlite3.connect(path) as db:
        assert migrate(db,'portal_workspace')==9
        db.execute("INSERT INTO papers VALUES ('legacy','Archived evidence','','2026-09-01','','','2026-09-01')")
        db.execute("INSERT INTO topic_papers VALUES ('default','legacy')")
        db.execute("INSERT INTO topic_paper_flags VALUES ('default','legacy',1,'done',1,'2026-10-01')")
        db.commit()
    monkeypatch.setitem(REGISTRIES,'portal_workspace',full)
    with sqlite3.connect(path) as db:
        assert migrate(db,'portal_workspace')==len(full)
        assert migrate(db,'portal_workspace')==len(full)
        assert db.execute('SELECT reading_status FROM topic_paper_flags').fetchone()[0]=='done'
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert 'idx_paper_notes_actions' in {r[1] for r in db.execute("PRAGMA index_list('paper_notes')")}
    monkeypatch.setitem(REGISTRIES,'portal_workspace',full[:9])
    with sqlite3.connect(path) as db:
        with pytest.raises(MigrationError,match='newer'):migrate(db,'portal_workspace')


def test_triage_static_UI_and_docs_exist():
    root=Path(__file__).resolve().parents[1]
    pub=(root/'paper_radar/static/portal.js').read_text()
    loc=(root/'paper_radar/static/app.js').read_text()
    assert 'api/public/papers/bulk' in pub and 'api/public/papers/note' in pub
    assert 'api/papers/bulk' in loc and 'api/paper/note' in loc
    assert 'local-reading-note' in loc and 'appendPaperNote' in pub
    assert 'id="localBulkStar"' in (root/'paper_radar/static/index.html').read_text()
    assert 'id="triageSelectedCount"' in (root/'paper_radar/static/portal.html').read_text()
    links=(root/'paper_radar/static/reading_links.js').read_text()
    assert "'inbox','actionable','skipped'" in links
    assert "library:'triage'" in links


def test_new_queue_filters_survive_canonical_url_roundtrip():
    from test_v1291_reading_state import _node
    for view in ('inbox','actionable','skipped'):
        result=_node(f"context.result={{url:fake.PRReading.link('public','library',{{filter:'{view}',topic:'default'}})}};")
        assert 'filter='+view in result['url']


def test_desktop_http_routes_notes_batches_scopes_and_errors(tmp_path,monkeypatch):
    import http.client
    from http.server import ThreadingHTTPServer
    from paper_radar import webapp
    dbfile=tmp_path/'desktop.sqlite3'
    with connect(dbfile) as db:
        save_papers(db,[Paper(paper_id='local:one',title='An experimental local paper',
            abstract='Local text',authors='Researcher',published='2026-11-01',
            updated='2026-11-01',category='cs.LG',url='https://arxiv.org/abs/1234.999',
            pdf_url='',doi='')])
    monkeypatch.setattr(webapp,'open_database',lambda:connect(dbfile))
    monkeypatch.setattr(webapp,'read_config',lambda:{'active_topic_id':'default',
        'topics':[{'id':'default','name':'默认研究主题'}]})
    server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
    server.daemon_threads=True
    server.token='v132-test-local-token'
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        def send(path,payload=None):
            con=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=4)
            method='GET' if payload is None else 'POST'
            headers={'Host':f'127.0.0.1:{server.server_port}'}
            if payload is not None:
                headers.update({'Content-Type':'application/json','X-Local-Token':server.token})
            con.request(method,path,body=None if payload is None else json.dumps(payload),headers=headers)
            response=con.getresponse()
            status=response.status;raw=response.read();con.close()
            try:data=json.loads(raw)
            except ValueError:data=raw
            return status,data
        assert send('/api/papers?scope=topic&triage=inbox')[1]['total']==1
        code,first=send('/api/paper/note',{'id':'local:one','note':'本地研究判断',
            'next_action':'read','expected_revision':0})
        assert code==200,first
        assert first['note_revision']==1
        assert send('/api/paper/note',{'id':'local:one','note':'过期稿',
            'next_action':'read','expected_revision':0})[0]==409
        assert send('/api/papers?scope=topic&triage=actionable')[1]['total']==1
        assert send('/api/papers?scope=topic&triage=inbox')[1]['total']==0
        assert send('/api/papers?scope=all&triage=actionable')[0]==400
        code,info=send('/api/papers/bulk',{'paper_ids':['local:one'],'action':'star'})
        assert code==200 and info['updated']==1
        assert send('/api/papers/bulk',{'paper_ids':['local:one','bad'],'action':'done'})[0]==400
        code,md=send('/api/papers/reading-export?scope=actionable&format=md')
        assert code==200 and '本地研究判断' in md.decode('utf-8')
        assert send('/api/papers/reading-export?scope=invalid')[0]==400
    finally:
        server.shutdown();server.server_close();thread.join(timeout=4)
