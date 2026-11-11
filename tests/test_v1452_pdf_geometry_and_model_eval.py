"""v1.45.2 layout alignment, bounded tables, frozen-model protocol safeguards."""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
DATASET=ROOT/'benchmarks'/'real_pdf30'


def _pdf(tmp_path):
    fitz=pytest.importorskip('fitz')
    filename=tmp_path/'test.pdf'
    pdf=fitz.open()
    pg=pdf.new_page()
    pg.insert_text((70,90),'Table 1: operator results, evaluation dataset.')
    xs=[70,230,390];ys=[120,160,200]
    for y in ys:pg.draw_line((xs[0],y),(xs[-1],y),width=.8)
    for x in xs:pg.draw_line((x,ys[0]),(x,ys[-1]),width=.8)
    pg.insert_text((90,145),'Method');pg.insert_text((260,145),'RMSE')
    pg.insert_text((90,185),'FNO-2D');pg.insert_text((260,185),'0.0128')
    pg.insert_text((70,248),'Equation (1): loss = RMSE of model output.')
    for _ in range(166):pdf.new_page()
    pdf[166].insert_text((70,100),'Late-page operator evidence 0.123 reported on page 167.')
    pdf.save(filename);pdf.close()
    return filename,hashlib.sha256(filename.read_bytes()).hexdigest()


def test_verified_cross_parser_locator_reports_separate_scientific_status(tmp_path):
    from paper_radar.pdf_geometry import locate,preview_png
    path,sha=_pdf(tmp_path)
    result=locate(path,sha,1,'operator results, evaluation dataset.')
    assert result['text_matched'] and result['matched']
    assert result['boxes'] and result['scientific_relation_verified'] is False
    assert preview_png(path,sha,1,'operator results, evaluation dataset.').startswith(b'\x89PNG')
    lost=locate(path,sha,1,'Not a real result.')
    assert not lost['text_matched'] and not lost['matched']
    with pytest.raises(ValueError,match='已更新'):locate(path,'0'*64,1,'operator results')


def test_table_row_and_column_coordinates_are_unverified_candidates(tmp_path):
    from paper_radar.pdf_geometry import page_tables
    path,sha=_pdf(tmp_path)
    result=page_tables(path,sha,1)
    assert len(result['tables'])>=1
    grid=result['tables'][0]
    assert grid['column_count']>=2 and grid['row_count']>=2
    assert grid['cells'] and all('row' in c and 'column' in c for c in grid['cells'])
    assert grid['review']=='unverified' and grid['confidence']=='ruled_grid_candidate'


def test_long_pdf_page_167_is_addressable_without_fabricated_science(tmp_path):
    from paper_radar.pdf_geometry import locate
    path,sha=_pdf(tmp_path)
    result=locate(path,sha,167,'Late-page operator evidence 0.123 reported on page 167.')
    assert result['page']==167 and result['matched']
    assert result['scientific_relation_verified'] is False


def test_formula_context_is_only_a_candidate(tmp_path):
    from paper_radar.pdf_geometry import locate
    path,sha=_pdf(tmp_path)
    result=locate(path,sha,1,'Equation (1): loss = RMSE of model output.')
    assert result['matched']
    assert result['formula_context']['equation_verified'] is False


def test_model_protocol_fixes_one_prompt_and_four_tasks():
    from tools.eval_realpdf30_models import TASK_INSTRUCTIONS,PROMPT_VERSION,protocol_sha
    assert set(TASK_INSTRUCTIONS)=={'evidence_extraction','method_comparison',
                                    'conflict_detection','research_qa'}
    assert 'v1452' in PROMPT_VERSION and len(protocol_sha())==64


def test_model_plan_builds_256_tasks_without_network(monkeypatch,tmp_path):
    from tools import eval_realpdf30_models as mod
    raw=mod.csvrows(DATASET/'annotations/candidate_evidence.csv')
    geo=tmp_path/'geometry.jsonl'
    with geo.open('w',encoding='utf-8') as f:
        for r in raw:
            f.write(json.dumps({'candidate_id':r['candidate_id'],'pdf_sha256':r['pdf_sha256'],
                'page':int(r['page']),'text_matched':True,
                'context':r['source_quote'],'formula_context':None,'table_context':None})+'\n')
    tasks=mod._task_inputs(DATASET,geo)
    assert len(tasks)==256
    from collections import Counter
    assert Counter(t['task_type'] for t in tasks)=={
        'evidence_extraction':117,'method_comparison':34,'conflict_detection':17,'research_qa':88}
    assert all(t['split'] in ('train','validation','test') for t in tasks)
    assert not any('answer_gold' in mod.build_prompt(t) for t in tasks)


def test_wrong_scientific_citation_is_detected():
    from tools.eval_realpdf30_models import validate_response
    task={'task_type':'research_qa','source':[{'paper_id':'p','page':8,
                  'layout_context':'The operator RMSE is 0.11 in the original table.',
                  'source_quote':'operator RMSE is 0.11'}]}
    obj={'answer':'RMSE is 0.11','answerable':True,
         'citations':[{'paper_id':'p','page':9,'quote':'operator RMSE is 0.11'}]}
    assert 'Unverified citation page/quote' in validate_response(task,obj)
    assert not validate_response(task,{**obj,'citations':[{'paper_id':'p','page':8,
                   'quote':'operator RMSE is 0.11'}]})
    assert 'Asserted scientific claim must cite source evidence' in validate_response(task,{**obj,'citations':[]})


def test_human_gate_does_not_autolabel_real_benchmark(monkeypatch):
    from tools import eval_realpdf30_models as mod
    monkeypatch.setattr(mod,'check_frozen_pdfs',lambda *_:{x['paper_id']:x for x in
         json.loads((DATASET/'manifest.json').read_text())['papers']})
    result=mod.review_gate(DATASET,Path('/no/files'))
    assert result['ready'] is False and result['scientific_accuracy_reportable'] is False
    assert result['evidence']['independently_double_reviewed']==0
    assert result['questions']['double_reviewed']==0 and result['pairs']['double_reviewed']==0


def test_cost_budget_config_requires_confirmed_provider(tmp_path):
    from tools.eval_realpdf30_models import FrozenConfig
    config=FrozenConfig.load(DATASET/'model_protocol_v1452.json')
    assert config.max_output_tokens==900 and config.max_cost_usd==0
    with pytest.raises(ValueError,match='Select a single provider'):
        FrozenConfig.load(DATASET/'model_protocol_v1452.json',require_live=True)


def test_do_not_treat_partial_model_answer_review_as_gold(tmp_path):
    from tools.review_realpdf30_model_answers import make_review,grade
    data=tmp_path/'model_predictions.jsonl'
    with data.open('w',encoding='utf-8') as f:
        for i in range(88):
            f.write(json.dumps({'id':f'Q{i:03d}','task_type':'research_qa',
                 'prediction':{'answer':'insufficient evidence','answerable':False,'citations':[]}})+'\n')
    make_review(tmp_path)
    with pytest.raises(FileExistsError):make_review(tmp_path)
    result=grade(tmp_path,{'A':{'reviewer_id':'human_a'},
                               'B':{'reviewer_id':'human_b'}})
    assert result['state']=='pending_human_model_answer_review'
    assert result['semantic_qa_quality'] is None and result['resolved']==0


def test_oversized_pdf_graphic_content_does_not_trigger_unbounded_grid_scan(monkeypatch,tmp_path):
    from paper_radar.pdf_geometry import _table_candidates
    class FakePage:
        def get_drawings(self):return [None]*501
        def find_tables(self,*args,**kwargs):raise AssertionError('should not scan dense drawing page')
    assert _table_candidates(FakePage())==[]


def test_conflict_gold_schema_matches_model_schema():
    from tools.eval_realpdf30_models import validate_response,TASK_INSTRUCTIONS
    from runpy import run_path
    categories=run_path(str(DATASET/'scripts'/'score_four_tasks.py'))['CONFLICT']
    task={'task_type':'conflict_detection','source':[]}
    for value in categories:
        answer={'label':value,'reason':'Distinct settings; compare with caution.', 'citations':[]}
        if value=='confirmed_conflict':
            assert 'Asserted scientific claim must cite source evidence' in validate_response(task,answer)
        else:
            assert validate_response(task,answer)==[]
    assert 'apparent_conflict_due_to_settings' in TASK_INSTRUCTIONS['conflict_detection']


def test_reviewed_qa_and_pair_fields_require_real_enum_and_condition_labels():
    from runpy import run_path
    scope=run_path(str(DATASET/'scripts'/'score_four_tasks.py'))
    assert not scope['_valid_review']({'answerable':'maybe','rubric_score':'3'},'qa')
    assert scope['_valid_review']({'answerable':'no','rubric_score':'3'},'qa')
    assert not scope['_valid_review']({'quantitatively_comparable':'yes',
         'contradiction_label':'contradiction','same_dataset':'yes','same_metric':'yes',
         'same_setting':'yes'},'pair')
    assert scope['_valid_review']({'quantitatively_comparable':'unverified',
         'contradiction_label':'apparent_conflict_due_to_settings',
         'same_dataset':'yes','same_metric':'unverified','same_setting':'no'},'pair')
