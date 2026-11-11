"""RealPDF30 integration, versioned parser, strict evidence provenance."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path

import pytest
from paper_radar.core import connect, save_papers
from paper_radar.models import Paper
from paper_radar.fulltext import (index_paper, search_evidence, MAX_PAGES, PARSER_VERSION)
from tools.triage_realpdf30 import assessment, triage
from tools.eval_realpdf30 import load_dataset, prepare_scratch, normalized_text

BASE=Path(__file__).resolve().parents[1]
DATASET=BASE/'benchmarks'/'real_pdf30'


def create_pdf(path:Path,pages:int=185):
    fitz=pytest.importorskip('fitz')
    doc=fitz.open()
    for index in range(1,pages+1):
        page=doc.new_page()
        text=('Methods. Experimental measurements on operator learning. '
              'Graph resolution comparison gives RMSE 0.18 on test data.')
        if index==167:
            text='APPENDIX AFTER 100 PAGES. UNIQUE-LATE-EVIDENCE neural operator evaluation RMSE 0.0123'
        page.insert_text((72,140),text)
    doc.save(path)
    doc.close()


def test_long_pdf_185_pages_keeps_late_evidence_and_full_index(tmp_path):
    assert MAX_PAGES>=185
    path=tmp_path/'downloads'/'long.pdf';path.parent.mkdir()
    create_pdf(path)
    db=tmp_path/'data'/'papers.sqlite3'
    conn=connect(db)
    try:
        paper=Paper(paper_id='long-test',title='Long paper',abstract='',authors='',
                    published='2026-01-01',updated='2026-01-01',category='cs.LG',
                    url='https://arxiv.org/abs/2010.08895',pdf_url='',local_file='long.pdf')
        save_papers(conn,[paper])
        result=index_paper(conn,'long-test',db_path=db)
        assert result['status']=='indexed' and result['pages']==185
        hits=search_evidence(conn,'UNIQUE-LATE-EVIDENCE',paper_id='long-test')
        assert any(hit['page']==167 for hit in hits)
        assert index_paper(conn,'long-test',db_path=db)['status']=='cached'
    finally:
        conn.close()


def test_portal_worker_handles_185_pages_without_truncating_tail(tmp_path):
    from paper_radar.portal_pdf_worker import MAX_PAGES, parse
    assert MAX_PAGES>=185
    file=tmp_path/'study.pdf'
    create_pdf(file)
    result=parse(file)
    assert result['total_pages']==185
    assert len(result['pages'])==185
    assert 'UNIQUE-LATE-EVIDENCE' in result['pages'][166]['text']


def test_real_pdf_30_manifest_and_annotation_sets():
    manifest,rows=load_dataset(DATASET)
    assert len(manifest['papers'])==30 and len(rows)==117
    assert sum(int(x['pages']) for x in manifest['papers'])==1019
    assert any(int(c['page'])==167 for c in rows)
    assert PARSER_VERSION.endswith('long-doc')


def test_source_hash_mismatch_prevents_index_and_gold_pollution(tmp_path):
    doc=tmp_path/'dataset'/'pdfs';doc.mkdir(parents=True)
    (doc/'fakev1.pdf').write_bytes(b'%PDF-fake original')
    manifest=[dict(paper_id='fake',version='v1',title='fake',category='operator',
                   arxiv_url='https://arxiv.org/abs/0000.00001',pdf_url='',sha256='0'*64)]
    with pytest.raises(ValueError,match='SHA-256 mismatch'):
        prepare_scratch(tmp_path/'dataset',tmp_path/'scratch',manifest,doc)


def test_review_triage_flags_bibliographic_numeric_noise(tmp_path):
    ref={'source_quote':'Our experiments define relative RMSE based on [44] as in the benchmark',
         'metric_candidate':'RMSE','value_candidate':'44'}
    got=assessment(ref)
    assert got['review_priority']=='low'
    assert 'number_matches_bibliographic_reference' in got['risk_flags']
    assert got['automatic_gold_eligible']=='no'
    result=triage(DATASET,tmp_path)
    assert result['total']==117 and result['represented_papers']==30
    assert (tmp_path/'review_queue.csv').is_file()


def test_corpus_automated_candidates_are_not_gold():
    from runpy import run_path
    scope=run_path(str(DATASET/'scripts'/'validate_and_score.py'))
    result=scope['validate'](DATASET)
    assert result['status']=='pending_human_review'
    assert result['accepted_human_gold']==0
    assert result['model_precision'] is None and result['model_f1'] is None


def test_needs_recheck_from_two_reviewers_is_never_gold(tmp_path):
    import csv
    from runpy import run_path
    dataset=tmp_path/'benchmark'
    (dataset/'annotations').mkdir(parents=True)
    (dataset/'manifest.json').write_text(json.dumps({'papers':[{'paper_id':'p','version':'v1',
        'sha256':'a'*64,'pages':1}]}),encoding='utf-8')
    fields=['candidate_id','paper_id','pdf_sha256','page','source_quote',
            'method_candidate','dataset_candidate','metric_candidate','value_candidate']
    row={'candidate_id':'X1','paper_id':'p','pdf_sha256':'a'*64,'page':'1',
         'source_quote':'RMSE of model A is 0.1','method_candidate':'A',
         'dataset_candidate':'D','metric_candidate':'RMSE','value_candidate':'0.1'}
    with (dataset/'annotations/candidate_evidence.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerow(row)
    rf=fields+['decision','correct_metric','correct_value','correct_dataset','correct_method',
               'correct_baseline','correct_setting','correct_quote','reviewer']
    for person in ('A','B'):
        with (dataset/f'annotations/review_{person}.csv').open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=rf);writer.writeheader()
            writer.writerow({**row,'decision':'needs_recheck','reviewer':person})
    with (dataset/'annotations/adjudicated_gold.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=rf);writer.writeheader()
    script=BASE/'benchmarks/real_pdf30/scripts/validate_and_score.py'
    validate=run_path(str(script))['validate']
    result=validate(dataset)
    assert result['status']=='pending_human_review'
    assert result['accepted_human_gold']==0 and result['model_f1'] is None


def test_scoring_verifies_source_bytes_before_using_double_reviewed_gold(tmp_path):
    """Synthetic fixture: a complete review cannot certify an altered PDF."""
    import csv
    from runpy import run_path
    fitz=pytest.importorskip('fitz')
    folder=tmp_path/'bench'
    (folder/'annotations').mkdir(parents=True)
    (folder/'pdfs').mkdir()
    quote='Model A evaluated on Dataset D has RMSE 0.123 compared with baseline.'
    pdf_path=folder/'pdfs'/'p000v1.pdf'
    doc=fitz.open()
    page=doc.new_page()
    page.insert_text((70,100),quote)
    doc.save(pdf_path)
    doc.close()
    sha=hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    (folder/'manifest.json').write_text(json.dumps({'papers':[
        {'paper_id':'p000','version':'v1','pages':1,'sha256':sha}]}),encoding='utf-8')
    fields=['candidate_id','paper_id','pdf_sha256','page','source_quote',
            'method_candidate','dataset_candidate','metric_candidate','value_candidate']
    row=dict(candidate_id='C1',paper_id='p000',pdf_sha256=sha,page='1',source_quote=quote,
             method_candidate='Model A',dataset_candidate='Dataset D',
             metric_candidate='RMSE',value_candidate='0.123')
    all_fields=fields+['decision','correct_metric','correct_value','correct_dataset',
                       'correct_method','correct_baseline','correct_setting','correct_quote','reviewer']
    for fname,records,headers in [
        ('candidate_evidence.csv',[row],fields),
        ('review_A.csv',[{**row,'decision':'accepted','reviewer':'A'}],all_fields),
        ('review_B.csv',[{**row,'decision':'accepted','reviewer':'B'}],all_fields),
        ('adjudicated_gold.csv',[],all_fields),
    ]:
        with (folder/'annotations'/fname).open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=headers)
            writer.writeheader()
            writer.writerows(records)
    prediction=folder/'pred.json'
    prediction.write_text(json.dumps({'records':[{
        'paper_id':'p000','page':'1','method':'Model A','dataset':'Dataset D',
        'metric':'RMSE','value':'0.123'}]}),encoding='utf-8')
    validate=run_path(str(DATASET/'scripts'/'validate_and_score.py'))['validate']
    good=validate(folder,predictions=prediction)
    assert good['model_f1']==1.0 and good['status']=='human_annotated_complete'
    pdf_path.write_bytes(b'%PDF-1.5\n%%EOF\n')
    changed=validate(folder,predictions=prediction)
    assert changed['model_f1'] is None
    assert changed['scoring_blocker']=='source_pdf_missing_or_changed'
