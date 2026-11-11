"""v1.23.0 PDF size/page, optional table and resilient checkpoint regressions."""
import io
import json
import sqlite3
from pathlib import Path
import pytest

from paper_radar.portal_auth import db as auth_db
from paper_radar.public_portal import Portal
from paper_radar.portal_pdf_worker import MAX_PAGES
from paper_radar import portal_research as research
from paper_radar.migrations import migrate,REGISTRIES
from tests.test_v122_fulltext_and_synthesis import service,synthetic_pdf,extracted


def test_raised_pdf_bounds_match_proxy_and_browser():
    assert research.MAX_PDF==32*1024*1024
    assert MAX_PAGES==240
    root=Path(__file__).resolve().parents[1]
    assert 'client_max_body_size 34m;' in (root/'deploy/nginx-paper-radar.conf').read_text()
    assert '32*1024*1024' in (root/'paper_radar/static/portal.js').read_text()
    assert 'page<=MAX_EVIDENCE_PAGES' in (root/'paper_radar/public_portal.py').read_text()


def test_65_page_pdf_index_and_deep_preview(tmp_path):
    portal,uid,pid=service(tmp_path)
    research.save_pdf(portal,uid,'default',pid,synthetic_pdf(pages=65))
    result=research.analyze(portal,uid,'default',pid,cloud=False)
    assert result['pages']==65 and result['analysis_state']=='complete'
    late=research.preview(portal,uid,'default',pid,60)
    assert late['document']['pages']==65
    assert 'page 60' in late['text']
    assert late['parser']['text_pages']==65
    with portal.workspace(uid) as conn:
        assert conn.execute('SELECT COUNT(*) FROM pdf_pages').fetchone()[0]==65
        assert conn.execute('SELECT COUNT(*) FROM pdf_parse_profiles').fetchone()[0]==1


def test_reparse_preexisting_index_without_layout_profile(tmp_path,monkeypatch):
    portal,uid,pid=service(tmp_path)
    assert research.analyze(portal,uid,'default',pid,cloud=False)['pages']==20
    with portal.workspace(uid) as conn:
        conn.execute('DELETE FROM pdf_parse_profiles WHERE paper_id=?',(pid,))
    parse=research.parse_pdf
    calls=[]
    def wrapped(path):
        calls.append(1)
        return parse(path)
    monkeypatch.setattr(research,'parse_pdf',wrapped)
    research.analyze(portal,uid,'default',pid,cloud=False)
    assert calls==[1]
    research.analyze(portal,uid,'default',pid,cloud=False)
    assert calls==[1]


def test_crash_retry_reuses_completed_model_batches_across_portal_instances(tmp_path):
    portal,uid,pid=service(tmp_path)
    attempts=[]
    def flaky(batch):
        attempts.append(1)
        if len(attempts)==2:raise ConnectionError('transient provider outage')
        return extracted(batch)
    portal.model_callable=flaky
    part=research.analyze(portal,uid,'default',pid,cloud=True)
    assert part['analysis_state']=='partial'
    assert part['retrieval']['model_batches_succeeded']==2
    with portal.workspace(uid) as conn:
        checkpoints=conn.execute('SELECT COUNT(*) FROM pdf_batch_checkpoints').fetchone()[0]
        assert checkpoints==2
    # Simulate a worker restart by constructing a fresh Portal over the same durable DB.
    restarted=Portal(portal.root,embedded_workers=False)
    restarted.model_callable=lambda batch:(attempts.append(1) or extracted(batch))
    done=research.analyze(restarted,uid,'default',pid,cloud=True)
    assert done['analysis_state']=='complete'
    assert done['retrieval']['model_batches_resumed']==2
    assert done['retrieval']['model_batches_attempted']==1
    assert len(attempts)==4
    assert restarted.job_queue.quota_status(uid)['model_calls']['used']==4
    assert research.analyze(restarted,uid,'default',pid,cloud=True)['cloud_analysis']=='cached'


def test_pdf_replacement_invalidates_layout_and_batch_checkpoints(tmp_path):
    portal,uid,pid=service(tmp_path)
    portal.model_callable=extracted
    assert research.analyze(portal,uid,'default',pid,cloud=True)['evidence_count']==2
    before=research.state(portal,uid,'default',pid)['sha256']
    research.save_pdf(portal,uid,'default',pid,synthetic_pdf(pages=21))
    after=research.state(portal,uid,'default',pid)
    assert after['sha256']!=before and after['state']=='saved'
    with portal.workspace(uid) as conn:
        for table in ('pdf_layout_rows','pdf_batch_checkpoints','pdf_parse_profiles','verified_evidence'):
            assert conn.execute(f'SELECT COUNT(*) FROM {table} WHERE paper_id=?',(pid,)).fetchone()[0]==0
    assert research.analyze(portal,uid,'default',pid,cloud=False)['pages']==21


def test_migration_v5_to_v6_keeps_existing_data(tmp_path,monkeypatch):
    steps=REGISTRIES['portal_workspace']
    monkeypatch.setitem(REGISTRIES,'portal_workspace',steps[:5])
    db=sqlite3.connect(tmp_path/'upgrade.sqlite3')
    assert migrate(db,'portal_workspace')==5
    db.execute("INSERT INTO topics VALUES ('old','Existing topic','[]','2026-01-01')")
    db.commit()
    monkeypatch.setitem(REGISTRIES,'portal_workspace',steps)
    assert migrate(db,'portal_workspace')==len(steps)
    assert db.execute("SELECT name FROM topics WHERE id='old'").fetchone()[0]=='Existing topic'
    assert db.execute('SELECT COUNT(*) FROM pdf_batch_checkpoints').fetchone()[0]==0
    db.close()


def test_parser_enforces_page_limit(tmp_path):
    pdf=tmp_path/'too_long.pdf'
    pdf.write_bytes(synthetic_pdf(pages=241))
    import subprocess,sys
    worker=subprocess.run([sys.executable,'-m','paper_radar.portal_pdf_worker',str(pdf)],
        capture_output=True,text=True,timeout=35)
    assert worker.returncode!=0
    assert json.loads(worker.stdout)['error']=='ValueError'


def test_layout_table_candidates_are_scoped_to_page_and_sha(tmp_path):
    fitz=pytest.importorskip('fitz')
    d=fitz.open();pg=d.new_page(width=500,height=400)
    cells=[['Dataset','RMSE','Accuracy'],['SetA','0.12','91'],['SetB','0.25','95']]
    for ri,row in enumerate(cells):
        for ci,value in enumerate(row):
            x=40+ci*125;y=50+ri*38
            pg.draw_rect(fitz.Rect(x,y,x+125,y+38),color=(0,0,0),width=0.5)
            pg.insert_text((x+9,y+23),value,fontsize=10)
    pdf=d.tobytes();d.close()
    portal,uid,pid=service(tmp_path)
    research.save_pdf(portal,uid,'default',pid,pdf)
    result=research.analyze(portal,uid,'default',pid,cloud=False)
    assert result['pages']==1
    preview=research.preview(portal,uid,'default',pid,1)
    assert preview['parser']['layout_backend']=='pymupdf_optional'
    rows=preview['table_candidates']
    assert rows
    assert any('SetA' in str(row['cells']) and '0.12' in str(row['cells']) for row in rows)
    assert all(row['method']=='pymupdf_table_candidate' for row in rows)
