"""HTTP account isolation, CSRF, local synthesis and safe deployment tests."""
from __future__ import annotations
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import sqlite3
from threading import Thread
from urllib.parse import urlencode

import pytest
from paper_radar.public_portal import Portal,make_server
from paper_radar.portal_auth import db as auth_db, create_account, AuthError
from paper_radar.synthesis import compare_records,to_markdown
from paper_radar.agents.research.synthesis import run_synthesis_pipeline
from paper_radar.core import connect,DEFAULTS
from paper_radar.models import Paper


class Client:
    def __init__(self,port):
        self.port=port;self.origin=f'http://127.0.0.1:{port}'
        self.cookie='';self.csrf=''
    def get(self,path):return self.request('GET',path)
    def post(self,path,data,*,csrf=True,origin=None,action=True):
        return self.request('POST',path,data,csrf=csrf,origin=origin,action=action)
    def request(self,method,path,data=None,*,csrf=True,origin=None,action=True):
        headers={'Host':f'127.0.0.1:{self.port}'}
        if self.cookie:headers['Cookie']=self.cookie
        content=None
        if method=='POST':
            headers['Content-Type']='application/json'
            headers['Origin']=self.origin if origin is None else origin
            if action:headers['X-Portal-Action']='1'
            if csrf:headers['X-CSRF-Token']=self.csrf
            content=json.dumps(data).encode()
        con=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        con.request(method,path,body=content,headers=headers)
        resp=con.getresponse();body=resp.read()
        cookie=resp.getheader('Set-Cookie')
        if cookie:self.cookie=cookie.split(';')[0]
        con.close()
        try:value=json.loads(body)
        except ValueError:value={'raw':body}
        if value.get('csrf'):self.csrf=value['csrf']
        return resp.status,value


@pytest.fixture
def portal(tmp_path):
    p=Portal(tmp_path/'data')
    server=make_server('127.0.0.1',0,p)
    t=Thread(target=server.serve_forever,daemon=True)
    t.start()
    try:yield p,server.server_port
    finally:server.shutdown();server.server_close();t.join(timeout=5)


def register(client,name,secret='a really long test password 123'):
    code,data=client.post('/api/public/register',{'username':name,'password':secret},csrf=False)
    assert code==201,data
    return data


def test_public_health_and_local_admin_routes_are_not_exposed(portal):
    _,port=portal;c=Client(port)
    assert c.get('/api/public/health')[0]==200
    assert c.get('/api/state')[0]==401  # no private data before auth
    register(c,'ResearcherOne')
    assert c.get('/api/state')[0]==404
    assert c.post('/api/shutdown',{})[0]==404
    assert c.post('/api/key',{})[0]==404
    assert c.get('/api/provider-catalog')[0]==404


def test_multi_account_isolation_and_topic_access(portal):
    _,port=portal;a=Client(port);b=Client(port)
    ainfo=register(a,'ResearcherOne')
    binfo=register(b,'ResearcherTwo')
    assert ainfo['user']['id']!=binfo['user']['id']
    status,t=a.post('/api/public/topics',{'name':'神经算子','keywords':['neural operator','mesh']})
    assert status==201
    topic=t['id']
    assert a.get('/api/public/topics')[1]['topics'][-1]['id']==topic
    assert all(item['id']!=topic for item in b.get('/api/public/topics')[1]['topics'])
    assert b.get('/api/public/papers?topic_id='+topic)[0]==400
    assert b.post('/api/public/search',{'topic_id':topic})[0]==400
    with portal[0].workspace(ainfo['user']['id']) as db:
        db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',('p1','Private Study','','2026-11-04','','', 'now'))
        db.execute('INSERT INTO topic_papers VALUES (?,?)',(topic,'p1'))
    assert a.get('/api/public/papers?topic_id='+topic)[1]['papers'][0]['title']=='Private Study'
    assert b.get('/api/public/papers?topic_id=default')[1]['papers']==[]


def test_login_csrf_and_session_revocation(portal):
    p,port=portal;a=Client(port);register(a,'ResearcherOne')
    assert a.post('/api/public/topics',{'name':'x','keywords':['x']},csrf=False)[0]==403
    assert a.post('/api/public/topics',{'name':'x','keywords':['x']},origin='https://evil.example')[0]==403
    token=a.cookie
    status,_=a.post('/api/public/logout',{})
    assert status==200
    a.cookie=token
    assert a.get('/api/public/me')[0]==401
    b=Client(port)
    assert b.post('/api/public/login',{'username':'ResearcherOne','password':'wrong'},csrf=False)[0]==401
    assert b.post('/api/public/login',{'username':'ResearcherOne','password':'a really long test password 123'},csrf=False)[0]==200
    assert b.post('/api/public/password',{'old_password':'a really long test password 123','new_password':'my changed strong password 999'})[0]==200
    assert b.get('/api/public/me')[0]==401


def test_admin_permissions_and_disable(portal):
    p,port=portal
    with auth_db(p.auth_path) as db:create_account(db,'SystemOwner','the admin password very secret',role='admin')
    admin=Client(port);user=Client(port)
    assert admin.post('/api/public/login',{'username':'SystemOwner','password':'the admin password very secret'},csrf=False)[0]==200
    member=register(user,'ResearcherOne')
    assert user.get('/api/public/admin/accounts')[0]==403
    assert admin.get('/api/public/admin/accounts')[0]==200
    assert admin.post('/api/public/admin/account',{'id':member['user']['id'],'active':False})[0]==200
    assert user.get('/api/public/me')[0]==401
    assert admin.post('/api/public/admin/account',{'id':member['user']['id'],'active':True})[0]==200
    assert admin.post('/api/public/admin/account',{'id':admin.get('/api/public/me')[1]['user']['id'],'active':False})[0]==400


def test_imported_evidence_marked_unverified_and_synthesis(portal):
    _,port=portal;c=Client(port);register(c,'ResearcherOne')
    csv_data='论文ID,论文标题,评价指标,原文数值,PDF页码,原文引文,类型,数据集,实验条件\n'
    csv_data+='p1,Paper One,RMSE,0.12,5,"Experiment on TestSet reports RMSE 0.12 for this setup",metric,TestSet,case A\n'
    csv_data+='p2,Paper Two,RMSE,0.10,7,"Experiment on TestSet reports RMSE 0.10 for this setup",metric,TestSet,case A\n'
    code,result=c.post('/api/public/import',{'topic_id':'default','csv':csv_data})
    assert code==200,result
    assert result['imported']==2
    code,res=c.get('/api/public/synthesis?topic_id=default')
    assert code==200
    assert res['summary']['paper_count']==2
    assert len(res['summary']['comparisons'])==1
    assert '云端未复核' in res['summary']['records'][0]['provenance'].replace('user_import_unverified','云端未复核')
    assert '尚未由服务器' in res['markdown']


def test_synthesis_requires_actual_matching_conditions():
    first={'paper_id':'a','title':'Method A','metric':'RMSE','value':'0.12','quote':'The observed RMSE is 0.12',
           'page':3,'kind':'metric','dataset':'Dataset X','setting':'split A','published':'2025-04-01'}
    second={**first,'paper_id':'b','title':'Method B','value':'0.09','page':5,
            'quote':'The observed RMSE is 0.09'}
    result=compare_records([first,second])
    assert len(result['comparisons'])==1 and result['comparisons'][0]['papers']==2
    mismatch={**second,'setting':'split B'}
    assert compare_records([first,mismatch])['comparisons']==[]
    assert '提升' not in to_markdown(result,'Test Topic').split('## 四、使用边界')[0]


def test_local_synthesis_agent_runs_without_cloud(tmp_path,monkeypatch):
    from paper_radar import synthesis
    dbfile=tmp_path/'papers.sqlite3'
    connect(dbfile).close()
    samples=[{'paper_id':'paper-a','title':'A','metric':'Error','value':'0.2','page':1,
        'quote':'The observed Error is 0.2 in case A','kind':'metric','dataset':'TestSet','setting':'case A'}]
    monkeypatch.setattr(synthesis,'collect_local',lambda conn, topic_id, db_path:list(samples))
    config={**DEFAULTS,'active_topic_id':'default','output_dir':str(tmp_path/'reports')}
    result=run_synthesis_pipeline(config,db_path=dbfile)
    assert result['file']=='跨论文实验比较.md'
    assert (tmp_path/'reports'/'跨论文实验比较.md').is_file()
    assert result['stats']['papers']==1


def test_portal_arxiv_search_uses_own_workspace(portal):
    from paper_radar.models import Paper
    p,port=portal
    c=Client(port);out=register(c,'ResearcherOne')
    seen={}
    def fetch(cfg,logger):
        seen.update(cfg)
        return [Paper(paper_id='arxiv-test-001',title='Public arXiv Test',abstract='This is public data',
                      published='2026-11-04',updated='2026-11-04',url='https://arxiv.org/abs/test',
                      authors='Test A',category='cs.LG',pdf_url='https://arxiv.org/pdf/test')]
    p.fetcher=fetch
    assert p.search(out['user']['id'],'default')['new']==1
    assert seen['max_results']==20
    assert c.get('/api/public/papers?topic_id=default')[1]['papers'][0]['title']=='Public arXiv Test'
    other=Client(port);register(other,'ResearcherTwo')
    assert other.get('/api/public/papers?topic_id=default')[1]['papers']==[]


def test_origin_and_custom_header_are_mandatory(portal):
    _,port=portal;c=Client(port)
    assert c.post('/api/public/register',{'username':'ResearcherOne','password':'a really long test password'},
                  csrf=False,action=False)[0]==403
    assert c.post('/api/public/register',{'username':'ResearcherOne','password':'a really long test password'},
                  csrf=False,origin='https://evil.example')[0]==403
    assert c.get('/api/public/me')[0]==401


def test_portal_hidden_auth_and_dashboard_have_explicit_css_override():
    """CSS display:grid otherwise wins over the browser's [hidden] user-agent rule."""
    from paper_radar.public_portal import STATIC
    css = (STATIC / 'portal.css').read_text(encoding='utf-8')
    assert '[hidden]{display:none!important}' in css.replace(' ', '').lower()


def test_public_nginx_template_port_and_domain_render():
    from tools.render_public_nginx import render
    output=render('papers.example.com',9009)
    assert 'proxy_pass http://127.0.0.1:9009;' in output
    assert 'server_name papers.example.com;' in output
    assert 'papers.example.com/fullchain.pem' in output
    for bad in ('bad.example.com; return 200', '127.0.0.1', '/tmp/sneaky'):
        with pytest.raises(ValueError):render(bad,8899)
    with pytest.raises(ValueError):render('papers.example.com',65536)


def test_public_launch_scripts_use_configurable_ports():
    from paper_radar.public_portal import ROOT
    win=(ROOT/'start_public_windows.bat').read_text(encoding='utf-8')
    shell=(ROOT/'start_public_macos_linux.sh').read_text(encoding='utf-8')
    assert 'set "PORT=%~1"' in win
    assert '--port "%PORT%"' in win
    assert '.venv\\Scripts\\python.exe' in win
    assert '--port "$PORT"' in shell
    assert '127.0.0.1' in win and '127.0.0.1' in shell
