"""Scientific reproducibility guards for v1.45.3.

Real-PDF pilot is optional at runtime and never counts as human gold.
"""
import pytest
from paper_radar.research_synthesis_agent import (
    numeric_literals, mentioned_methods, _call_model, relevance, build_report)


def _findings():
    return [{'claim_id':'cl-01','body':'FNO Navier Stokes Table 1 error 0.0086; FNO-2D 0.0128',
             'sources':[{'anchor_id':'a-01','paper_id':'p1','title':'FNO paper',
                         'quote':'FNO-3D 0.0086; FNO-2D 0.0128','page':8,'pdf_sha256':'f'*64}]},
            {'claim_id':'cl-02','body':'U-FNO and CNN Table 2 gas saturation front 3.4% 9.2%',
             'sources':[{'anchor_id':'a-02','paper_id':'p2','title':'U-FNO paper',
                         'quote':'CNN 9.2 U-FNO 3.4','page':19,'pdf_sha256':'a'*64}]}]


def test_numeric_rank_includes_experiment_values_without_table_number_noise():
    assert numeric_literals('Table 2 and Figure 3') == set()
    assert numeric_literals('FNO error 0.0086, U-FNO error 3.4% and N=1000') == {'0.0086','3.4%','1000'}
    f=_findings()
    assert relevance('Navier Stokes FNO error 0.0086', {'body':f[0]['body'],'sources':f[0]['sources']}) > relevance('Navier Stokes FNO error 0.0086', {'body':f[1]['body'],'sources':f[1]['sources']})


def test_method_family_distinguishes_u_fno_and_pino():
    assert mentioned_methods('U-FNO gas saturation model')=={'U-FNO'}
    assert 'PINO' in mentioned_methods('Physics-informed neural operator PINO')
    assert 'FNO' not in mentioned_methods('U-FNO only')


def test_model_cannot_introduce_new_numeric_fact_with_valid_claim_id():
    fake=lambda _: {'sections':[{'title':'supported', 'interpretation':'FNO-3D error 0.0086','claim_ids':['cl-01']},
                                {'title':'fabricated','interpretation':'FNO error improved by 99.9%','claim_ids':['cl-01']}],
                    'questions':[]}
    result=_call_model('FNO benchmark',_findings(),{},fake)
    assert [s['title'] for s in result['sections']]==['supported']
    assert result['validation']['rejected_unsupported_numeric_interpretations']==1


def test_no_false_support_label_for_model_generated_summary():
    claims=[{'id':'cl-01','revision':1,'kind':'finding',
             'body':'FNO-3D error 0.0086','effective_status':'confirmed',
             'anchors':[{'id':'a-01','status':'ready','paper_id':'p1','page':8,'title':'source',
                         'quote':'FNO-3D error 0.0086','pdf_sha256':'f'*64}]}]
    model=lambda _: {'sections':[{'title':'unsupported','interpretation':'The error is 100.0%','claim_ids':['cl-01']}], 'questions':[]}
    r=build_report({'claims':claims},'What is the FNO-3D error value?',use_model=True,confirm_transfer=True,model_callable=model)
    assert not r['model']['sections']
    assert r['model']['validation']['rejected_unsupported_numeric_interpretations']==1
    assert all(src['pdf_sha256']=='f'*64 for c in r['findings'] for src in c['evidence'])


def test_frozen_real_corpus_is_not_pretended_human_gold():
    from pathlib import Path
    import csv
    base=Path(__file__).resolve().parents[1]/'benchmarks/real_pdf30'
    with (base/'annotations/review_A.csv').open(encoding='utf-8-sig') as f:
        rows=list(csv.DictReader(f))
    assert len(rows)==117
    assert sum(r.get('decision') in ('accepted','rejected') for r in rows)==0
