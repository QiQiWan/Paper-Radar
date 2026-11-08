"""v1.33 tests: PDF anchors, review invalidation, cross-paper, browser API and migrations."""
from __future__ import annotations

import hashlib
from pathlib import Path
from threading import Thread

import pytest

from paper_radar.core import connect,save_papers
from paper_radar.evidence_chain import EvidenceChain,RevisionConflict,portal_chain,local_chain
from paper_radar.migrations import REGISTRIES,migrate,status
from paper_radar.models import Paper
from paper_radar.public_portal import Portal,make_server
from paper_radar.portal_research import analyze,save_pdf,records
from paper_radar.portal_reviews import review_evidence,review_pair
from tests.test_v118_public_pipeline import paper,pdf_bytes,QUOTE
from test_v116_portal_and_synthesis import Client,register
from test_v124_trusted_comparisons import CONDITIONS


def setup_portal(tmp_path, *, users=1):
    p=Portal(tmp_path/'data',embedded_workers=False)
    p.fetcher=lambda cfg,logger:[paper('2601.12345'),paper('2601.12346')]
    p.model_callable=lambda batch:{'findings':[{'chunk_id':batch[0]['chunk_id'],
        'kind':'metric','dataset':'DatasetA','metric':'Accuracy','value':'91%',
        'baseline':'BaselineX','setting':'split1','quote':QUOTE}]}
    # Model callable for this test pipeline expects JSON text.
    import json
    old=p.model_callable;p.model_callable=lambda x:json.dumps(old(x))
    ids=[]
    for n in range(users):
        from paper_radar.portal_auth import db as auth_db,create_account
        with auth_db(p.auth_path) as db:
            uid=create_account(db,'ChainUser'+str(n),'a very long safe test password')['id']
        p.search(uid,'default')
        for pid in ('2601.12345','2601.12346'):
            save_pdf(p,uid,'default',pid,pdf_bytes())
            assert analyze(p,uid,'default',pid,cloud=True)['evidence_count']==1
        ids.append(uid)
    return p,ids


def anchor(p,uid,pid,*,experiment_id=''):
    return portal_chain(p,uid,'default','add_anchor',pid,1,QUOTE,experiment_id=experiment_id)


def test_pdf_anchor_confirmation_cross_paper_and_replacement(tmp_path):
    p,[uid]=setup_portal(tmp_path)
    first=anchor(p,uid,'2601.12345')
    assert first['page']==1 and first['pdf_sha256']
    with pytest.raises(ValueError,match='原文'):
        portal_chain(p,uid,'default','add_anchor','2601.12345',1,'Fabricated 99% quote')
    with pytest.raises(ValueError,match='PDF 页码'):
        portal_chain(p,uid,'default','add_anchor','2601.12345',3,QUOTE)
    with pytest.raises(ValueError,match='主题'):
        portal_chain(p,uid,'default','add_anchor','unknown',1,QUOTE)
    claim=portal_chain(p,uid,'default','save_claim','This paper reports Accuracy 91% in benchmark.',
                       'finding',[first['id']],0)
    with pytest.raises(RevisionConflict):
        portal_chain(p,uid,'default','save_claim','A different draft about the same result',
                     'finding',[first['id']],3,claim_id=claim['id'])
    confirmed=portal_chain(p,uid,'default','confirm',claim['id'],1,'I inspected the exact PDF sentence')
    assert confirmed['decision']=='confirmed'
    view=portal_chain(p,uid,'default','read')
    assert view['claims'][0]['effective_status']=='confirmed'
    assert 'This paper reports' in portal_chain(p,uid,'default','export_markdown')
    # Replacing a PDF retains audit, but revokes the claim dynamically.
    save_pdf(p,uid,'default','2601.12345',pdf_bytes('Completely changed study with no old benchmark results'))
    changed=portal_chain(p,uid,'default','read')
    assert changed['anchors'][0]['status']=='stale'
    assert changed['claims'][0]['effective_status']=='stale'
    assert changed['claims'][0]['decision']=='confirmed'
    with pytest.raises(ValueError,match='无法确认'):
        portal_chain(p,uid,'default','confirm',claim['id'],2,'Rechecking after replacement')
    with p.workspace(uid) as db:
        assert db.execute('SELECT COUNT(*) FROM research_claim_audit').fetchone()[0]==2
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_experiment_relation_and_pair_review_required(tmp_path):
    p,[uid]=setup_portal(tmp_path)
    data=records(p,uid,'default')
    first,second=sorted(data,key=lambda r:r['paper_id'])
    a=anchor(p,uid,first['paper_id'],experiment_id=first['evidence_id'])
    b=anchor(p,uid,second['paper_id'],experiment_id=second['evidence_id'])
    claim=portal_chain(p,uid,'default','save_claim','The two papers report an accuracy of 91 percent.',
                       'comparison',[a['id'],b['id']],0)
    view=portal_chain(p,uid,'default','read')
    assert view['claims'][0]['effective_status']=='needs_review'
    with pytest.raises(ValueError,match='无法确认'):
        portal_chain(p,uid,'default','confirm',claim['id'],1,'Checked both PDF table rows')
    for r in (first,second):
        review_evidence(p,uid,'default',r['paper_id'],r['evidence_id'],
                        'approved',CONDITIONS,'Verified the complete table and metrics')
    view=portal_chain(p,uid,'default','read')
    assert view['claims'][0]['effective_status']=='needs_review'
    review_pair(p,uid,'default',first['evidence_id'],second['evidence_id'],'comparable',
                'Checked identical splits and the exact evaluation protocols')
    confirmed=portal_chain(p,uid,'default','confirm',claim['id'],1,'Reviewed the two independent experimental records')
    assert confirmed['decision']=='confirmed'
    assert portal_chain(p,uid,'default','read')['claims'][0]['effective_status']=='confirmed'
    review_evidence(p,uid,'default',first['paper_id'],first['evidence_id'],
                    'approved',{**CONDITIONS,'split':'new split'},'Changed split after verification')
    assert portal_chain(p,uid,'default','read')['claims'][0]['effective_status']=='needs_review'


def test_topic_and_account_isolation_and_unsafe_quote(tmp_path):
    p,ids=setup_portal(tmp_path,users=2)
    a=anchor(p,ids[0],'2601.12345')
    assert portal_chain(p,ids[1],'default','read')['anchors']==[]
    with pytest.raises(ValueError,match='证据不属于'):
        portal_chain(p,ids[1],'default','save_claim','My own interpretation of this paper.',
                     'finding',[a['id']],0)
    with p.workspace(ids[0]) as db:
        db.execute("INSERT INTO topics VALUES ('other','Other topic','[]','2026-11-07')")
    assert portal_chain(p,ids[0],'other','read')['anchors']==[]
    with pytest.raises(ValueError):
        portal_chain(p,ids[0],'other','save_claim','Another topic cannot reuse it.',
                     'finding',[a['id']],0)
    with pytest.raises(ValueError):
        portal_chain(p,ids[0],'default','add_anchor','2601.12345',1,'x'*651)
    with pytest.raises(ValueError):
        portal_chain(p,ids[0],'default','add_anchor','2601.12345',1,QUOTE,expected_sha256='0'*64)


def test_api_route_and_replay(tmp_path):
    p=Portal(tmp_path/'data',embedded_workers=False)
    p.fetcher=lambda cfg,logger:[paper('2601.12345')]
    server=make_server('127.0.0.1',0,p)
    t=Thread(target=server.serve_forever,daemon=True);t.start()
    try:
        a=Client(server.server_port);b=Client(server.server_port)
        ai=register(a,'ChainHttpAlice')['user']['id'];register(b,'ChainHttpBob')
        p.search(ai,'default');save_pdf(p,ai,'default','2601.12345',pdf_bytes())
        analyze(p,ai,'default','2601.12345')
        code,data=a.post('/api/public/trace/anchor',{'topic_id':'default','paper_id':'2601.12345',
            'page':1,'quote':QUOTE,'experiment_id':'','expected_sha256':''})
        assert code==200,data
        assert b.get('/api/public/trace?topic_id=default')[1]['counts']['anchors']==0
        assert a.post('/api/public/trace/anchor',{'topic_id':'default','paper_id':'2601.12345',
            'page':1,'quote':QUOTE,'experiment_id':'','expected_sha256':''},csrf=False)[0]==403
        code,claim=a.post('/api/public/trace/claim',{'topic_id':'default','body':'This user confirms a source-backed research observation.',
            'kind':'finding','anchor_ids':[data['id']],'expected_revision':0,'claim_id':''})
        assert code==200,claim
        assert a.post('/api/public/trace/confirm',{'topic_id':'default','claim_id':claim['id'],
            'expected_revision':1,'review_note':'I checked this source carefully','decision':'confirmed'})[0]==200
        assert a.post('/api/public/trace/confirm',{'topic_id':'default','claim_id':claim['id'],
            'expected_revision':1,'review_note':'I checked this source carefully','decision':'confirmed'})[0]==409
        assert b.post('/api/public/trace/confirm',{'topic_id':'default','claim_id':claim['id'],
            'expected_revision':2,'review_note':'I checked this source carefully','decision':'confirmed'})[0]==409
        assert 'source\\-backed' in a.get('/api/public/trace/export?topic_id=default')[1]['raw'].decode()
    finally:
        server.shutdown();server.server_close();t.join(timeout=5)


def test_local_chained_quote_and_nonpromoted_experiment(tmp_path):
    from paper_radar.downloads.manager import download_root
    from paper_radar.fulltext import init_schema
    dbfile=tmp_path/'research'/'paper.sqlite3'
    dbfile.parent.mkdir()
    pdfdir=download_root(dbfile);pdfdir.mkdir(parents=True)
    pdf=pdfdir/'sample.pdf';pdf.write_bytes(pdf_bytes())
    sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
    sample=Paper(paper_id='local:pdf',title='Experiment pdf',abstract='text',authors='A',
        published='2026-11-01',updated='2026-11-01',category='cs.LG',
        url='https://example.org',pdf_url='',doi='')
    with connect(dbfile) as db:
        save_papers(db,[sample]);init_schema(db)
        db.execute('UPDATE papers SET local_file=? WHERE paper_id=?',('sample.pdf','local:pdf'))
        db.execute('''INSERT INTO fulltext_documents
            (paper_id,pdf_sha256,file_name,parser_version,total_pages,indexed_pages,chunk_count,state)
            VALUES (?,?,'sample.pdf','v1',1,1,1,'indexed')''',('local:pdf',sha))
        db.execute('INSERT INTO fulltext_chunks(paper_id,page,section,content) VALUES (?,?,?,?)',
                   ('local:pdf',1,'实验',QUOTE))
    first=local_chain('default','add_anchor','local:pdf',1,QUOTE,db_path=dbfile)
    claim=local_chain('default','save_claim','Local human finding supported by this PDF.',
                      'finding',[first['id']],0,db_path=dbfile)
    assert local_chain('default','confirm',claim['id'],1,'Verified local PDF',db_path=dbfile)['decision']=='confirmed'
    assert local_chain('default','read',db_path=dbfile)['counts']['confirmed_current']==1
    # Out-of-band PDF replacement without a new index invalidates the record.
    pdf.write_bytes(pdf_bytes('New PDF content with modified results'))
    assert local_chain('default','read',db_path=dbfile)['claims'][0]['effective_status']=='stale'
    with connect(dbfile) as db:
        assert status(db,'research')['version']==len(REGISTRIES['research'])
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_migration_from_previous_versions(tmp_path,monkeypatch):
    for kind,old in [('portal_workspace',10),('research',3)]:
        if kind=='portal_workspace':
            complete=REGISTRIES[kind]
            monkeypatch.setitem(REGISTRIES,kind,complete[:old])
            import sqlite3
            c=sqlite3.connect(tmp_path/'portal.sqlite')
            migrate(c,kind)
            c.execute("INSERT INTO topics VALUES ('legacy','Legacy topic','[]','2026-11-06')")
        else:
            path=tmp_path/'local.sqlite'
            complete=REGISTRIES[kind]
            monkeypatch.setitem(REGISTRIES,kind,complete[:old])
            c=connect(path)
            migrate(c,kind)
        c.commit()
        monkeypatch.setitem(REGISTRIES,kind,complete)
        assert migrate(c,kind)==complete[-1].version
        assert c.execute('SELECT name FROM sqlite_master WHERE name="research_anchors"').fetchone()
        if kind=='portal_workspace':
            assert c.execute('SELECT name FROM topics WHERE id="legacy"').fetchone()[0]=='Legacy topic'
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        c.close()


def test_note_revision_link_audit_and_synthesis_snapshot(tmp_path):
    import sqlite3
    from paper_radar.portal_triage import save_note
    from paper_radar.portal_research import synthesis
    p,[uid]=setup_portal(tmp_path)
    r1=save_note(p,uid,'default','2601.12345','This benchmark result appears credible',
                 'cite',0)
    assert r1['note_revision']==1
    a=anchor(p,uid,'2601.12345')
    assert a['note_revision']==1
    assert a['note_snapshot']=='This benchmark result appears credible'
    assert a['note_action_snapshot']=='cite'
    c=portal_chain(p,uid,'default','save_claim',
        'I found a specific result supported by the PDF quotation.',
        'finding',[a['id']],0)
    portal_chain(p,uid,'default','confirm',c['id'],1,
        'Verified the PDF page and its exact quote')
    view=portal_chain(p,uid,'default','read')
    assert view['anchors'][0]['reading_note']['state']=='current'
    assert view['claims'][0]['effective_status']=='confirmed'
    snap=synthesis(p,uid,'default')
    assert snap['human_trace']['confirmed']==1
    assert a['id'][:12] in snap['markdown']
    assert 'v1' in snap['markdown']
    save_note(p,uid,'default','2601.12345',
        'This citation needs a more cautious interpretation', 'discuss',1)
    view=portal_chain(p,uid,'default','read')
    assert view['anchors'][0]['reading_note']['state']=='changed'
    assert view['anchors'][0]['reading_note']['current_revision']==2
    assert view['anchors'][0]['reading_note']['snapshot_excerpt']=='This benchmark result appears credible'
    assert view['anchors'][0]['reading_note']['excerpt']=='This citation needs a more cautious interpretation'
    # Changing an interpretive note does not fake a change to the PDF bytes.
    assert view['claims'][0]['effective_status']=='confirmed'
    assert 'changed' in portal_chain(p,uid,'default','export_markdown')
    assert 'This benchmark result appears credible' in portal_chain(p,uid,'default','export_markdown')
    events=portal_chain(p,uid,'default','history',c['id'])['history']
    assert len(events)==2 and events[0]['action']=='confirmed'
    with p.workspace(uid) as db:
        with pytest.raises(sqlite3.DatabaseError,match='immutable'):
            db.execute('DELETE FROM research_claim_audit WHERE claim_id=?',(c['id'],))
        with pytest.raises(sqlite3.DatabaseError,match='immutable'):
            db.execute('UPDATE research_claim_audit SET body=? WHERE claim_id=?',('bad',c['id']))


def test_pdf_anchor_history_restriction_and_fulltext_endpoint(tmp_path):
    p,[uid]=setup_portal(tmp_path)
    a=anchor(p,uid,'2601.12345')
    doc=portal_chain(p,uid,'default','page_source','2601.12345',1)
    assert doc['valid'] and doc['chunks'][0]['text']
    assert QUOTE in doc['chunks'][0]['text']
    with pytest.raises(ValueError,match='主题'):
        portal_chain(p,uid,'default','page_source','other',1)
    with pytest.raises(ValueError):
        portal_chain(p,uid,'default','history',a['id'])
    with p.workspace(uid) as db:
        assert db.execute('SELECT name FROM sqlite_master WHERE type="trigger" AND name="research_claim_audit_no_update"').fetchone()


def test_shared_trace_ui_asset_and_routes(tmp_path):
    root=Path(__file__).resolve().parents[1]
    from paper_radar import __version__
    from paper_radar.startup_update import parsed_version
    assert parsed_version(__version__) >= (1, 33, 0)
    script=(root/'paper_radar/static/trace_workbench.js').read_text(encoding='utf-8')
    assert 'window.PRTrace={mount}' in script and 'textContent' in script
    assert 'claim_id' in script and '/history?' in script
    for page in ('portal.html','index.html'):
        html=(root/'paper_radar/static'/page).read_text(encoding='utf-8')
        assert 'trace_workbench.js' in html
        assert 'TraceWorkbench' in html
    public=(root/'paper_radar/public_portal.py').read_text()
    local=(root/'paper_radar/webapp.py').read_text()
    assert '/trace_workbench.js' in public and '/trace_workbench.js' in local
    assert '/api/public/trace/history' in public and '/api/trace/history' in local
