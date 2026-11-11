# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.51: offline abstract insights, safe metadata completion and compact UI."""
from pathlib import Path
import json

from paper_radar.models import Paper
from paper_radar.core import connect,save_papers
from paper_radar.abstract_insights import extract_abstract_insights
from paper_radar.abstract_enrichment import (abstract_quality,quality_summary,refresh_one,run_batch)
from paper_radar.research_intelligence import run_local,read_local,_model_screen_metadata


def _paper(pid, abstract):
    return Paper(paper_id=pid,title='Geometry generalization of neural operators across meshes',
                 abstract=abstract,authors='Example Researcher',published='2026-11-10',
                 updated='2026-11-10',category='cs.LG',url='https://arxiv.org/abs/2611.12345',
                 pdf_url='',source='arxiv',doi='')


FULL=('We introduce a neural operator for unseen geometries under changing mesh resolution. '
      'We evaluate the method on two simulation datasets and compare with a finite element baseline. '
      'Results demonstrate improved cross-mesh transfer under the tested conditions. '
      'However, the study did not evaluate generalization to unseen material parameters.')


def test_abstract_first_inquiry_without_pdf_or_model(tmp_path):
    path=tmp_path/'papers.sqlite3'
    with connect(path) as db:
        save_papers(db,[_paper('2611.12345',FULL)])
    run=run_local(path,'default','神经算子在不同网格和几何条件下的泛化能力有哪些实验结果和局限？')
    brief=run['brief']
    assert brief['coverage']['papers_scanned']==1
    assert brief['coverage']['anchors_scanned']==0
    assert brief['findings']==[]
    assert brief['abstract_insights'], brief
    assert any(x['label']=='研究方法' and x['paper_id']=='2611.12345' for x in brief['abstract_insights'])
    assert all(x['verification']=='metadata_only_unverified' for x in brief['abstract_insights'])
    assert not brief['abstract_findings']
    md=read_local(path,'default',run['id'])['markdown']
    assert '摘要中可直接追溯的研究线索' in md
    assert 'Results demonstrate' in md or 'We introduce' in md


def test_model_interpretation_must_reference_a_supplied_abstract():
    papers=[{'paper_id':'arxiv:real','title':'Neural operator generalization',
             'abstract_excerpt':'We evaluate neural operators across unseen geometries with finite element baselines.'}]
    valid={'reading_questions':[], 'abstract_findings':[
        {'observation':'该研究进行了跨几何的神经算子评估。','span_ids':['S1']},
        {'observation':'该研究精度提升 99%。','span_ids':['S1']},
        {'observation':'没有来源的结果','span_ids':['S999']} ]}
    suggestions,status=_model_screen_metadata('神经算子跨几何泛化？',papers,config={},
                                               model_callable=lambda _:valid)
    assert suggestions==[]
    assert len(status['abstract_findings'])==1
    note=status['abstract_findings'][0]
    assert note['sources'][0]['paper_id']=='arxiv:real'
    assert note['sources'][0]['quote']==papers[0]['abstract_excerpt']
    assert note['status']=='abstract_interpretation_unverified'


def test_background_abstract_completion_preserves_longer_record(tmp_path):
    path=tmp_path/'store.sqlite3'
    with connect(path) as db:
        save_papers(db,[_paper('2611.12345','We propose a model.'),_paper('2611.12346',FULL)])
        before=quality_summary(db,'default')
        assert before['total']==2 and before['needs_refresh']==1
        assert abstract_quality(FULL)=='adequate'
        assert abstract_quality('Some text...')=='possibly_truncated'
        unchanged=refresh_one(db,'2611.12346',fetcher=lambda row:('', ''))
        assert unchanged['outcome']=='unchanged'
    fetched=[]
    def fetch(row):
        fetched.append(row['paper_id'])
        return FULL,'arXiv'
    result=run_batch(path,'default',limit=10,fetcher=fetch)
    assert result['selected']==1 and result['improved']==1
    assert fetched==['2611.12345']
    with connect(path) as db:
        summary=quality_summary(db,'default')
        assert summary['needs_refresh']==0
        assert db.execute('SELECT abstract FROM papers WHERE paper_id=?',('2611.12345',)).fetchone()[0]==FULL
        assert db.execute('SELECT COUNT(*) FROM abstract_enrichment_attempts').fetchone()[0]==1
    again=run_batch(path,'default',limit=10,fetcher=fetch)
    assert again['attempted']==0 and again['improved']==0
    assert len(fetched)==1


def test_no_unfounded_abstract_substitution(tmp_path):
    path=tmp_path/'papers.sqlite3'
    original='This short abstract describes geometry and mesh methods.'
    with connect(path) as db:
        save_papers(db,[_paper('2611.12345',original)])
        r=refresh_one(db,'2611.12345',fetcher=lambda _:('A one-line note.', 'Crossref'))
        assert r['outcome']=='unchanged'
        assert db.execute('SELECT abstract FROM papers WHERE paper_id=?',('2611.12345',)).fetchone()[0]==original


def test_no_raw_research_question_or_api_key_in_abstract_audit(tmp_path):
    path=tmp_path/'papers.sqlite3'
    with connect(path) as db:
        save_papers(db,[_paper('2611.12345','')])
    run_batch(path,'default',fetcher=lambda _:('', ''))
    with connect(path) as db:
        text=json.dumps([dict(x) for x in db.execute('SELECT * FROM abstract_enrichment_attempts')])
        assert 'raw_abstract' not in text and 'api_key' not in text and 'research_goal' not in text


def test_analysis_ui_default_single_action_and_advanced_tools_hidden():
    path=Path(__file__).resolve().parents[1]/'paper_radar'/'static'/'inquiry_workbench.js'
    js=path.read_text('utf-8')
    assert "'开始分析'" in js
    assert 'inquiry-options-wrap' in js and 'inquiry-expert-tools' in js
    assert 'abstract_insights' in js and 'inquiry-abstract-insights' in js
    assert 'cloudAuthorized=!!model?.checked&&!!readiness.ready&&!!confirm?.checked' in js
