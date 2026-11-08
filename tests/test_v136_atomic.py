"""Search persistence: checkpoints, paper writes and notifications share one transaction."""
import sqlite3
from datetime import datetime,timezone
from paper_radar import core
from paper_radar.following import plan_windows,record_cycle,status
from paper_radar.models import Paper


def test_paper_event_and_checkpoint_roll_back_as_one_transaction(tmp_path):
    path=tmp_path/'research.sqlite3'
    cfg=core.validate_config(dict(core.DEFAULTS,keywords=['operator'],sources=['arxiv']))
    p=Paper('2611.10101','Atomic record','Atomic study','Researcher',
            '2026-11-08','2026-11-08','cs.LG','','',source='arxiv')
    try:
        with core.open_database(path) as db:
            cfg['_following_windows']=plan_windows(db,cfg,datetime(2026,11,8,tzinfo=timezone.utc))
            assert core.save_papers(db,[p],topic_id='default',commit=False)
            result=record_cycle(db,cfg,[p],[p],[{'name':'arxiv','ok':True,'partial':False,
                'count':1,'queries':[{'ok':True,'count':1}]}],new_to_topic={p.paper_id})
            assert result['events']==1
            raise sqlite3.OperationalError('injected crash after tracking before transaction commit')
    except sqlite3.OperationalError:pass
    with core.open_database(path) as db:
        assert db.execute("SELECT COUNT(*) FROM papers WHERE paper_id='2611.10101'").fetchone()[0]==0
        assert status(db,'default')['events']==[]
        assert status(db,'default')['checkpoints']==[]
    with core.open_database(path) as db:
        core.save_papers(db,[p],topic_id='default',commit=False)
        record_cycle(db,cfg,[p],[p],[{'name':'arxiv','ok':True,'partial':False,'count':1,
                                     'queries':[{'ok':True,'count':1}]}],new_to_topic={p.paper_id})
    with core.open_database(path) as db:
        assert db.execute("SELECT COUNT(*) FROM papers WHERE paper_id='2611.10101'").fetchone()[0]==1
        assert status(db,'default')['unread']==1
