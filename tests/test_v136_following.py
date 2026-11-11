"""v1.36 incremental checkpoints, event history, opt-in notifications."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import json

import pytest
from paper_radar.core import connect,DEFAULTS,validate_config,save_papers
from paper_radar.following import (plan_windows,record_cycle,status,update_preference,
                                   mark_seen,digest,query_fingerprint)
from paper_radar.following.delivery import send_digest
from paper_radar.models import Paper

NOW=datetime(2026,11,8,11,0,tzinfo=timezone.utc)

def make_paper(*,title='First paper',updated='2026-11-08T09:00:00Z',doi=''):
    return Paper('2611.00001',title,'Evidence text','An Author',
      '2026-11-07T08:00:00Z',updated,'cs.LG','https://arxiv.org/abs/2611.00001','',source='arxiv',doi=doi)

def cfg():return validate_config(dict(DEFAULTS,keywords=['operator learning'],sources=['arxiv']))

def source(*,ok=True,partial=False,saturated=False,count=1):
    return [{'name':'arxiv','ok':ok,'partial':partial,'timed_out':False,'count':count,
             'queries':[{'ok':ok,'count':count,'saturated':saturated}]}]

@pytest.fixture
def database(tmp_path):
    db=connect(tmp_path/'papers.sqlite3')
    try:yield db
    finally:db.close()


def run(db,config,paper,rows,*,new=False,now=NOW):
    config['_following_windows']=plan_windows(db,config,now)
    if paper is not None:
        save_papers(db,[paper],topic_id=config['active_topic_id'])
    result=record_cycle(db,config,[paper] if paper else [],[paper] if paper else [],rows,
                        new_to_topic={paper.paper_id} if (new and paper) else set(),now=now)
    db.commit()
    return result


def test_query_cap_change_invalidates_old_source_checkpoint(database):
    config=cfg()
    original_hash=query_fingerprint(config,'arxiv')
    run(database,config,make_paper(),source(),new=True)
    config['max_results']=config['max_results']+5
    assert query_fingerprint(config,'arxiv')!=original_hash
    fresh=plan_windows(database,config,NOW+timedelta(days=5))
    assert fresh['arxiv']['fresh_start'] is True


def test_new_event_and_repeat_is_idempotent(database):
    config=cfg();paper=make_paper()
    assert run(database,config,paper,source(),new=True)['events']==1
    assert run(database,config,paper,source(),new=False)['events']==0
    info=status(database,'default')
    assert info['unread']==1 and len(info['events'])==1
    assert info['pending_sources']==0
    assert info['events'][0]['kind']=='new'
    assert len(digest(database,'default'))>20


def test_source_failure_keeps_last_success_and_pending_window(database):
    config=cfg();paper=make_paper()
    run(database,config,paper,source(),new=True)
    last=status(database,'default')['checkpoints'][0]['last_success']
    future=NOW+timedelta(days=7)
    config['_following_windows']=plan_windows(database,config,future)
    assert config['_following_windows']['arxiv']['start'][:10] <= '2026-11-07'
    result=record_cycle(database,config,[],[],source(ok=False,count=0),now=future)
    database.commit()
    assert result['pending_sources']==1
    checkpoint=status(database,'default')['checkpoints'][0]
    assert checkpoint['last_success']==last and checkpoint['status']=='pending'
    assert checkpoint['error_code']=='SOURCE_FAILED'


def test_saturated_search_does_not_claim_full_coverage(database):
    config=cfg();p=make_paper()
    out=run(database,config,p,source(saturated=True),new=True)
    assert out['pending_sources']==1
    assert status(database,'default')['checkpoints'][0]['last_success']==''


def test_new_revision_and_old_stale_version(database):
    config=cfg();p=make_paper()
    run(database,config,p,source(),new=True)
    p2=make_paper(title='Revised paper',updated='2026-11-09T11:00:00Z')
    assert run(database,config,p2,source(),now=NOW+timedelta(days=1))['events']==1
    assert run(database,config,p,source(),now=NOW+timedelta(days=2))['events']==0
    info=status(database,'default')
    assert [e['kind'] for e in info['events']]==['revised','new']
    versions=database.execute('SELECT COUNT(*) FROM following_versions').fetchone()[0]
    assert versions==2


def test_formal_publication_signal(database):
    config=cfg();run(database,config,make_paper(),source(),new=True)
    paper=make_paper(doi='10.1000/test',updated='2026-11-09T12:00:00Z')
    run(database,config,paper,source(),now=NOW+timedelta(days=1))
    assert status(database,'default')['events'][0]['kind']=='published_version'


def test_new_topic_cannot_see_other_topic_events(database):
    config=cfg();run(database,config,make_paper(),source(),new=True)
    config['active_topic_id']='topic-b'
    config['topics'].append({**config['topics'][0],'id':'topic-b','name':'Topic B'})
    assert status(database,'topic-b')['events']==[]
    assert status(database,'topic-b')['unread']==0
    assert mark_seen(database,'topic-b',[1])==0
    assert status(database,'default')['unread']==1


def test_unsubscribe_keeps_history_and_stops_notifications(database):
    config=cfg();run(database,config,make_paper(),source(),new=True)
    update_preference(database,'default',{'notify_updated':False})
    run(database,config,make_paper(title='New title',updated='2026-11-09T13:00:00Z'),source(),now=NOW+timedelta(days=1))
    database.commit()
    assert len(status(database,'default')['events'])==2
    assert status(database,'default')['unread']==1
    update_preference(database,'default',{'enabled':False})
    assert plan_windows(database,config,NOW)=={}


def test_mark_seen_does_not_erase_history(database):
    c=cfg();run(database,c,make_paper(),source(),new=True)
    event_id=status(database,'default')['events'][0]['id']
    assert mark_seen(database,'default',[event_id])==1
    assert mark_seen(database,'default',[event_id])==0
    assert status(database,'default')['unread']==0
    assert len(status(database,'default')['events'])==1


def test_email_opt_in_and_send_failure_retry(database):
    c=cfg();run(database,c,make_paper(),source(),new=True)
    assert send_digest(database,'default')['state']=='disabled'
    with pytest.raises(ValueError):update_preference(database,'default',{'email_enabled':True})
    update_preference(database,'default',{'email_enabled':True,'email_to':'research@example.org'})
    env={'PAPER_RADAR_SMTP_HOST':'smtp.example.org','PAPER_RADAR_SMTP_FROM':'bot@example.org'}
    def fail(message):raise ConnectionError('Simulated outage')
    with pytest.raises(ConnectionError):send_digest(database,'default',env=env,transport=fail)
    sent=[]
    assert send_digest(database,'default',env=env,transport=sent.append)['sent']==1
    assert len(sent)==1 and 'First paper' in sent[0].get_content()
    assert send_digest(database,'default',env=env,transport=sent.append)['sent']==0


def test_query_change_does_not_advance_old_cursor(database):
    c=cfg();run(database,c,make_paper(),source(),new=True)
    prior=status(database,'default')['checkpoints'][0]
    assert prior['last_success']
    c['keywords']=['physics informed']
    planned=plan_windows(database,c,NOW+timedelta(days=3))
    assert planned['arxiv']['fresh_start'] is True
    assert planned['arxiv']['start'][:10]=='2026-11-04'
