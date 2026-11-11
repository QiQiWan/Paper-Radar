# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Real Runtime.start worker path against isolated local PDF/SQLite fixtures."""
from __future__ import annotations
import time
from paper_radar import webapp
from paper_radar.core import DEFAULTS,open_database
from paper_radar.evidence_batch import create_run,list_run
from paper_radar.runtime_diagnostics import RuntimeDiagnostics
from test_v150_evidence_batch import fixture


def test_actual_background_worker_dispatch_and_recovery(tmp_path,monkeypatch):
    db,path=fixture(tmp_path,count=2)
    rid=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',
                   ['evidence-0','evidence-1'])['run_id']
    db.close()
    cfg={**DEFAULTS,'active_topic_id':'default','llm_data_consent':False}
    monkeypatch.setattr(webapp,'read_config',lambda:cfg)
    monkeypatch.setattr(webapp,'assert_primary_healthy',lambda _:None)
    monkeypatch.setattr(webapp,'open_database',lambda:open_database(path))
    monkeypatch.setattr(webapp,'DB_FILE',path)
    monkeypatch.setattr(webapp,'APP_ROOT',tmp_path)
    monkeypatch.setattr(webapp,'LOCK_FILE',tmp_path/'worker.lock')
    monkeypatch.setattr(webapp,'DIAGNOSTICS',RuntimeDiagnostics(tmp_path))
    status=[]
    monkeypatch.setattr(webapp,'write_status',lambda s:status.append(s))
    r=webapp.Runtime()
    r.start('evidence_batch',[rid])
    for _ in range(400):
        if not r.state()['busy']:break
        time.sleep(.025)
    assert not r.state()['busy']
    assert r.state()['result'].get('review_candidates',0)>=2
    assert any(s.get('mode')=='evidence_batch' and s.get('state')=='success' for s in status)
    with open_database(path) as conn:
        data=list_run(conn,'default',rid,db_path=path)
        assert data['counts']['pending']>=2
        assert all(p['state']=='ready_for_review' for p in data['items'])
