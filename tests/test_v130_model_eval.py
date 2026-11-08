# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.30 offline authorization, pricing, and evidence-measurement gates."""
from __future__ import annotations
import csv
import hashlib
import http.client
import json
from pathlib import Path
import shutil
from threading import Thread

import pytest

from paper_radar import model_governance as g
from paper_radar.benchmark_quality import summary, review_coverage, adjudicated_quality
from paper_radar.core import DEFAULTS, validate_config

ROOT=Path(__file__).resolve().parents[1]
CORPUS=ROOT/'benchmarks'/'real_papers_v2'


def test_real_corpus_has_33_canonical_distinct_arxiv_records():
    info=review_coverage(CORPUS)
    assert info['papers_total']==33
    assert len(info['topic_distribution'])>=5
    assert info['meta_verified_count']==33
    assert info['real_pdfs_available']==0
    assert info['review_A_completed']==info['review_B_completed']==0
    assert info['double_reviewed']==0


def test_accuracy_and_human_correction_not_fabricated():
    result=summary(CORPUS)
    assert result['extraction_quality']['measured'] is False
    assert result['extraction_quality']['model_f1'] is None
    assert result['human_correction']['measured'] is False
    assert result['human_correction']['correction_rate'] is None


def test_unpriced_model_is_unknown_not_zero():
    r=g.estimate(DEFAULTS,'fulltext')
    assert r['estimated_cost'] is None
    assert r['pricing_configured'] is False
    assert r['documents']==3
    assert 'PDF' in r['transmission_notice']


def test_configured_rates_report_bounded_dollar_range():
    cfg={**DEFAULTS,'llm_input_usd_per_million':1.5,'llm_output_usd_per_million':5.0}
    result=g.estimate(cfg,'summary',count=3)
    assert result['estimated_cost']['currency']=='USD'
    assert 0<result['estimated_cost']['min']<result['estimated_cost']['max']
    assert result['prompt_tokens_range'][1]>result['prompt_tokens_range'][0]


@pytest.mark.parametrize('action,count,chars', [('foo',1,400),('summary',0,400),('fulltext',40,900),('summary',1,20)])
def test_estimator_rejects_unbounded_input(action,count,chars):
    with pytest.raises(ValueError):g.estimate(DEFAULTS,action,count=count,chars_per_document=chars)


def test_nonfinite_prices_rejected_and_invalid_rates_unknown():
    assert g.estimate({**DEFAULTS,'llm_input_usd_per_million':'NaN'},'ranking')['estimated_cost'] is None
    with pytest.raises(ValueError):validate_config({**DEFAULTS,'llm_input_usd_per_million':float('nan')})


def test_offline_model_status_does_not_transmit_or_expose_secrets(monkeypatch):
    monkeypatch.setattr('paper_radar.cloud_api.chat_completion',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('not offline')))
    cfg={**DEFAULTS,'llm_data_consent':False,'api_key':'SECRET-DO-NOT-LEAK'}
    assert g.readiness(cfg,key_present=False)['state']=='key_missing'
    assert g.readiness(cfg,key_present=True)['state']=='awaiting_consent'
    assert 'SECRET' not in json.dumps(g.readiness(cfg,key_present=True))
    cfg['llm_data_consent']=True
    assert g.readiness(cfg,key_present=True)['state']=='configured_unverified'
    assert g.readiness(cfg,key_present=True)['connection_tested'] is False


@pytest.mark.parametrize('message,reason', [('HTTP 401 invalid API key','key'),('HTTP 402','balance'),('HTTP 429','rate_limit'),('request timeout','timeout'),('DNS lookup fail','network'),('model 404','model'),('super private prompt data','unknown')])
def test_error_classification_never_reflects_raw_provider_payload(message,reason):
    report=g.describe_failure(message)
    assert report['code']==reason
    assert 'super private' not in json.dumps(report)
    assert report['raw_redacted'] is True


def test_default_consent_is_false_even_when_ai_suggested():
    cfg=validate_config(DEFAULTS)
    assert cfg['ai_enabled'] and cfg['ai_rerank_enabled']
    assert cfg['llm_data_consent'] is False


def test_all_browser_jobs_have_cloud_permission_gate():
    web=(ROOT/'paper_radar/webapp.py').read_text(encoding='utf-8')
    portal=(ROOT/'paper_radar/public_portal.py').read_text(encoding='utf-8')
    pipeline=(ROOT/'paper_radar/agents/pipeline.py').read_text(encoding='utf-8')
    js=(ROOT/'paper_radar/static/portal.js').read_text(encoding='utf-8')
    assert "data.get('confirm_data_transfer') is not True" in web
    assert "payload.get('confirm_cloud_data_transfer') is not True" in portal
    assert 'llm_data_consent' in pipeline
    assert 'confirm_cloud_data_transfer:true' in js


def test_public_estimate_is_readonly_to_signed_in_users_only(tmp_path):
    from paper_radar.public_portal import Portal,make_server
    service=Portal(tmp_path/'portal',embedded_workers=False)
    server=make_server('127.0.0.1',0,service)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        port=server.server_port
        c=http.client.HTTPConnection('127.0.0.1',port,timeout=6)
        c.request('GET','/api/public/model/preflight',headers={'Host':f'127.0.0.1:{port}'})
        resp=c.getresponse();resp.read()
        assert resp.status==401
        c.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def _copy_and_fill(tmp_path):
    folder=tmp_path/'bench'
    shutil.copytree(CORPUS,folder)
    return folder


def test_completed_review_without_pdf_sha_is_rejected(tmp_path):
    folder=_copy_and_fill(tmp_path)
    for reviewer in ('A','B'):
        f=folder/f'annotations_{reviewer}.csv'
        with f.open(encoding='utf-8-sig',newline='') as h:r=list(csv.DictReader(h));fields=list(r[0])
        r[0]['independent_review_complete']='yes'
        with f.open('w',encoding='utf-8',newline='') as h:
            w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(r)
    with pytest.raises(ValueError,match='SHA-256'):
        review_coverage(folder)


def test_pdf_fingerprint_validated_before_formal_f1(tmp_path):
    folder=_copy_and_fill(tmp_path)
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    first=manifest['papers'][0]
    pid=first['paper_id']
    actual_pdf=b'%PDF-1.4\nplaceholder unparseable test fixture\n'
    (folder/'pdfs').mkdir()
    (folder/'pdfs'/(pid+first['version']+'.pdf')).write_bytes(actual_pdf)
    real_sha=hashlib.sha256(actual_pdf).hexdigest()
    for reviewer in ('A','B'):
        f=folder/f'annotations_{reviewer}.csv'
        with f.open(encoding='utf-8-sig',newline='') as h:r=list(csv.DictReader(h));fields=list(r[0])
        r[0].update({'pdf_sha256':real_sha,'page_index':'0','method':'ModelX','dataset':'Dset',
                     'metric':'Accuracy','value':'0.9','quote':'ModelX Dset Accuracy 0.9',
                     'independent_review_complete':'yes'})
        with f.open('w',encoding='utf-8',newline='') as h:
            w=csv.DictWriter(h,fieldnames=fields);w.writeheader();w.writerows(r)
    gold=folder/'adjudicated_gold.csv'
    with gold.open(encoding='utf-8-sig',newline='') as h:head=list(csv.DictReader(h).fieldnames)
    with gold.open('w',encoding='utf-8',newline='') as h:
        w=csv.DictWriter(h,fieldnames=head);w.writeheader();w.writerow({'paper_id':pid,'pdf_sha256':real_sha,'page_index':'0',
             'method':'ModelX','dataset':'Dset','metric':'Accuracy','value':'0.9','quote':'ModelX Dset Accuracy 0.9'})
    pred=tmp_path/'pred.json'
    pred.write_text(json.dumps({'findings':[{'paper_id':pid,'page_index':'0','method':'ModelX',
                             'dataset':'Dset','metric':'Accuracy','value':'0.9'}]}))
    result=adjudicated_quality(folder,pred)
    assert result['measured'] is False
    assert result['model_f1'] is None
    assert result['pdf_quote_errors']


def test_review_burden_from_explicit_auditable_actions(tmp_path):
    from paper_radar.benchmark_quality import review_burden
    folder=_copy_and_fill(tmp_path)
    path=folder/'review_activity.csv'
    with path.open(encoding='utf-8-sig',newline='') as h:fields=list(csv.DictReader(h).fieldnames)
    with path.open('w',encoding='utf-8',newline='') as h:
        w=csv.DictWriter(h,fieldnames=fields);w.writeheader()
        for action,edits,t in [('accept',0,20),('edit',2,50),('add',3,90),('delete',1,40)]:
            w.writerow({'paper_id':'2010.08895','reviewer':'A','action':action,'field_edits':edits,'seconds':t})
    stats=review_burden(folder)
    assert stats['measured'] is True
    assert stats['total_review_actions']==4
    assert stats['corrected_actions']==3
    assert stats['correction_rate']==0.75
    assert stats['fields_changed']==6
    assert stats['review_minutes']==3.33


def test_synthetic_real_pdf_f1_calculator_positive_path(tmp_path):
    """Unit-test math with generated synthetic PDF; never report as real accuracy."""
    import io
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    folder=_copy_and_fill(tmp_path)
    first=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))['papers'][0]
    pid=first['paper_id']
    writer=PdfWriter();page=writer.add_blank_page(width=595,height=842)
    font=writer._add_object(DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')}))
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    contents=DecodedStreamObject()
    quote='ModelX on Dset Accuracy 0.91 compared with BaselineX'
    contents.set_data(('BT /F1 11 Tf 30 730 Td ('+quote+') Tj ET').encode())
    page[NameObject('/Contents')]=writer._add_object(contents)
    raw=io.BytesIO();writer.write(raw)
    (folder/'pdfs').mkdir();(folder/'pdfs'/(pid+first['version']+'.pdf')).write_bytes(raw.getvalue())
    sha=hashlib.sha256(raw.getvalue()).hexdigest()
    template={'paper_id':pid,'pdf_sha256':sha,'page_index':'0','method':'ModelX',
              'dataset':'Dset','metric':'Accuracy','value':'0.91','quote':quote}
    for reviewer in ('A','B'):
        path=folder/f'annotations_{reviewer}.csv'
        with path.open(encoding='utf-8-sig',newline='') as h:r=list(csv.DictReader(h));cols=list(r[0])
        r[0].update({**template,'independent_review_complete':'yes'})
        with path.open('w',encoding='utf-8',newline='') as h:
            w=csv.DictWriter(h,fieldnames=cols);w.writeheader();w.writerows(r)
    adjud=folder/'adjudicated_gold.csv'
    with adjud.open(encoding='utf-8-sig',newline='') as h:cols=list(csv.DictReader(h).fieldnames)
    with adjud.open('w',encoding='utf-8',newline='') as h:
        w=csv.DictWriter(h,fieldnames=cols);w.writeheader();w.writerow(template)
    prediction=tmp_path/'predictions.json'
    prediction.write_text(json.dumps({'findings':[template]}))
    quality=adjudicated_quality(folder,prediction)
    assert quality['measured'] is True
    assert quality['scored_papers']==1 and quality['adjudicated_records']==1
    assert quality['model_precision']==1.0 and quality['model_recall']==1.0 and quality['model_f1']==1.0
    assert quality['pdf_hash_verified_papers']==1


def test_consent_rejects_truthy_strings_and_numeric_flags():
    for value in ('false','true',1,0,None):
        with pytest.raises(ValueError,match='布尔'):
            validate_config({**DEFAULTS,'llm_data_consent':value})


def test_model_eval_cli_is_dry_run_without_explicit_transfer(tmp_path):
    import subprocess
    result=subprocess.run([__import__('sys').executable,str(ROOT/'tools/predict_real_papers_v130.py'),
                           '--max-papers','2','--output',str(tmp_path/'should_not_exist.json')],
                          cwd=ROOT,capture_output=True,text=True,timeout=20)
    assert result.returncode==0
    payload=json.loads(result.stdout)
    assert payload['state']=='offline_preview'
    assert payload['preflight']['available_pdf_count']==0
    assert not (tmp_path/'should_not_exist.json').exists()
