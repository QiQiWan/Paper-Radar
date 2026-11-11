# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Batch pipeline: real local PDFs, strict human approval and HTTP contracts."""
from __future__ import annotations
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import patch
import json
import pytest

from paper_radar import webapp
from paper_radar.core import DEFAULTS,connect,save_papers
from paper_radar.models import Paper
from paper_radar.evidence_batch import (create_run,preview,process_run,list_run,latest_run,
    resume_run,review_candidate,comparison_matrix,comparison_csv)
from test_v112_fulltext_evaluation import make_pdf


def fixture(tmp_path:Path, *, count=2,missing=False):
    p=tmp_path/'data'/'papers.sqlite3';db=connect(p)
    papers=[]
    for i in range(count):
        papers.append(Paper(paper_id=f'evidence-{i}',
            title=f'Neural operator mesh geometry generalization study {i}',
            abstract='Neural operator evaluates geometry and mesh transfer on benchmark data.',
            authors='Researcher',published='2026-10-01',updated='2026-10-01',
            category='cs.LG',url='https://example.org',pdf_url='',source='arxiv'))
    save_papers(db,papers,topic_id='default')
    out=tmp_path/'downloads';out.mkdir(exist_ok=True)
    for i in range(count-(1 if missing else 0)):
        pdf=out/f'sample-{i}.pdf'
        make_pdf(pdf,[f'Experiments. On MeshBench, RMSE 0.0123 in split A compared with baseline 0.050 for neural operator variant {i}.',
                      'Results. Evaluated on geometry and meshes across multiple conditions.'])
        db.execute("UPDATE papers SET local_file=?,download_status='downloaded' WHERE paper_id=?",
                   (pdf.name,f'evidence-{i}'))
    db.commit();return db,p


def review(db,path,topic,candidate):
    return review_candidate(db,topic,candidate['id'],revision=candidate['revision'],
        decision='approved',metric='RMSE',value='0.0123',dataset='MeshBench',
        baseline='baseline',setting='split A',review_note='已查看本地 PDF 第 1 页并逐字核对实验信息。',
        expected_sha256=candidate['pdf_sha256'],db_path=path)


def test_preview_relevance_membership_and_no_synthetic_candidates(tmp_path):
    db,path=fixture(tmp_path,count=2)
    result=preview(db,'default','神经算子在不同网格和几何条件下的泛化能力？')
    assert len(result['papers'])==2 and result['matches']==2
    with pytest.raises(ValueError,match='不属于'):create_run(db,'other','神经算子跨几何泛化', ['evidence-0'])
    with pytest.raises(ValueError):create_run(db,'default','神经算子跨几何泛化',['evidence-0']*2)
    with pytest.raises(ValueError):preview(db,'default','abc')
    db.close()


def test_real_pdf_batch_review_and_comparison_no_auto_ranking(tmp_path):
    pytest.importorskip('pypdf')
    db,path=fixture(tmp_path)
    cfg={**DEFAULTS,'llm_data_consent':False,'active_topic_id':'default'}
    run=create_run(db,'default','神经算子在不同网格和几何条件下的泛化能力？',
                   ['evidence-0','evidence-1'],use_model=False)
    result=process_run(path,run['run_id'],cfg)
    assert result['prepared']==2 and result['failed']==0 and result['review_candidates']>0
    summary=list_run(db,'default',run['run_id'],db_path=path)
    assert summary['counts']['pending']>0
    assert all(x['effective_status']=='pending' for x in summary['candidates'])
    assert all(row['state']=='ready_for_review' for row in summary['items'])
    unique={}
    for c in summary['candidates']:
        unique.setdefault(c['paper_id'],c)
    assert set(unique)=={'evidence-0','evidence-1'}
    for paper,c in unique.items():
        done=review(db,path,'default',c)
        assert done['decision']=='approved' and done['anchor_id']
        with pytest.raises(ValueError,match='版本|更改'):review(db,path,'default',c)
    matrix=comparison_matrix(db,'default',db_path=path)
    assert len(matrix['groups'])==1
    assert matrix['groups'][0]['metric']=='RMSE'
    assert matrix['groups'][0]['comparable'] is False
    assert len({entry['paper_id'] for entry in matrix['groups'][0]['entries']})==2
    assert all(entry['page']==1 and len(entry['pdf_sha256'])==64 for entry in matrix['groups'][0]['entries'])
    assert b'evidence-0' in comparison_csv(matrix)
    assert list_run(db,'default',run['run_id'],db_path=path)['counts']['approved']==2
    assert len([x for x in db.execute("SELECT * FROM research_anchors WHERE topic_id='default'")])==2
    with pytest.raises(ValueError):review(db,path,'foreign',unique['evidence-0'])
    # Resuming a completed run is idempotent; no approved evidence is overwritten.
    assert resume_run(db,'default',run['run_id'])['queued']==0
    again=process_run(path,run['run_id'],cfg)
    assert again['prepared']==0
    assert list_run(db,'default',run['run_id'],db_path=path)['counts']['approved']==2
    db.close()


def test_replaced_pdf_invalidates_review_and_comparison(tmp_path):
    db,path=fixture(tmp_path)
    cfg={**DEFAULTS,'llm_data_consent':False}
    rid=create_run(db,'default','神经算子跨网格和几何泛化性能对比？', ['evidence-0','evidence-1'])['run_id']
    process_run(path,rid,cfg)
    records=list_run(db,'default',rid,db_path=path)['candidates']
    for pid in ('evidence-0','evidence-1'):
        review(db,path,'default',next(x for x in records if x['paper_id']==pid))
    assert comparison_matrix(db,'default',db_path=path)['groups']
    make_pdf(tmp_path/'downloads'/'sample-1.pdf',['Experiments. Altered unrelated document, RMSE 0.99.'])
    status=list_run(db,'default',rid,db_path=path)
    assert status['counts']['stale']>=1
    assert comparison_matrix(db,'default',db_path=path)['groups']==[]
    assert comparison_matrix(db,'default',db_path=path)['stale_excluded']>=1
    db.close()


def test_failure_isolation_and_resume_unavailable(tmp_path):
    db,path=fixture(tmp_path,count=2,missing=True)
    rid=create_run(db,'default','神经算子在不同网格几何的泛化表现？', ['evidence-0','evidence-1'])['run_id']
    def fake_download(conn,ids,cfg,*,db_path,logger):
        if ids==['evidence-1']:return [{'status':'unavailable','error':'no public PDF'}]
        return [{'status':'skipped'}]
    result=process_run(path,rid,DEFAULTS,downloader=fake_download)
    assert result['prepared']==1 and result['no_pdf']==1
    assert list_run(db,'default',rid,db_path=path)['run']['state']=='partial'
    assert resume_run(db,'default',rid)['queued']==0
    assert resume_run(db,'default',rid,retry_unavailable=True)['queued']==1
    assert process_run(path,rid,DEFAULTS,downloader=fake_download)['no_pdf']==1
    assert latest_run(db,'default')==rid
    db.close()


def test_quote_and_metric_require_exact_document_evidence(tmp_path):
    db,path=fixture(tmp_path)
    rid=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',['evidence-0'])['run_id']
    process_run(path,rid,DEFAULTS)
    cand=list_run(db,'default',rid,db_path=path)['candidates'][0]
    with pytest.raises(ValueError,match='原文'):review_candidate(db,'default',cand['id'],
        revision=1,decision='approved',metric='RMSE',value='999%',dataset='',baseline='',
        setting='',review_note='已查看 PDF',expected_sha256=cand['pdf_sha256'],db_path=path)
    with pytest.raises(ValueError):review_candidate(db,'default',cand['id'],
        revision=1,decision='approved',metric='RMSE',value='0.0123',dataset='',baseline='',
        setting='',review_note='已查看 PDF',expected_sha256='0'*64,db_path=path)
    assert db.execute('SELECT COUNT(*) FROM research_anchors').fetchone()[0]==0
    db.close()


def test_http_preview_start_status_review_csrf_and_csv(tmp_path,monkeypatch):
    db,path=fixture(tmp_path);db.close()
    @contextmanager
    def local_db():
        c=connect(path)
        try:
            with c:yield c
        finally:c.close()
    server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
    server.token='v150-batch-token'
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    monkeypatch.setattr(webapp,'DB_FILE',path)
    monkeypatch.setattr(webapp,'open_database',local_db)
    monkeypatch.setattr(webapp,'read_config',lambda:{**DEFAULTS,'active_topic_id':'default'})
    monkeypatch.setattr(webapp,'config_health',lambda p:{'read_only':False})
    started=[]
    monkeypatch.setattr(webapp.runtime,'start',lambda mode,ids:started.append((mode,ids)))
    def post(path_,obj,csrf=True):
        headers={'Content-Type':'application/json','Origin':base}
        if csrf:headers['X-Local-Token']=server.token
        return urlopen(Request(base+path_,data=json.dumps(obj).encode(),headers=headers),timeout=8)
    try:
        payload={'question':'神经算子在不同网格和几何条件下的泛化能力？','limit':2}
        with pytest.raises(HTTPError) as forbidden:post('/api/evidence-batch/preview',payload,False)
        assert forbidden.value.code==403
        with post('/api/evidence-batch/preview',payload) as req: assert json.load(req)['matches']==2
        payload={'question':payload['question'],'paper_ids':['evidence-0','evidence-1'],
                 'use_model':False,'confirm_transfer':False}
        with post('/api/evidence-batch/start',payload) as req:
            assert req.status==202;rid=json.load(req)['run_id']
        assert started==[('evidence_batch',[rid])]
        process_run(path,rid,DEFAULTS)
        with urlopen(base+'/api/evidence-batch/status?run_id='+rid) as req:
            status=json.load(req)
        assert status['counts']['pending']>0
        candidate=status['candidates'][0]
        action={'candidate_id':candidate['id'],'revision':candidate['revision'],
                'decision':'approved','metric':'RMSE','value':'0.0123','dataset':'MeshBench',
                'baseline':'baseline','setting':'split A','review_note':'已核对原文 PDF 及数值。',
                'expected_sha256':candidate['pdf_sha256']}
        with post('/api/evidence-batch/review',action) as req:
            assert json.load(req)['decision']=='approved'
        with urlopen(base+'/api/evidence-batch/compare') as req:
            assert json.load(req)['approved_valid']==1
        with urlopen(base+'/api/evidence-batch/export') as req:
            assert req.status==200 and req.headers.get_content_type()=='text/csv'
    finally:
        server.shutdown();server.server_close();thread.join(3)


def test_repeated_new_batch_reuses_pending_evidence_without_duplicate(tmp_path):
    db,path=fixture(tmp_path)
    ids=['evidence-0']
    first=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',ids)['run_id']
    process_run(path,first,DEFAULTS)
    count=db.execute('SELECT COUNT(*) FROM evidence_batch_candidates').fetchone()[0]
    second=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',ids)['run_id']
    process_run(path,second,DEFAULTS)
    assert db.execute('SELECT COUNT(*) FROM evidence_batch_candidates').fetchone()[0]==count
    assert list_run(db,'default',second,db_path=path)['counts']['pending']>0
    assert list_run(db,'default',first,db_path=path)['counts']['pending']==0
    db.close()


def test_model_extraction_to_pending_queue_never_auto_approves(tmp_path,monkeypatch):
    from paper_radar import evidence_batch
    from paper_radar.experiments import persist_findings
    from paper_radar.fulltext import paper_status
    db,path=fixture(tmp_path)
    rid=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',['evidence-0'],use_model=True)['run_id']
    monkeypatch.setattr(evidence_batch,'_model_available',lambda cfg:True)
    calls=[]
    def fake_extractor(pid,cfg,*,db_path,logger):
        calls.append(pid)
        with open_database(db_path) as other:
            chunk=other.execute('SELECT id,page,content FROM fulltext_chunks WHERE paper_id=? ORDER BY id LIMIT 1',(pid,)).fetchone()
            doc=paper_status(other,pid,db_path=db_path)['document']
            return persist_findings(other,pid,doc,'test-model-signature',[{
                'kind':'metric','dataset':'MeshBench','metric':'RMSE','value':'0.0123',
                'baseline':'baseline','setting':'split A','chunk_id':chunk['id'],
                'page':chunk['page'],'quote':chunk['content']}])
    from paper_radar.core import open_database
    result=process_run(path,rid,DEFAULTS,extractor=fake_extractor)
    assert calls==['evidence-0'] and result['model_used']
    rows=list_run(db,'default',rid,db_path=path)['candidates']
    assert rows and rows[0]['source']=='model_verified_quote'
    assert rows[0]['effective_status']=='pending'
    assert comparison_matrix(db,'default',db_path=path)['approved_valid']==0
    db.close()


def test_inquiry_reads_question_scoped_reviewed_matrix_and_markdown(tmp_path):
    from paper_radar.research_intelligence import run_local,read_local
    db,path=fixture(tmp_path)
    run=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',
                   ['evidence-0','evidence-1'])
    process_run(path,run['run_id'],DEFAULTS)
    candidates=list_run(db,'default',run['run_id'],db_path=path)['candidates']
    for pid in ('evidence-0','evidence-1'):
        review(db,path,'default',next(x for x in candidates if x['paper_id']==pid))
    db.close()
    report=run_local(path,'default','神经算子在不同网格和几何下的泛化实验有哪些局限？')
    current=read_local(path,'default',report['id'])
    assert current['brief']['findings']==[]  # No reviewed *scientific claim* invented.
    assert current['reviewed_experiments']['approved_valid']>=2
    assert current['reviewed_experiments']['groups']
    assert '当前人工核对的实验数值' in current['markdown']
    assert 'PDF 第 1 页' in current['markdown']


def test_diagnostic_export_includes_batch_steps_without_research_text(tmp_path):
    from io import BytesIO
    from zipfile import ZipFile
    from paper_radar.runtime_diagnostics import RuntimeDiagnostics
    db,path=fixture(tmp_path)
    secret='SPECIFIC_RESEARCH_QUESTION_DO_NOT_EXPORT'
    rid=create_run(db,'default',secret+' 研究实验条件？',['evidence-0'])['run_id']
    process_run(path,rid,DEFAULTS)
    db.close()
    diagnostic=RuntimeDiagnostics(tmp_path)
    with ZipFile(BytesIO(diagnostic.export())) as archive:
        assert 'evidence_batches.json' in archive.namelist()
        content=archive.read('evidence_batches.json').decode('utf-8')
        assert rid in content and 'download' in content and 'review' in content
        assert secret not in content and 'MeshBench' not in content
        manifest=json.loads(archive.read('manifest.json'))
        assert 'evidence_batches.json' in manifest['included']


def test_withdraw_review_removes_orphan_anchor_and_matrix_entry(tmp_path):
    db,path=fixture(tmp_path)
    rid=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',['evidence-0'])['run_id']
    process_run(path,rid,DEFAULTS)
    candidate=list_run(db,'default',rid,db_path=path)['candidates'][0]
    approved=review(db,path,'default',candidate)
    assert db.execute('SELECT COUNT(*) FROM research_anchors WHERE id=?',
                      (approved['anchor_id'],)).fetchone()[0]==1
    withdrawn=review_candidate(db,'default',candidate['id'],revision=approved['revision'],
        decision='rejected',metric='',value='',dataset='',baseline='',setting='',
        review_note='再次核对后排除此项实验数据。',expected_sha256=candidate['pdf_sha256'],db_path=path)
    assert withdrawn['anchor_id']==''
    assert db.execute('SELECT COUNT(*) FROM research_anchors WHERE id=?',
                      (approved['anchor_id'],)).fetchone()[0]==0
    assert comparison_matrix(db,'default',db_path=path)['approved_valid']==0
    db.close()


def test_one_checksum_check_per_paper_for_large_review_queue(tmp_path,monkeypatch):
    from paper_radar import evidence_batch
    db,path=fixture(tmp_path)
    rid=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',
                   ['evidence-0','evidence-1'])['run_id']
    process_run(path,rid,DEFAULTS)
    call_count=[]
    original=evidence_batch.paper_status
    def counted(conn,pid,*,db_path):
        call_count.append(pid)
        return original(conn,pid,db_path=db_path)
    monkeypatch.setattr(evidence_batch,'paper_status',counted)
    _=list_run(db,'default',rid,db_path=path)
    assert len(call_count)==2
    call_count.clear()
    records=list_run(db,'default',rid,db_path=path)['candidates']
    for pid in ('evidence-0','evidence-1'):
        review(db,path,'default',next(x for x in records if x['paper_id']==pid))
    call_count.clear()
    matrix=comparison_matrix(db,'default',db_path=path)
    assert matrix['approved_valid']==2 and len(call_count)==2
    db.close()


def test_indexed_pdf_without_experimental_metrics_reports_partial(tmp_path):
    db,path=fixture(tmp_path,count=1)
    make_pdf(tmp_path/'downloads'/'sample-0.pdf',
             ['Methods and definitions. This article describes a numerical model.',
              'Conclusion. Descriptive results without explicit error metrics.'])
    rid=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',['evidence-0'])['run_id']
    result=process_run(path,rid,DEFAULTS)
    assert result['state']=='partial' and result['no_experiment']==1
    data=list_run(db,'default',rid,db_path=path)
    assert data['items'][0]['state']=='no_experiment'
    assert data['counts']['pending']==0
    assert resume_run(db,'default',rid)['queued']==0
    assert resume_run(db,'default',rid,retry_unavailable=True)['queued']==1
    db.close()


def test_model_requested_but_not_ready_does_not_silently_claim_ai(tmp_path):
    db,path=fixture(tmp_path,count=1)
    rid=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',
                   ['evidence-0'],use_model=True)['run_id']
    result=process_run(path,rid,{**DEFAULTS,'llm_data_consent':False})
    assert result['model_status']=='not_ready' and result['model_attempts']==0
    assert list_run(db,'default',rid,db_path=path)['run']['model_status']=='not_ready'
    assert result['review_candidates']>=1
    db.close()


def test_model_error_isolated_and_status_reports_fallback(tmp_path,monkeypatch):
    from paper_radar import evidence_batch
    db,path=fixture(tmp_path,count=1)
    rid=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',
                   ['evidence-0'],use_model=True)['run_id']
    monkeypatch.setattr(evidence_batch,'_model_available',lambda _:True)
    def bad_model(*args,**kwargs):
        raise RuntimeError('external model unavailable')
    result=process_run(path,rid,DEFAULTS,extractor=bad_model)
    assert result['model_status']=='failed_fallback'
    assert result['model_attempts']==1 and result['model_failures']==1
    assert result['review_candidates']>=1
    events=list_run(db,'default',rid,db_path=path)['events']
    assert any(event['outcome']=='model_failed' for event in events)
    db.close()


def test_retry_changed_pdf_rebuilds_candidates_and_keeps_old_stale(tmp_path):
    db,path=fixture(tmp_path,count=1)
    rid=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',
                   ['evidence-0'])['run_id']
    process_run(path,rid,DEFAULTS)
    old=list_run(db,'default',rid,db_path=path)['candidates'][0]
    review(db,path,'default',old)
    make_pdf(tmp_path/'downloads'/'sample-0.pdf',
             ['Experiments. On MeshBench, RMSE 0.0432 in split B versus baseline 0.030 for neural operator.'])
    assert list_run(db,'default',rid,db_path=path)['counts']['stale']>=1
    assert resume_run(db,'default',rid,retry_unavailable=True,db_path=path)['queued']==1
    result=process_run(path,rid,DEFAULTS)
    assert result['review_candidates']>=1
    newest=list_run(db,'default',rid,db_path=path)
    assert newest['counts']['stale']>=1 and newest['counts']['pending']>=1
    assert newest['counts']['approved']==0
    db.close()


def test_review_rejects_truncated_numerical_precision(tmp_path):
    db,path=fixture(tmp_path,count=1)
    rid=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',
                   ['evidence-0'])['run_id']
    process_run(path,rid,DEFAULTS)
    item=list_run(db,'default',rid,db_path=path)['candidates'][0]
    with pytest.raises(ValueError,match='完整数字'):
        review_candidate(db,'default',item['id'],revision=item['revision'],
            decision='approved',dataset='MeshBench',metric='RMSE',value='0.012',
            baseline='baseline',setting='split A',review_note='已核对原文。',
            expected_sha256=item['pdf_sha256'],db_path=path)
    db.close()


def test_one_click_batch_of_twelve_keeps_each_pdf_independent(tmp_path):
    db,path=fixture(tmp_path,count=12)
    ids=[f'evidence-{i}' for i in range(12)]
    rid=create_run(db,'default','神经算子跨网格和几何泛化性能对比？',ids)['run_id']
    result=process_run(path,rid,DEFAULTS)
    assert result['prepared']==12 and result['review_candidates']>=12
    assert result['state']=='complete'
    status=list_run(db,'default',rid,db_path=path)
    assert len(status['items'])==12
    assert len({item['paper_id'] for item in status['candidates']})==12
    with pytest.raises(ValueError,match='12'):
        create_run(db,'default','神经算子跨网格和几何泛化性能对比？',ids+['other'])
    db.close()
