"""Public following: authenticated tenant isolation, revisions, and CSRF."""
import http.client
import json
from threading import Thread

import pytest
from paper_radar.models import Paper
from paper_radar.public_portal import Portal,make_server


class Session:
    def __init__(self,port):
        self.port=port; self.csrf='';self.cookie=''
    def request(self,method,path,body=None,*,valid_csrf=True):
        headers={'Host':f'127.0.0.1:{self.port}'}
        if self.cookie:headers['Cookie']=self.cookie
        if method=='POST':
            headers.update({'Content-Type':'application/json','Origin':f'http://127.0.0.1:{self.port}',
                            'X-Portal-Action':'1','X-CSRF-Token':self.csrf if valid_csrf else 'invalid'})
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        conn.request(method,path,body=json.dumps(body).encode() if body is not None else None,headers=headers)
        resp=conn.getresponse();raw=resp.read();code=resp.status
        try:data=json.loads(raw)
        except ValueError:data={'html':raw.decode('utf-8','replace')}
        cookie=resp.getheader('Set-Cookie')
        if cookie:self.cookie=cookie.split(';')[0]
        if data.get('csrf'):self.csrf=data['csrf']
        conn.close();return code,data
    def get(self,path):return self.request('GET',path)
    def post(self,path,body,**kwargs):return self.request('POST',path,body,**kwargs)


@pytest.fixture
def two_users(tmp_path):
    portal=Portal(tmp_path/'tenants',job_workers=0)
    server=make_server('127.0.0.1',0,portal)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    a,b=Session(server.server_port),Session(server.server_port)
    ua=a.post('/api/public/register',{'username':'ResearcherA','password':'strong passphrase alpha 123'})
    ub=b.post('/api/public/register',{'username':'ResearcherB','password':'strong passphrase beta 456'})
    assert ua[0]==201 and ub[0]==201
    try:yield portal,a,b,ua[1]['user']['id'],ub[1]['user']['id']
    finally:server.shutdown();server.server_close();thread.join(timeout=4)


def test_public_following_is_account_isolated_and_reviewable(two_users):
    portal,a,b,uid_a,uid_b=two_users
    current={'title':'An original arxiv research note','updated':'2026-11-08T08:00:00Z'}
    def fetch(cfg,logger):
        return [Paper('2611.00003',current['title'],'An abstract','Author',
                '2026-11-08T08:00:00Z',current['updated'],'cs.LG',
                'https://arxiv.org/abs/2611.00003','',source='arxiv')]
    portal.fetcher=fetch
    assert portal.search(uid_a,'default')['following']['events']==1
    code,result=a.get('/api/public/following?topic_id=default')
    assert code==200 and result['unread']==1
    assert result['events'][0]['kind']=='new'
    assert b.get('/api/public/following?topic_id=default')[1]['events']==[]
    current.update(title='Revised arxiv research note',updated='2026-11-09T08:00:00Z')
    assert portal.search(uid_a,'default')['following']['events']==1
    records=a.get('/api/public/following?topic_id=default')[1]
    assert [e['kind'] for e in records['events']]==['revised','new']
    ids=[e['id'] for e in records['events']]
    assert a.post('/api/public/following/seen',{'topic_id':'default','event_ids':ids},valid_csrf=False)[0]==403
    assert a.post('/api/public/following/seen',{'topic_id':'default','event_ids':ids})[1]['following']['unread']==0
    assert b.get('/api/public/following?topic_id=default')[1]['unread']==0
    assert a.post('/api/public/following/preferences',{'topic_id':'default',
                'preferences':{'notify_new':False}})[0]==200
    assert a.get('/api/public/following?topic_id=default')[1]['preferences']['notify_new'] is False
    assert b.get('/api/public/following?topic_id=default')[1]['preferences']['notify_new'] is True
    assert a.post('/api/public/following/preferences',{'topic_id':'not_their_topic',
                'preferences':{'notify_new':False}})[0]==400


def test_public_pages_and_following_routes_exist(two_users):
    _,a,_,_,_=two_users
    assert a.get('/following')[0]==200
    assert a.get('/api/public/following?topic_id=default')[0]==200
    assert a.post('/api/public/following/seen',{'topic_id':'default','event_ids':[1]})[1]['following']['unread']==0


def test_all_public_sources_fail_without_advancing_success_checkpoint(two_users,monkeypatch):
    from paper_radar.public_portal import PortalError
    from paper_radar import public_portal
    portal,a,_,uid,_=two_users
    portal.fetcher=None
    def down(p,cfg,sources,should_cancel=None):
        raise PortalError('公开来源暂时不可用',[{'name':'arxiv','ok':False,
           'partial':False,'timed_out':True,'count':0,'error_code':'SOURCE_TIMEOUT'}])
    monkeypatch.setattr(public_portal,'discover',down)
    with pytest.raises(PortalError):portal.search(uid,'default')
    data=a.get('/api/public/following?topic_id=default')[1]
    assert len(data['checkpoints'])==1
    assert data['checkpoints'][0]['status']=='pending'
    assert data['checkpoints'][0]['last_success']==''
    assert data['checkpoints'][0]['error_code']=='SOURCE_FAILED'
    assert data['unread']==0
