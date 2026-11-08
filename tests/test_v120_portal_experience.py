"""Paper-Radar v1.20: multi-user library UX with persisted reading metadata."""
import json
import sqlite3
from pathlib import Path
from threading import Thread
import pytest

from paper_radar import __version__
from paper_radar.migrations import REGISTRIES, status
from paper_radar.portal_auth import db as auth_db,create_account
from paper_radar.public_portal import Portal, make_server
from test_v116_portal_and_synthesis import Client,register


@pytest.fixture
def service(tmp_path):
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    server=make_server('127.0.0.1',0,portal)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield portal,server.server_port
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def seed(portal,uid,topic='default'):
    with portal.workspace(uid) as db:
        for pid,title,published,abstract in [
            ('a','Fourier Neural Operator','2026-08-01','Numerical error in Darcy PDE'),
            ('b','Physics Neural Operator','2025-07-03','Mesh PDE solver'),
            ('c','Graph FNO','2024-02-02','Darcy error')]:
            db.execute('INSERT OR IGNORE INTO papers VALUES (?,?,?,?,?,?,?)',
                (pid,title,abstract,published,'https://arxiv.org/abs/'+pid,'10.1/'+pid,'now'))
            db.execute('INSERT OR IGNORE INTO topic_papers VALUES (?,?)',(topic,pid))


def test_schema_is_append_only_and_compatible(tmp_path):
    assert __version__==__import__('tools.packaging.release_version',fromlist=['read_version']).read_version()
    p=Portal(tmp_path/'data');uid='c'*32
    with p.workspace(uid) as db:
        assert status(db,'portal_workspace')['version']==len(REGISTRIES['portal_workspace'])
        assert db.execute('SELECT name FROM sqlite_master WHERE name="topic_paper_flags"').fetchone()
        db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',('p','Paper','','','','','now'))
        db.execute('INSERT INTO topic_papers VALUES (?,?)',('default','p'))
        assert db.execute('SELECT COUNT(*) FROM topic_papers').fetchone()[0]==1


def test_dashboard_persisted_star_read_and_feedback(service):
    p,port=service;a=Client(port);info=register(a,'ReaderOne');uid=info['user']['id']
    seed(p,uid)
    code,d=a.get('/api/public/dashboard?topic_id=default')
    assert code==200,d
    assert (d['papers'],d['starred'],d['read'],d['reports'])==(3,0,0,0)
    code,result=a.post('/api/public/papers/flags',{'topic_id':'default','paper_id':'a',
        'is_starred':True,'reading_status':'done','feedback':1})
    assert code==200,result
    assert result['is_starred'] is True and result['feedback']==1
    code,listed=a.get('/api/public/papers?topic_id=default&filter=starred')
    assert code==200,listed
    assert listed['total']==1 and listed['papers'][0]['id']=='a'
    assert listed['papers'][0]['reading_status']=='done'
    assert a.get('/api/public/dashboard?topic_id=default')[1]['read']==1
    with p.workspace(uid) as db:
        assert db.execute('SELECT reading_status FROM topic_paper_flags WHERE paper_id="a"').fetchone()[0]=='done'


def test_server_side_search_sort_filter_and_escaped_wildcards(service):
    p,port=service;a=Client(port);uid=register(a,'ReaderTwo')['user']['id'];seed(p,uid)
    assert a.get('/api/public/papers?topic_id=default&q=darcy')[1]['total']==2
    assert a.get('/api/public/papers?topic_id=default&q=%25')[1]['total']==0
    assert a.get('/api/public/papers?topic_id=default&q=%5f')[1]['total']==0
    assert [x['id'] for x in a.get('/api/public/papers?sort=oldest')[1]['papers']]==['c','b','a']
    assert [x['id'] for x in a.get('/api/public/papers?sort=title')[1]['papers']]==['a','c','b']
    for invalid in ['sort=some-sql','filter=invalid','q='+('x'*121)]:
        assert a.get('/api/public/papers?'+invalid)[0]==400


def test_cross_account_and_cross_topic_flags_isolation(service):
    p,port=service;a,b=Client(port),Client(port)
    u1=register(a,'ReaderThree')['user']['id'];u2=register(b,'ReaderFour')['user']['id']
    seed(p,u1);seed(p,u2)
    with p.workspace(u1) as db:
        db.execute('INSERT INTO topics VALUES (?,?,?,?)',('another','Second',json.dumps(['FNO']),'now'))
        db.execute('INSERT INTO topic_papers VALUES (?,?)',('another','a'))
    assert a.post('/api/public/papers/flags',{'topic_id':'default','paper_id':'a','is_starred':True})[0]==200
    assert a.get('/api/public/papers?topic_id=another&filter=starred')[1]['total']==0
    assert b.get('/api/public/dashboard')[1]['starred']==0
    assert b.post('/api/public/papers/flags',{'topic_id':'another','paper_id':'a','is_starred':True})[0]==400
    assert a.post('/api/public/papers/flags',{'topic_id':'default','paper_id':'nonexistent','is_starred':True})[0]==404


def test_flags_input_validation_and_csrf(service):
    p,port=service;a=Client(port);uid=register(a,'ReaderFive')['user']['id'];seed(p,uid)
    base={'topic_id':'default','paper_id':'a'}
    for invalid in [{'is_starred':1},{'reading_status':'hacked'},{'feedback':True},
                    {'feedback':4},{'unknown':'nope'},{'is_starred':True,'unknown':'nope'}]:
        assert a.post('/api/public/papers/flags',{**base,**invalid})[0]==400
    assert a.post('/api/public/papers/flags',{**base,'feedback':1},csrf=False)[0]==403
    assert a.post('/api/public/papers/flags',{**base,'feedback':1},origin='https://evil.example')[0]==403
    assert a.post('/api/public/papers/flags',{**base,'feedback':-1})[1]['feedback']==-1
    assert a.get('/api/public/papers?filter=liked')[1]['total']==0
    assert a.post('/api/public/papers/flags',{**base,'feedback':1})[0]==200
    assert a.get('/api/public/papers?filter=liked')[1]['total']==1


def test_frontend_nav_and_semantic_controls():
    root=Path(__file__).parents[1]/'paper_radar/static'
    html=(root/'portal.html').read_text(encoding='utf-8')
    js=(root/'portal.js').read_text(encoding='utf-8')
    css=(root/'portal.css').read_text(encoding='utf-8')
    for view in ['workspace','library','tasks','reports','account']:
        assert f'data-view="{view}"' in html and f'data-page="{view}"' in html
    assert "window.addEventListener('popstate',applyRoute)" in js
    assert "window.addEventListener('hashchange',applyRoute)" in js
    assert 'aria-current' in js and 'aria-pressed' in js
    assert 'paperSearch' in html and 'paperFilter' in html and 'paperSort' in html
    assert 'topic_paper_flags' in (Path(__file__).parents[1]/'paper_radar/migrations.py').read_text()
    assert 'position:fixed' in css or 'position:fixed' in css.replace(' ','')


def test_local_task_center_and_advanced_tools_present():
    root=Path(__file__).parents[1]/'paper_radar/static'
    html=(root/'index.html').read_text(encoding='utf-8')
    js=(root/'app.js').read_text(encoding='utf-8')
    import re
    task_section=re.search(r'<section[^>]*\bid="tasks"[^>]*>',html)
    assert task_section and 'view' in task_section.group(0)
    assert 'id="taskViewHistory"' in html
    assert 'id="localExtraTools"' in html
    assert 'data-view="zotero"' in html and 'data-view="downloads"' in html
    assert "if(name==='tasks')loadTaskView()" in js
    assert "hook('taskViewRefresh',loadTaskView)" in js


def test_upgrade_existing_v3_workspace_preserves_papers_reports_and_flags(tmp_path, monkeypatch):
    """A v1.19.1 workspace upgrades without losing any research snapshots."""
    import paper_radar.migrations as migrations
    uid = 'e' * 32
    root = tmp_path / 'portal'
    original = migrations.REGISTRIES['portal_workspace']
    monkeypatch.setitem(migrations.REGISTRIES, 'portal_workspace', original[:3])
    portal = Portal(root, embedded_workers=False)
    with portal.workspace(uid) as db:
        assert status(db, 'portal_workspace')['version'] == 3
        db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',
                   ('legacy-p', 'Prior paper', 'Prior abstract', '2025-01-01',
                    'https://example.com/legacy', '10.1/legacy', '2025-02-02'))
        db.execute('INSERT INTO topic_papers VALUES (?,?)', ('default', 'legacy-p'))
        db.execute('''INSERT INTO research_reports
            (id,topic_id,job_id,generated_at,schema_version,source_digest,
             paper_count,finding_count,summary_json,markdown)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            ('legacy-report', 'default', None, 1000, 1, 'old-digest', 1, 0,
             '{}', '# Previously saved report'))
    monkeypatch.setitem(migrations.REGISTRIES, 'portal_workspace', original)
    with portal.workspace(uid) as db:
        assert status(db, 'portal_workspace')['version'] == len(REGISTRIES['portal_workspace'])
        assert db.execute('SELECT title FROM papers WHERE id=?',
                          ('legacy-p',)).fetchone()[0] == 'Prior paper'
        assert db.execute('SELECT markdown FROM research_reports WHERE id=?',
                          ('legacy-report',)).fetchone()[0] == '# Previously saved report'
        db.execute('INSERT INTO topic_paper_flags(topic_id,paper_id,is_starred,reading_status,feedback,updated_at) '
                   'VALUES (?,?,?,?,?,?)', ('default', 'legacy-p', 1, 'reading', 0, 'now'))
        assert db.execute('SELECT COUNT(*) FROM topic_paper_flags').fetchone()[0] == 1


def test_release_keeps_independent_worker_launchers():
    from tools.package_release import include
    for filename in ('start_public_macos_linux.sh','start_public_windows.bat',
                     'start_public_worker_macos_linux.sh','start_public_worker_windows.bat'):
        assert include(Path(filename)), filename


def test_signed_updater_accepts_independent_worker_launchers():
    from paper_radar.startup_update import _safe_member
    for filename in ('start_public_worker_windows.bat','start_public_worker_macos_linux.sh'):
        assert _safe_member('Paper-Radar/'+filename, 'Paper-Radar') == filename


def test_release_includes_real_paper_evaluation_seed():
    from tools.package_release import include
    from paper_radar.startup_update import _safe_member
    target=Path('benchmarks/real_papers_v1/manifest.json')
    assert target.is_file()
    assert include(target)
    assert _safe_member('Paper-Radar/'+target.as_posix(),'Paper-Radar')==target.as_posix()
