"""v1.21.1 functional regression: provenance, quota and partial extraction."""
import json
import pytest
from paper_radar.portal_auth import db as auth_db, create_account
from paper_radar.portal_jobs import PortalJobs
from paper_radar.synthesis import compare_records
from paper_radar.portal_research import analyze,save_pdf
from paper_radar.public_portal import Portal
from paper_radar.models import Paper
from test_v118_public_pipeline import pdf_bytes
from tools.render_public_nginx import render


def test_pdf_upload_nginx_location_restricts_only_upload():
    nginx=render('papers.example.org',19000)
    assert 'location = /api/public/pdf {' in nginx
    assert 'client_max_body_size 34m;' in nginx
    assert 'client_max_body_size 400k;' in nginx
    assert 'proxy_pass http://127.0.0.1:19000;' in nginx


def test_pdf_verified_upload_and_unverified_import_are_distinct():
    def record(pid, provenance):
        return {'paper_id':pid,'title':pid,'kind':'metric','dataset':'Darcy',
                'metric':'L2 error','value':'0.2','baseline':'BaselineA',
                'setting':'64x64','page':3,'quote':'Darcy L2 error 0.2 64x64 BaselineA',
                'provenance':provenance}
    result=compare_records([record('p1','user_pdf_quote_verified'),
                            record('p2','pdf_sha256_verified'),
                            record('p3','user_import_unverified')])
    assert result['diagnostics']['unverified']==1
    records=result['records']
    assert records[0]['quote_verified_against_pdf'] is True
    assert records[0]['document_source']=='user_upload'
    assert records[0]['bibliographic_identity_checked'] is False
    assert records[2]['quote_verified_against_pdf'] is False
    flags=result['comparisons'][0]['flags']
    assert any('上传 PDF' in f for f in flags)
    assert any('未在服务器核验' in f for f in flags)


def test_synthesis_requests_cannot_use_up_search_limit(tmp_path):
    auth=tmp_path/'auth.sqlite3'
    with auth_db(auth) as db:
        uid=create_account(db,'QuotaUserTest','very long valid test password')['id']
    q=PortalJobs(auth,lambda *_:None,daily_limit=2)
    for _ in range(3):
        job=q.enqueue(uid,'default',kind='synthesize');q.cancel(uid,job['id'])
    assert q.quota_status(uid)['search']['used']==0
    for _ in range(2):
        job=q.enqueue(uid,'default',kind='search');q.cancel(uid,job['id'])
    assert q.quota_status(uid)['search']['remaining']==0
    from paper_radar.portal_jobs import QueueError
    with pytest.raises(QueueError,match='文献检索'):
        q.enqueue(uid,'default',kind='research')
    assert q.enqueue(uid,'default',kind='synthesize')['state']=='queued'


def test_failed_llm_is_partial_even_after_pdf_indexed(tmp_path):
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    with auth_db(portal.auth_path) as db:
        uid=create_account(db,'PartialTester','very long valid test password')['id']
    with portal.workspace(uid) as db:
        db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',('2401.12345','Example','','2026','https://arxiv.org/abs/2401.12345','','now'))
        db.execute('INSERT INTO topic_papers VALUES (?,?)',('default','2401.12345'))
    save_pdf(portal,uid,'default','2401.12345',pdf_bytes())
    portal.model_callable=lambda rows: (_ for _ in ()).throw(RuntimeError('cloud unreachable'))
    result=analyze(portal,uid,'default','2401.12345',cloud=True)
    assert result['state']=='indexed' and result['analysis_state']=='partial'
    assert result['cloud_analysis']=='unavailable:RuntimeError'
    assert '不能据此判断' in result['analysis_error']
    outer=portal.research_job(uid,'default','analyze','2401.12345',{'cloud_analysis':True},lambda:False,lambda stage:None)
    assert outer['state']=='partial' and outer['analysis']['state']=='indexed'
