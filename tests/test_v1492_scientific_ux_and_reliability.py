"""v1.49.2: real failure modes, public abstract provenance and safe inquiry discovery."""
from __future__ import annotations
import json
import sqlite3
from unittest.mock import patch
from pathlib import Path

import pytest

from paper_radar.ai_planner import plan_research
from paper_radar.abstract_recovery import refresh_missing_abstract,refresh_portal_abstract
from paper_radar.core import connect,save_papers
from paper_radar.models import Paper
from paper_radar.research_intelligence import run_local,read_local
from paper_radar.runtime_diagnostics import RuntimeDiagnostics


def _paper(pid,title,abstract='',doi=''):
    return Paper(paper_id=pid,title=title,abstract=abstract,authors='Scientist',
      published='2026-11-01',updated='2026-11-01',category='cs.LG',
      url='https://example.org/study',pdf_url='',doi=doi)


def test_ai_parser_recovers_freeform_model_terms():
    with patch('paper_radar.ai_planner.chat_completion',return_value=(
       'Suggested keywords:\n1. Fourier neural operator\n2. unstructured mesh generalization\n'
       '3. complex geometry benchmark\n')):
        result=plan_research('研究神经算子在非结构化网格和复杂几何中的泛化表现',{})
    assert result['source']=='model_text_recovered'
    assert 'Fourier neural operator' in result['keywords']


def test_ai_parser_known_chinese_domain_fallback_without_fake_model_claims():
    with patch('paper_radar.ai_planner.chat_completion',return_value='抱歉，无法按要求生成结构化方案。'):
        result=plan_research('研究神经算子在非结构化网格和复杂几何中的泛化表现',{})
    assert result['source']=='local_terms_after_invalid_model'
    assert 'neural operator' in result['keywords']
    assert result['notes'] and '人工' in result['notes'][0]


def test_ai_provider_failure_remains_actionable():
    from paper_radar.cloud_api import ModelServiceError
    with patch('paper_radar.ai_planner.chat_completion',side_effect=ModelServiceError('MODEL_AUTH','密钥无效')):
        with pytest.raises(ModelServiceError,match='密钥无效'):
            plan_research('研究神经算子在非结构化网格和复杂几何中的泛化表现',{})


def test_bilingual_chinese_question_recalls_english_papers_without_faking_claims(tmp_path):
    path=tmp_path/'papers.sqlite3'
    with connect(path) as db:
        save_papers(db,[_paper('local:1','Generalization of neural operators across unstructured meshes and irregular geometries',
                      'We examine neural operators across variable meshes and geometries.')])
    r=run_local(path,'default','神经算子在不同网格和复杂几何条件下的泛化能力有哪些局限？')
    brief=r['brief']
    assert brief['findings']==[]
    assert len(brief['metadata_candidates'])>=1
    assert brief['coverage']['candidate_matches']>=1
    assert 'not_verified' in brief['metadata_candidates'][0]['status']
    assert 'unstructured meshes' in read_local(path,'default',r['id'])['markdown']


def test_missing_abstract_refresh_updates_only_blank_and_preserves_notes(tmp_path):
    path=tmp_path/'papers.sqlite3'
    with connect(path) as db:
        save_papers(db,[_paper('crossref:10.2222/x','A PDE study',doi='10.2222/x')])
        with patch('paper_radar.abstract_recovery._candidate',return_value=('A reproducible abstract with $E=mc^2$.','Crossref')):
            result=refresh_missing_abstract(db,'crossref:10.2222/x')
        assert result['status']=='updated' and result['source']=='Crossref'
        with patch('paper_radar.abstract_recovery._candidate',side_effect=AssertionError('should not fetch twice')):
            assert refresh_missing_abstract(db,'crossref:10.2222/x')['status']=='already_present'
        assert 'reproducible' in db.execute('SELECT abstract FROM papers WHERE paper_id=?',('crossref:10.2222/x',)).fetchone()[0]


def test_missing_abstract_refresh_never_invents_content(tmp_path):
    path=tmp_path/'papers.sqlite3'
    with connect(path) as db:
        save_papers(db,[_paper('crossref:10.2222/y','Unknown paper',doi='10.2222/y')])
        with patch('paper_radar.abstract_recovery._candidate',return_value=('','')):
            result=refresh_missing_abstract(db,'crossref:10.2222/y')
        assert result['status']=='unavailable'
        assert db.execute('SELECT abstract FROM papers WHERE paper_id=?',('crossref:10.2222/y',)).fetchone()[0]==''


def test_no_api_to_arbitrary_url_from_paper_metadata():
    from paper_radar.abstract_recovery import _candidate
    with patch('paper_radar.abstract_recovery._json_from',side_effect=AssertionError('must not request URL')):
        assert _candidate({'paper_id':'local:evil','doi':'https://evil.example/abc','url':'http://127.0.0.1/private'})==('','')


def test_portal_abstract_refresh_stays_in_user_workspace(tmp_path):
    from paper_radar.public_portal import Portal
    portal=Portal(tmp_path/'portal',job_workers=0,embedded_workers=False)
    uid='f'*32
    with portal.workspace(uid) as db:
        db.execute('INSERT INTO papers(id,title,abstract,published,url,doi,imported_at) VALUES (?,?,?,?,?,?,?)',
                  ('doi:test','Title','', '2026-11-01','https://example.org','10.2222/z','2026-11-01'))
        with patch('paper_radar.abstract_recovery._candidate',return_value=('Abstract from metadata.','OpenAlex')):
            assert refresh_portal_abstract(db,'doi:test')['source']=='OpenAlex'
        assert db.execute('SELECT abstract FROM papers WHERE id=?',('doi:test',)).fetchone()[0]=='Abstract from metadata.'


def test_runtime_diagnostic_tag_client_disconnect(tmp_path):
    log=RuntimeDiagnostics(tmp_path)
    log.record('client_disconnected',method='GET',route='/api/papers?q=private',
               code='CLIENT_DISCONNECTED',error=BrokenPipeError('a secret message'))
    content=(tmp_path/'logs'/'runtime.jsonl').read_text()
    assert 'CLIENT_DISCONNECTED' in content and 'BrokenPipeError' in content
    assert 'DATABASE_UNAVAILABLE' not in content and 'secret message' not in content
    assert 'private' not in content


def test_diagnostics_summary_is_bounded_and_does_not_leak_user_text(tmp_path):
    from io import BytesIO
    from zipfile import ZipFile
    d=RuntimeDiagnostics(tmp_path)
    d.record('model_error',method='POST',route='/api/ai/plan?prompt=RESEARCH_SECRET',
             code='MODEL_FORMAT',severity='error',error=ValueError('PRIVATE_EXPERIMENT_VALUE'))
    d.record('client_disconnected',method='GET',route='/app/library?token=PRIVATE',
             code='CLIENT_DISCONNECTED',error=BrokenPipeError('PRIVATE_INFO'))
    d.record('http_request',method='POST',route='/api/ai/plan?user=PRIVATE',
             status=400,duration_ms=5100)
    result=d.summary()
    assert result['sampled_events']==3
    assert result['model_calls_failed']==1 and result['client_disconnections']==1
    assert result['slow_requests_over_5s']==1 and result['requests_http_4xx_5xx']==1
    assert result['errors_by_code'][0]['code']=='MODEL_FORMAT'
    z=ZipFile(BytesIO(d.export()))
    assert 'summary.json' in z.namelist()
    manifest=json.loads(z.read('manifest.json'))
    assert 'summary.json' in manifest['included']
    assert not any(text in z.read('summary.json').decode() for text in
                   ('RESEARCH_SECRET','PRIVATE_EXPERIMENT_VALUE','PRIVATE_INFO','token=PRIVATE'))


def test_model_metadata_screening_rejects_unprovided_papers_and_numeric_claims():
    from paper_radar.research_intelligence import _model_screen_metadata
    papers=[{'paper_id':'p:one','title':'Neural operators on mesh','abstract_excerpt':'PDE on unstructured mesh'}]
    answer={'reading_questions':[{'question':'该实验是否改变了网格尺寸？','paper_ids':['p:one']},
             {'question':'精度提升 99%','paper_ids':['p:one']} ]}
    suggestions,state=_model_screen_metadata('网格泛化？',papers,config={},model_callable=lambda _:answer)
    assert len(suggestions)==1 and suggestions[0]['paper_ids']==['p:one']
    assert state['sent_to_cloud'] is True and state['new_factual_claims_allowed'] is False
    with pytest.raises(ValueError,match='输入之外'):
        _model_screen_metadata('网格泛化？',papers,config={},model_callable=lambda _:{
            'reading_questions':[{'question':'是否可比？','paper_ids':['fictional:paper']}]})


def test_runtime_diagnostics_http_summary_endpoint(tmp_path):
    from tests.test_v1491_runtime_diagnostics import running_server
    with running_server(tmp_path) as (api,folder):
        status,body=api('/api/diagnostics/summary')
        assert status==200
        response=json.loads(body)
        assert isinstance(response['summary']['recent_issues'],list)
        assert 'sampled_events' in response['summary']
        assert 'api_key' not in str(response).lower()
