"""v1.49.3: useful literature triage with evidence and trace integrity."""
from __future__ import annotations
import io
import json
import zipfile
from pathlib import Path
from paper_radar.core import connect, save_papers
from paper_radar.models import Paper
from paper_radar.research_intelligence import run_local, read_local
from paper_radar.runtime_diagnostics import RuntimeDiagnostics


def paper(pid, title, abstract):
    return Paper(paper_id=pid,title=title,abstract=abstract,authors='Author',
        published='2026-11-10',updated='2026-11-10',category='cs.LG',
        url='https://example.org/study',pdf_url='',doi='')


def sample_db(path):
    with connect(path) as db:
        save_papers(db,[
            paper('arxiv:1','Geometry generalization of neural operators across meshes',
                  'We evaluate neural operators on unseen geometries and unstructured meshes with out-of-distribution tests.'),
            paper('arxiv:2','Fourier neural operator for varying mesh resolution',
                  'A neural operator is evaluated on multiple mesh resolutions; the train-test protocol requires reading.'),
            paper('arxiv:3','LocateAnyMesh: Grounding 3D mesh queries',
                  'We propose a 3D mesh vision-language retrieval method using images.'),
            paper('arxiv:4','Anisotropic mesh adaptation for finite elements',
                  'We analyze the stability of a finite element method on nonuniform meshes.'),
            paper('arxiv:5','Neural operator for magnetostatic equations',
                  'A surrogate neural operator for electromagnetic fields. No geometry generalization was studied.'),
        ])


def test_screening_prioritizes_cross_geometry_mesh_and_rejects_unrelated(tmp_path):
    path=tmp_path/'papers.sqlite3';sample_db(path)
    result=run_local(path,'default','神经算子在不同网格和几何条件下的泛化能力有哪些实验限制？')
    brief=result['brief']
    ids=[item['paper_id'] for item in brief['metadata_candidates']]
    assert ids[:2]==['arxiv:1','arxiv:2'],ids
    assert 'arxiv:3' not in ids and 'arxiv:4' not in ids
    assert brief['coverage']['candidate_matches']==3
    assert brief['metadata_candidates'][0]['relevance_level']=='high'
    assert {'mesh','geometry','generalization'}.issubset(
        brief['metadata_candidates'][0]['matched_facets'])
    assert brief['findings']==[] and brief['coverage']['question_matched_claims']==0
    assert brief['topic_map']
    md=read_local(path,'default',result['id'])['markdown']
    assert '研究主题线索' in md and '原文锚点 0' in md


def test_inquiry_diagnostics_status_counts_and_safe_bundle(tmp_path):
    path=tmp_path/'papers.sqlite3';sample_db(path)
    logs=RuntimeDiagnostics(tmp_path)
    def add(event,**kwargs):
        return logs.record(event,method='WORKER',task='research_inquiry',**kwargs)
    question='神经算子在不同网格和几何条件下的泛化能力有哪些实验限制？'
    result=run_local(path,'default',question,diagnostic=add,use_model=True,confirm_transfer=True,
                     model_callable=lambda _: {'reading_questions':[{'question':'Invented?',
                         'paper_ids':['fake-paper']}]})
    brief=result['brief']
    assert brief['model']['status']=='model_failed_local_screening_used'
    assert brief['model']['failure_code']=='MODEL_RESPONSE_INVALID'
    overview=logs.summary()
    assert len(overview['research_runs'])==1
    item=overview['research_runs'][0]
    assert item['run_id']==brief['diagnostic_run_id']
    assert item['outcome_code']=='CANDIDATE_ONLY'
    assert item['model_code']=='MODEL_RESPONSE_INVALID'
    assert len(item['stages'])==5
    assert item['counts']['papers']==5
    assert item['counts']['confirmed_matches']==0
    bundle=logs.export()
    with zipfile.ZipFile(io.BytesIO(bundle)) as z:
        assert {'README_FIRST.md','research_runs.json','summary.json','runtime.jsonl'}.issubset(z.namelist())
        text=''.join(z.read(f).decode('utf-8') for f in z.namelist())
        assert question not in text
        assert 'fake-paper' not in text
        assert 'MODEL_RESPONSE_INVALID' in text


def test_log_scrubs_untrusted_counters_and_urls(tmp_path):
    log=RuntimeDiagnostics(tmp_path)
    log.record('inquiry_stage',task='research_inquiry',run_id='c'*16,stage='inquiry_match',
               route='/api/inquiry/run?secret=123',counts={'papers':7,'research_question':'secret','bad':10},
               error=ValueError('My private research question'))
    text=(tmp_path/'logs'/'runtime.jsonl').read_text()
    assert 'private research question' not in text and 'secret=123' not in text
    assert '"papers":7' in text and 'research_question' not in text


def test_browser_sources_have_compact_cards_and_history():
    static=Path(__file__).resolve().parents[1]/'paper_radar'/'static'
    inquiry=(static/'inquiry_workbench.js').read_text('utf-8')
    local=(static/'app.js').read_text('utf-8')
    public=(static/'portal.js').read_text('utf-8')
    assert 'inquiry-history-disclosure' in inquiry
    assert 'inquiry-topic-grid' in inquiry and 'inquiry-paper-excerpt-more' in inquiry
    assert 'paper-card-v1493' in local and 'paper-card-v1493' in public
    assert 'paper-abstract-more' in public


def test_no_unrelated_fallback_candidates(tmp_path):
    path=tmp_path/'papers.sqlite3'
    with connect(path) as db:
        save_papers(db,[paper('arxiv:mesh','Adaptive finite element meshing',
            'We study domain partitioning and multigrid solvers.')])
    result=run_local(path,'default',
        '神经算子在不同网格和几何条件下的泛化能力有哪些实验限制？')
    assert result['brief']['metadata_candidates']==[]
    assert result['brief']['coverage']['candidate_matches']==0
    assert result['brief']['topic_map']==[]
