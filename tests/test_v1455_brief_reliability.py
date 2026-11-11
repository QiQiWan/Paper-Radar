"""v1.45.5 research brief: late events, idempotency and durable resumption."""
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from paper_radar.core import connect
from paper_radar.evidence_chain import EvidenceChain
from paper_radar.migrations import migrate
from paper_radar import research_continuity as legacy
from paper_radar.research_brief_reliability import run
from tests.test_v140_research_inquiry import _create_local

WHEN=datetime(2026,11,10,10,tzinfo=timezone.utc)


def setup(tmp_path):
    path,_,pid=_create_local(tmp_path)
    with connect(path) as db:migrate(db,'research')
    return path,pid


def insert_event(db,pid,num,observed='2026-11-05T09:00:00+00:00'):
    db.execute('''INSERT INTO following_events
    (topic_id,source_name,source_id,paper_id,kind,signature,observed_at,title,details_json)
    VALUES (?,?,?,?,?,?,?,?,?)''',
    ('default','arxiv',str(num),pid,'new','signature'+str(num),observed,'PDE research '+str(num),'{}'))


def chain(db,path):
    return EvidenceChain(db,'default',backend='local',db_path=path)


def test_idempotent_replay_and_late_insertion_is_append_only(tmp_path):
    path,pid=setup(tmp_path)
    with connect(path) as db:
        insert_event(db,pid,1)
        first=run(db,'default',chain(db,path),now=WHEN)
        assert first['state']=='complete' and first['report']['event_count']==1
        assert run(db,'default',chain(db,path),now=WHEN)['id']==first['id']
        assert db.execute('SELECT COUNT(*) FROM research_brief_event_ledger').fetchone()[0]==1
        insert_event(db,pid,2)
        amended=run(db,'default',chain(db,path),now=WHEN)
        assert amended['id']==first['id']
        assert amended['late_events_recorded']==1
        assert amended['amendment_summary']['events']==1
        assert amended['report']['event_count']==1  # historical original immutable
        again=run(db,'default',chain(db,path),now=WHEN)
        assert again['amendment_summary']['events']==1
        assert db.execute('SELECT COUNT(*) FROM research_brief_amendments').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM research_brief_event_ledger').fetchone()[0]==2
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            db.execute('UPDATE research_brief_amendments SET digest=?',('fake',))


def test_resumable_batched_scan_survives_connection_loss(tmp_path):
    path,pid=setup(tmp_path)
    with connect(path) as db:
        for i in range(510):insert_event(db,pid,i)
        db.commit()
    with connect(path) as db:
        first=run(db,'default',chain(db,path),now=WHEN,batch_size=75,max_batches=1)
        assert first['state']=='processing' and first['last_scanned_id']>=75
        job=first['job_id']
    # The next process starts a new DB connection and resumes persisted event IDs.
    with connect(path) as db:
        second=run(db,'default',chain(db,path),now=WHEN,batch_size=75,max_batches=20)
        assert second['state']=='complete' and second['report']['event_count']==510
        assert db.execute('SELECT COUNT(*) FROM research_brief_job_events WHERE job_id=?',(job,)).fetchone()[0]==510
        assert db.execute('SELECT COUNT(*) FROM research_brief_event_ledger').fetchone()[0]==510
        assert db.execute('SELECT COUNT(*) FROM research_period_briefs').fetchone()[0]==1
        audit=db.execute('SELECT action,COUNT(*) n FROM research_brief_job_audit GROUP BY action').fetchall()
        assert audit[0]['n']>=7
        assert run(db,'default',chain(db,path),now=WHEN)['id']==second['id']


def test_late_event_routes_to_existing_older_period(tmp_path):
    path,pid=setup(tmp_path)
    with connect(path) as db:
        insert_event(db,pid,1,'2026-10-27T00:00:00+00:00')
        older=datetime(2026,11,3,10,tzinfo=timezone.utc)
        earlier=run(db,'default',chain(db,path),now=older)
        newer=run(db,'default',chain(db,path),now=WHEN)
        insert_event(db,pid,2,'2026-10-28T00:00:00+00:00')
        result=run(db,'default',chain(db,path),now=WHEN)
        assert result['id']==newer['id']
        prev=legacy.read_brief(db,'default',earlier['id'],chain(db,path))
        assert prev['amendment_summary']['events']==1
        assert prev['amendments'][0]['period_end']==earlier['report']['period_end']
        assert db.execute('SELECT brief_id FROM research_brief_event_ledger WHERE event_id=2').fetchone()[0]==earlier['id']


def test_different_topic_and_cadence_do_not_share_ledgers(tmp_path):
    path,pid=setup(tmp_path)
    with connect(path) as db:
        insert_event(db,pid,1)
        weekly=run(db,'default',chain(db,path),now=WHEN,cadence='weekly')
        daily=run(db,'default',chain(db,path),now=WHEN,cadence='daily')
        assert weekly['state']=='complete' and daily['state']=='complete'
        assert db.execute('SELECT COUNT(*) FROM research_brief_event_ledger').fetchone()[0]==2
        assert run(db,'another',EvidenceChain(db,'another',backend='local',db_path=path),now=WHEN)['report']['event_count']==0


def test_revision_diff_does_not_overwrite_earlier_claims():
    from paper_radar.research_brief_reliability import revision_diff
    original={'confirmed_claims':{'fno':'rev-1','pino':'rev-3','retired':'rev-4'}}
    current={'fno':'rev-2','pino':'rev-3','new_method':'rev-1'}
    assert revision_diff(original,current)=={
        'new':['new_method'],'revised':['fno'],'no_longer_current':['retired']}
