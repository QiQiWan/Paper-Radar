# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
from __future__ import annotations
from io import BytesIO
import csv
import json
import zipfile
from pathlib import Path
import pytest

from paper_radar.core import connect,save_papers,DEFAULTS
from paper_radar.models import Paper
from paper_radar.abstract_enrichment import quality_summary,run_batch
from paper_radar.research_intelligence import preview_local,run_local,read_local
from paper_radar.research_jobs import create_job,process_job,read_job,resume_job,update_job
from paper_radar.research_acceptance import evaluate,package

QUESTION='Neural operator mesh geometry generalization benchmark experiments'
ABSTRACT=('We propose a neural operator approach for different mesh resolutions and unseen geometries. '
          'We evaluate the model on computational physics benchmarks against a finite element baseline. '
          'Results demonstrate improved transfer across the tested meshes under a fixed data split. '
          'However, this study does not cover unseen material parameters or different PDE families. ')


def fixtures(path,count=72):
    with connect(path) as db:
        papers=[]
        for i in range(count):
            text=ABSTRACT+f' Experiment configuration {i}.'
            title=f'Neural operator mesh geometry comparison {i}'
            if i%13==0:title=f'Neural operator across unseen geometries {i}'
            papers.append(Paper(paper_id=f'2601.{10000+i}',title=title,abstract=text,
                authors='Example Researcher',published='2026-02-10',updated='2026-02-10',
                category='cs.LG',url='',pdf_url='',source='arxiv',doi=''))
        save_papers(db,papers)


def test_full_scope_covers_72_no_silent_eight_paper_cap(tmp_path):
    path=tmp_path/'papers.sqlite3';fixtures(path)
    preview=preview_local(path,'default',QUESTION)
    assert preview['topic_total']==72 and len(preview['papers'])==72
    report=run_local(path,'default',QUESTION,selection_mode='all')['brief']
    assert report['selection']['selected_count']==72
    assert len(report['metadata_candidates'])==72
    assert len(report['analysis_batches'])==9
    assert sum(x['count'] for x in report['analysis_batches'])==72
    assert report['model']['batch_total']==9
    assert report['model']['batch_attempted']==0
    assert report['coverage']['analyzed_abstracts']==72
    assert len(set(report['selection']['selected_ids']))==72


def test_manual_and_recommended_changed_scope_changes_sources(tmp_path):
    path=tmp_path/'papers.sqlite3';fixtures(path,22)
    preview=preview_local(path,'default',QUESTION)
    default=run_local(path,'default',QUESTION)['brief']
    first=preview['recommended_ids'][0]
    different=next(r['paper_id'] for r in preview['papers'] if r['paper_id'] not in preview['recommended_ids'])
    modified=run_local(path,'default',QUESTION,selection_mode='recommended',include_ids=[different],exclude_ids=[first])['brief']
    assert first not in modified['selection']['selected_ids']
    assert different in modified['selection']['selected_ids']
    assert modified['selection']['selection_hash']!=default['selection']['selection_hash']
    manual=run_local(path,'default',QUESTION,selection_mode='manual',paper_ids=[first,different])['brief']
    assert manual['selection']['selected_ids']==[first,different]
    assert len(manual['metadata_candidates'])==2
    assert all(x['paper_id'] in (first,different) for x in manual['abstract_insights'])
    with pytest.raises(ValueError,match='不属于当前主题'):
        run_local(path,'default',QUESTION,selection_mode='manual',paper_ids=['not-in-topic'])


def test_confirmed_source_hash_invalidates_old_report(tmp_path):
    path=tmp_path/'papers.sqlite3';fixtures(path,2)
    report=run_local(path,'default',QUESTION,selection_mode='all')
    with connect(path) as db:
        db.execute('UPDATE papers SET abstract=? WHERE paper_id=?',('A corrected abstract', '2601.10000'))
    saved=read_local(path,'default',report['id'])
    assert saved['snapshot_status']=='needs_refresh'
    assert any('摘要' in x for x in saved['issues'])


def test_model_batches_process_all_72_and_isolate_failed_batch(tmp_path):
    path=tmp_path/'papers.sqlite3';fixtures(path)
    calls=[]
    def model(payload):
        calls.append([p['paper_id'] for p in payload['papers']])
        if len(calls)==4:raise TimeoutError('deliberately simulated')
        return {'reading_questions':[{'question':'What is the evaluation setting?',
            'paper_ids':[payload['papers'][0]['paper_id']]}],
            'abstract_findings':[]}
    report=run_local(path,'default',QUESTION,selection_mode='all',use_model=True,
        confirm_transfer=True,model_callable=model)['brief']
    state=report['model']
    assert len(calls)==9
    assert len(set(x for batch in calls for x in batch))==72
    assert state['batch_attempted']==9 and state['batch_succeeded']==8
    assert state['batch_failed']==1 and state['status']=='model_partially_completed'
    assert any(b.get('error_class')=='TimeoutError' for b in state['batch_details'])
    assert len(report['model_suggestions'])==8


def test_job_progress_resume_idempotency_and_metrics(tmp_path):
    path=tmp_path/'papers.sqlite3';fixtures(path,4)
    request={'question':QUESTION,'selection_mode':'manual','paper_ids':['2601.10000']}
    created=create_job(path,'default',request)
    assert read_job(path,created['run_id'],'default')['state']=='queued'
    seen=[]
    result=process_job(path,created['run_id'],DEFAULTS,progress=lambda p,m:seen.append(p))
    assert result['state']=='completed' and seen[-1]==100
    assert read_job(path,created['run_id'],'default')['report_id']==created['run_id']
    assert result['metrics']['selected_papers']==1
    assert result['metrics']['model_cost_estimate'] is None
    again=process_job(path,created['run_id'],DEFAULTS)
    assert again['report_id']==created['run_id']
    with connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM research_inquiries WHERE id=?',(created['run_id'],)).fetchone()[0]==1
    with pytest.raises(ValueError,match='已完成'):
        resume_job(path,created['run_id'],'default')
    other=create_job(path,'default',request)
    update_job(path,other['run_id'],state='failed',error_code='TEST')
    assert resume_job(path,other['run_id'],'default')['state']=='queued'


def test_abstract_source_unavailable_is_not_infinite_actionable(tmp_path):
    path=tmp_path/'papers.sqlite3';fixtures(path,1)
    with connect(path) as db:
        db.execute("UPDATE papers SET abstract='' WHERE paper_id='2601.10000'")
        assert quality_summary(db,'default')['needs_refresh']==1
    result=run_batch(path,'default',fetcher=lambda _:('', ''))
    assert result['state']=='no_changes'
    with connect(path) as db:
        quality=quality_summary(db,'default')
        assert quality['by_quality']['missing']==1
        assert quality['needs_refresh']==0
        assert quality['checked_without_improvement']==1
        assert quality['checked_details'][0]['reason']=='来源未提供摘要'
    retried=[]
    second=run_batch(path,'default',fetcher=lambda row:retried.append(row) or ('', ''))
    assert second['selected']==0 and retried==[]


def test_true_library_audit_keeps_unobserved_metrics_null(tmp_path):
    path=tmp_path/'papers.sqlite3';fixtures(path)
    result=evaluate(path,expected_count=72,question=QUESTION)
    summ=result['summary']
    assert summ['actual_count']==72 and summ['matches_expected_count']
    assert summ['selection_count']==72 and summ['analysis_batches']==9
    assert summ['pdf_acquisition_rate'] is None
    assert summ['field_accuracy_on_reviewed_fields'] is None
    assert summ['manual_review_seconds'] is None
    assert summ['source_quote_integrity_fraction']==1
    outfile=package(result,tmp_path/'pilot.zip')
    with zipfile.ZipFile(outfile) as z:
        assert json.loads(z.read('summary.json'))['actual_count']==72
        assert len(z.read('paper_coverage.csv'))>1200
