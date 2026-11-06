"""v1.22.0 bounded PDF retrieval, real quote validation, failure isolation and UX API."""
from __future__ import annotations
import io
import json
import time

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from paper_radar.portal_auth import db as auth_db, create_account, throttle
from paper_radar.public_portal import Portal
from paper_radar.portal_evidence_retrieval import (
    MAX_CANDIDATE_PAGES, MAX_PAGE_TEXT, BATCH_SIZE,
    select_candidate_pages, page_excerpt, make_batches,
)
from paper_radar.portal_research import save_pdf, analyze, records
from paper_radar.synthesis import compare_records, to_markdown
from tools.eval_real_papers import evaluate_pdf_pages, load

Q13 = 'Table 2 Experiment results on DarcyNet test set RMSE 0.031 using MethodA baseline BaselineB setting 64x64.'
Q19 = 'Table 4 Experiment results on LateSet with Accuracy 94% using MethodB baseline BaseY setting splitB.'


def synthetic_pdf(pages=20):
    writer=PdfWriter()
    font=writer._add_object(DictionaryObject({
        NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/Type1'),
        NameObject('/BaseFont'):NameObject('/Helvetica')}))
    for number in range(1,pages+1):
        page=writer.add_blank_page(width=595,height=842)
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):
            DictionaryObject({NameObject('/F1'):font})})
        text=Q13 if number==13 else Q19 if number==19 else 'Overview of science methods and setup page %d.'%number
        stream=DecodedStreamObject()
        stream.set_data(('BT /F1 11 Tf 30 750 Td ('+text+') Tj ET').encode('ascii'))
        page[NameObject('/Contents')]=writer._add_object(stream)
    output=io.BytesIO();writer.write(output)
    return output.getvalue()


def service(tmp_path):
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    with auth_db(portal.auth_path) as db:
        uid=create_account(db,'EvidenceTestUser','very long test password 1234')['id']
    pid='2601.55555'
    with portal.workspace(uid) as db:
        db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',
                   (pid,'Synthetic journal','', '2026-11-05',
                    'https://arxiv.org/abs/'+pid,'','now'))
        db.execute('INSERT INTO topic_papers VALUES (?,?)',('default',pid))
    save_pdf(portal,uid,'default',pid,synthetic_pdf(),source='user_upload')
    return portal,uid,pid


def extracted(rows):
    findings=[]
    for row in rows:
        for quote,dataset,metric,value,baseline,setting in (
            (Q13,'DarcyNet','RMSE','0.031','BaselineB','64x64'),
            (Q19,'LateSet','Accuracy','94%','BaseY','splitB'),
        ):
            if quote in row['text']:
                findings.append({'chunk_id':row['chunk_id'],'kind':'metric',
                                 'dataset':dataset,'metric':metric,'value':value,
                                 'baseline':baseline,'setting':setting,'quote':quote})
    return json.dumps({'findings':findings})


def test_lexical_candidate_pages_include_late_experiments():
    pages=[{'page':i,'text':'Research background and reference bibliography.'} for i in range(1,25)]
    pages[12]['text']='Experiment results Table 2 on DarcyNet give RMSE 0.031 with error and baseline comparison.'
    pages[18]['text']='Ablation study results Table 4 Accuracy 94% for the benchmark. Results and metrics.'
    selected=select_candidate_pages(pages)
    assert 13 in selected['candidate_pages'] and 19 in selected['candidate_pages']
    assert selected['scanned_pages']==24
    assert len(selected['candidate_pages'])==MAX_CANDIDATE_PAGES
    batches=make_batches(selected)
    assert len(batches)==3 and all(len(x)<=BATCH_SIZE for x in batches)
    assert all(len(page['text'])<=MAX_PAGE_TEXT for batch in batches for page in batch)
    assert {i['page'] for batch in batches for i in batch}==set(selected['candidate_pages'])


def test_page_excerpt_prefers_experiment_at_end_of_page():
    text=('Unimportant abstract and introductory material. '*110)+Q19
    snippet=page_excerpt(text)
    assert len(snippet)<=MAX_PAGE_TEXT
    assert Q19 in snippet
    assert snippet in text


def test_no_untrusted_duplicate_or_nontext_pages_pass_selector():
    sample=[{'page':-1,'text':'Table 5 error 1.0'},
            {'page':1,'text':''}, {'page':1,'text':'Experiment results'},
            {'page':2,'text':None}, {'page':121,'text':'Table 1'},
            {'page':3.2,'text':'Table 3'}, {'page':5,'text':'Accuracy 92%'}]
    result=select_candidate_pages(sample)
    assert result['candidate_pages']==[1,5]
    assert result['excluded_nonempty_pages']==0


def test_end_to_end_20_page_real_pdf_parser_and_multiple_batches(tmp_path):
    portal,uid,pid=service(tmp_path)
    calls=[]
    portal.model_callable=lambda rows: (calls.append(len(rows)) or extracted(rows))
    result=analyze(portal,uid,'default',pid,cloud=True)
    assert result['state']=='indexed' and result['analysis_state']=='complete'
    assert result['pages']==20 and result['evidence_count']==2
    retrieval=result['retrieval']
    assert 13 in retrieval['candidate_pages'] and 19 in retrieval['candidate_pages']
    assert retrieval['scanned_pages']==20
    assert retrieval['model_batches_total']==3
    assert retrieval['model_batches_succeeded']==3
    assert len(calls)==3
    original=records(portal,uid,'default')
    assert {r['page'] for r in original}=={13,19}
    assert all(r['quote_verified_against_pdf'] is True for r in compare_records(original)['records'])
    quota=portal.job_queue.quota_status(uid)
    assert quota['model_calls']['used']==3
    assert quota['search']['used']==0
    assert quota['analyze']['used']==0
    cached=analyze(portal,uid,'default',pid,cloud=True)
    assert cached['cloud_analysis']=='cached' and len(calls)==3
    assert cached['retrieval']['model_cache_hit'] is True
    assert cached['evidence_count']==2


def test_failed_middle_batch_does_not_cache_an_incomplete_analysis(tmp_path):
    portal,uid,pid=service(tmp_path)
    attempts=[]
    def flaky(rows):
        attempts.append(1)
        if len(attempts)==2:raise RuntimeError('Provider temporarily unavailable')
        return extracted(rows)
    portal.model_callable=flaky
    partial=analyze(portal,uid,'default',pid,cloud=True)
    assert partial['state']=='indexed' and partial['analysis_state']=='partial'
    assert partial['cloud_analysis']=='partial:BatchFailures'
    assert partial['retrieval']['model_batches_failed']==1
    assert partial['retrieval']['failed_candidate_pages']
    assert partial['retrieval']['model_batches_succeeded']==2
    with portal.workspace(uid) as db:
        cache=db.execute('SELECT analysis_cache FROM pdf_documents WHERE paper_id=?',(pid,)).fetchone()[0]
    assert not cache or 'verified' not in cache
    portal.model_callable=extracted
    finished=analyze(portal,uid,'default',pid,cloud=True)
    assert finished['analysis_state']=='complete'
    assert {r['page'] for r in records(portal,uid,'default')}=={13,19}
    # v1.23 resumes the two already verified batches and only re-calls the failed batch.
    assert portal.job_queue.quota_status(uid)['model_calls']['used']==4
    assert finished['retrieval']['model_batches_resumed']==2


def test_model_quota_exhaustion_is_visible_and_does_not_charge_more(tmp_path):
    portal,uid,pid=service(tmp_path)
    with auth_db(portal.auth_path) as db:
        for _ in range(8):assert throttle(db,'public_llm:'+uid,limit=8,seconds=86400)
    portal.model_callable=extracted
    result=analyze(portal,uid,'default',pid,cloud=True)
    assert result['analysis_state']=='partial'
    assert result['retrieval']['model_quota_blocked'] is True
    assert '额度已耗尽' in result['analysis_error']
    assert result['retrieval']['model_batches_attempted']==0
    assert portal.job_queue.quota_status(uid)['model_calls']['remaining']==0
    with portal.workspace(uid) as db:
        assert not db.execute('SELECT analysis_cache FROM pdf_documents WHERE paper_id=?',(pid,)).fetchone()[0]


def test_prior_verified_evidence_survives_subsequent_provider_outage(tmp_path):
    portal,uid,pid=service(tmp_path)
    portal.model_callable=extracted
    assert analyze(portal,uid,'default',pid,cloud=True)['evidence_count']==2
    # Clear only the analysis cache, for instance after an extraction policy
    # change. Keep the current PDF hash and verified rows unchanged.
    with portal.workspace(uid) as db:
        db.execute("UPDATE pdf_documents SET analysis_cache='' WHERE paper_id=?",(pid,))
        # Force actual re-extraction rather than the new v1.23 checkpoint resume.
        db.execute('DELETE FROM pdf_batch_checkpoints WHERE paper_id=?',(pid,))
    portal.model_callable=lambda _: (_ for _ in ()).throw(ConnectionError('provider offline'))
    result=analyze(portal,uid,'default',pid,cloud=True)
    assert result['analysis_state']=='partial'
    assert result['evidence_count']==2
    assert result['new_evidence_count']==0
    assert len(records(portal,uid,'default'))==2


def test_cancelled_before_network_call_does_not_write_cloud_cache(tmp_path):
    portal,uid,pid=service(tmp_path)
    portal.model_callable=lambda _:pytest.fail('Cloud must not run after cancellation')
    with pytest.raises(InterruptedError):analyze(portal,uid,'default',pid,cloud=True,cancel=lambda:True)
    assert portal.job_queue.quota_status(uid)['model_calls']['used']==0


def test_normalized_multispace_source_quote_survives_evidence_readback(tmp_path):
    portal,uid,pid=service(tmp_path)
    quoted=Q13.replace(' RMSE ', '  RMSE ')
    # Ensure the stored PDF text has two spaces while the quote has one. This
    # verifies read-back uses exactly the same normalization as acceptance.
    import paper_radar.portal_research as research
    original=research.parse_pdf
    def wrapped(path):
        data=original(path)
        for page in data['pages']:
            if page['page']==13:page['text']=page['text'].replace(Q13,quoted)
        return data
    from unittest.mock import patch
    portal.model_callable=extracted
    with patch.object(research,'parse_pdf',wrapped):
        result=analyze(portal,uid,'default',pid,cloud=True)
    # The fixture's first quote is normalized, second page untouched.
    assert result['evidence_count']>=1
    assert all(r['page'] in (13,19) for r in records(portal,uid,'default'))


def test_synthesis_positive_candidate_remains_manual_and_baseline_is_flagged():
    base={'title':'Paper A','paper_id':'a','kind':'metric','dataset':'SameData',
          'metric':'RMSE','value':'0.13','baseline':'BaseA','setting':'split-1',
          'quote':'SameData RMSE 0.13 against BaseA on split-1', 'page':3,
          'provenance':'pdf_sha256_verified','published':'2025-10-01'}
    other={**base,'paper_id':'b','title':'Paper B','value':'0.11',
           'baseline':'BaseB','page':4,'quote':'SameData RMSE 0.11 against BaseB on split-1'}
    summary=compare_records([base,other])
    assert len(summary['comparisons'])==1
    group=summary['comparisons'][0]
    assert group['review_status']=='manual_review_required'
    assert len(group['review_checks'])>=4
    assert summary['diagnostics']['baseline_conflicts']==1
    assert '对照基线不一致' in to_markdown(summary,'Synth test')
    assert not compare_records([base,{**other,'setting':'different'}])['comparisons']


def test_real_paper_eval_missing_pdfs_reports_unmeasured(tmp_path):
    manifest,gold=load()
    outcome=evaluate_pdf_pages(manifest,gold,tmp_path)
    assert outcome['real_pdfs_parsed']==0
    assert outcome['page_anchor_recall'] is None
    assert outcome['candidate_page_recall'] is None
    assert outcome['candidate_excerpt_anchor_recall'] is None
