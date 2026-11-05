# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.14 research agents: verified experiments, caching, stale PDF, API and safety."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json

import pytest

from paper_radar.agents.research.pipeline import research_registry, run_experiment_pipeline
from paper_radar.core import DEFAULTS, connect, save_papers
from paper_radar.experiments import (validate_experiments, read_findings,
                                     selected_experiment_chunks, safe_model_material)
from paper_radar.fulltext import index_paper
from paper_radar.models import Paper
from paper_radar import webapp
from test_v112_fulltext_evaluation import make_pdf


def build_study(root):
    db_path=Path(root)/'data'/'papers.sqlite3'
    db=connect(db_path)
    paper=Paper(paper_id='study-v114',title='Neural operator experiments',
        abstract='Neural operator benchmarking',authors='Test Author',
        published='2026-10-10',updated='2026-10-10',category='cs.LG',
        url='https://example.org/paper',pdf_url='',source='arxiv')
    save_papers(db,[paper],topic_id='default')
    (Path(root)/'downloads').mkdir(exist_ok=True)
    pdf=Path(root)/'downloads'/'study.pdf'
    make_pdf(pdf,['Introduction. The method learns geometry and mesh transfer.',
        'Experiments. Model X achieves 12% L2 error on Dataset A compared with baseline Y.',
        'Results. Ablation reduces the score to 9% on Dataset A compared with baseline Y.'])
    db.execute("UPDATE papers SET local_file='study.pdf', download_status='downloaded' WHERE paper_id='study-v114'")
    db.commit()
    db.close()
    return db_path,pdf


def evidence_chat(config,prompt,**kwargs):
    materials=json.loads(prompt[prompt.index('[{'):])
    chosen=next(item for item in materials if '12%' in item['text'])
    quote='Model X achieves 12% L2 error on Dataset A compared with baseline Y.'
    return json.dumps({'findings':[{'kind':'metric','dataset':'Dataset A',
        'metric':'L2 error','value':'12%','baseline':'baseline Y',
        'setting':'on Dataset A', 'chunk_id':chosen['chunk_id'],'quote':quote}]})


def test_registry_independent_research_pipeline_and_version():
    specs=research_registry().manifest()
    assert [x['name'] for x in specs]==['fulltext','experiment','evidence_verifier','research_storage']
    assert specs[1]['sends_to_cloud'] and specs[1]['capabilities']==['cloud_model','database_read']
    assert not specs[2]['sends_to_cloud']
    assert specs[3]['capabilities']==['database_write']
    assert all(x['max_attempts']==1 for x in specs)


def test_strict_field_quote_chunk_and_numeric_checks():
    by_id={15:{'page':3,'content':'Model X achieves 12% L2 error on Dataset A compared with baseline Y.'}}
    base={'kind':'metric','dataset':'Dataset A','metric':'L2 error','value':'12%',
          'baseline':'baseline Y','setting':'on Dataset A','chunk_id':15,
          'quote':by_id[15]['content']}
    valid=validate_experiments(json.dumps({'findings':[base]}),by_id)
    assert len(valid)==1 and valid[0]['page']==3
    mutations=[{'value':'88%'},{'dataset':'Dataset B'},{'baseline':'other baseline'},
               {'metric':'accuracy'},{'chunk_id':16},{'quote':'A forged sentence with 12% L2 error.'},
               {'setting':'a 99 percent improvement'},{'value':'Not reported'}]
    for patch_fields in mutations:
        candidate={**base,**patch_fields}
        assert not validate_experiments(json.dumps({'findings':[candidate]}),by_id),patch_fields
    assert validate_experiments('{bad json',by_id)==[]
    assert validate_experiments(json.dumps({'findings':{}}),by_id)==[]
    assert validate_experiments(json.dumps({'findings':[base,base]}),by_id)==valid


def test_pipeline_valid_source_cached_without_second_model_request_and_full_pdf_reindex():
    pytest.importorskip('pypdf')
    with TemporaryDirectory() as td:
        db_path,pdf=build_study(td)
        config={**DEFAULTS,'api_model':'test-large','api_provider':'OpenAI'}
        count=[]
        def counted(config,prompt,**kw):
            count.append(1)
            return evidence_chat(config,prompt,**kw)
        first=run_experiment_pipeline('study-v114',config,db_path=db_path,
                                      chat=counted,get_key=lambda provider:'mock-key')
        assert first['status']=='analyzed' and first['findings']==1
        assert [step['state'] for step in first['agent_run']['steps']]==['success']*4
        assert len(count)==1
        with connect(db_path) as db:
            data=read_findings(db,'study-v114',db_path=db_path)
            assert data['status']=='ready' and data['findings'][0]['value']=='12%'
            assert data['findings'][0]['page']==2
            assert len(selected_experiment_chunks(db,'study-v114'))>=2
        second=run_experiment_pipeline('study-v114',config,db_path=db_path,
                                       chat=lambda *_a,**_k: (_ for _ in ()).throw(AssertionError('repeated cloud call')),
                                       get_key=lambda _: 'key')
        assert second['status']=='cached' and second['findings']==1
        # Out-of-process PDF replacement makes all old evidence inaccessible.
        make_pdf(pdf,['Methods. A completely different evaluation without numeric results.'])
        with connect(db_path) as db:
            assert read_findings(db,'study-v114',db_path=db_path)['status']=='stale'
        # A subsequent analysis reindexes and rejects former evidence.
        with pytest.raises(AssertionError):
            # prove no old cached result, by deliberately aborting model call
            run_experiment_pipeline('study-v114',config,db_path=db_path,
                                    chat=lambda *_a,**_k: (_ for _ in ()).throw(AssertionError('new model call required')),
                                    get_key=lambda _: 'key')
        with connect(db_path) as db:
            assert read_findings(db,'study-v114',db_path=db_path)['findings']==[]


def test_invalid_model_answer_does_not_delete_previous_verified_data():
    pytest.importorskip('pypdf')
    with TemporaryDirectory() as td:
        db_path,_=build_study(td)
        config={**DEFAULTS,'api_model':'model-A'}
        first=run_experiment_pipeline('study-v114',config,db_path=db_path,
            chat=evidence_chat,get_key=lambda _: 'key')
        assert first['findings']==1
        invalid=run_experiment_pipeline('study-v114',{**config,'api_model':'model-B'},db_path=db_path,
            chat=lambda c,p,**kw:json.dumps({'findings':[{'kind':'metric','metric':'accuracy',
                'value':'100%','quote':'fabricated evidence','chunk_id':1}]}),
            get_key=lambda _: 'key')
        assert invalid['status']=='no_valid_evidence' and invalid['findings']==0
        with connect(db_path) as db:
            data=read_findings(db,'study-v114',db_path=db_path)
            assert data['findings'][0]['value']=='12%'


def test_model_request_has_bounded_context_and_does_not_access_arbitrary_paths():
    pytest.importorskip('pypdf')
    with TemporaryDirectory() as td:
        db_path,_=build_study(td)
        def model(config,prompt,**kwargs):
            assert len(prompt)<25000 and 'chunk_id' in prompt
            assert 'file://' not in prompt
            return json.dumps({'findings':[]})
        result=run_experiment_pipeline('study-v114',DEFAULTS,db_path=db_path,
                                       chat=model,get_key=lambda _: 'key')
        assert result['status']=='no_valid_evidence'
        with pytest.raises(ValueError, match='不存在'):
            run_experiment_pipeline('not-in-database',DEFAULTS,db_path=db_path,
                                    chat=model,get_key=lambda _: 'key')


def test_real_http_experiment_status_and_job_token():
    pytest.importorskip('pypdf')
    with TemporaryDirectory() as td:
        db_path,_=build_study(td)
        with connect(db_path) as db:index_paper(db,'study-v114',db_path=db_path)
        @contextmanager
        def local_db():
            with connect(db_path) as db:yield db
        server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
        server.token='v114-local-token'
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with patch.object(webapp,'DB_FILE',db_path),\
                 patch.object(webapp,'read_config',lambda:DEFAULTS),\
                 patch.object(webapp,'open_database',local_db):
                with urlopen(base+'/api/experiments/status?paper_id=study-v114') as req:
                    info=json.load(req)
                assert info['status']=='no_results' and info['findings']==[]
                with pytest.raises(HTTPError) as exc:
                    urlopen(base+'/api/experiments/status?paper_id=none')
                assert exc.value.code==404
                body=json.dumps({'mode':'analyze_experiments','ids':['study-v114']}).encode('utf-8')
                with pytest.raises(HTTPError) as exc:
                    urlopen(Request(base+'/api/job',data=body,headers={'Content-Type':'application/json'}))
                assert exc.value.code==403
                calls=[]
                with patch.object(webapp.runtime,'start',lambda mode,ids=None:calls.append((mode,ids))):
                    req=Request(base+'/api/job',data=body,headers={'Content-Type':'application/json',
                       'X-Local-Token':server.token,'Origin':base})
                    with urlopen(req) as response:assert response.status==202
                assert calls==[('analyze_experiments',['study-v114'])]
        finally:
            server.shutdown();server.server_close();thread.join(3)


def test_experiment_csv_is_topic_scoped_spreadsheet_safe_and_rejects_stale_pdf():
    import csv
    from paper_radar.experiments import export_experiment_index
    pytest.importorskip('pypdf')
    with TemporaryDirectory() as td:
        db_path,pdf=build_study(td)
        run_experiment_pipeline('study-v114',DEFAULTS,db_path=db_path,
                                chat=evidence_chat,get_key=lambda _: 'key')
        out=Path(td)/'reports'/'experimental.csv'
        with connect(db_path) as db:
            db.execute("UPDATE papers SET title=? WHERE paper_id='study-v114'",('=HYPERLINK("evil")',))
            db.commit()
            empty=export_experiment_index(db,topic_id='nonmatch',db_path=db_path,destination=out)
            assert empty['rows']==0
            ready=export_experiment_index(db,topic_id='default',db_path=db_path,destination=out)
        assert ready['rows']==1 and ready['skipped_stale']==0
        with out.open(encoding='utf-8-sig',newline='') as handle:
            row=list(csv.DictReader(handle))[0]
        assert row['论文标题'].startswith("'=")
        assert row['原文数值']=='12%' and row['PDF页码']=='2'
        assert row['原文引文'].startswith('Model X achieves 12%')
        make_pdf(pdf,['Results changed. No longer contains the original benchmark.'])
        with connect(db_path) as db:
            stale=export_experiment_index(db,topic_id='default',db_path=db_path,destination=out)
        assert stale['rows']==0 and stale['skipped_stale']==1
        with out.open(encoding='utf-8-sig',newline='') as handle:
            assert not list(csv.DictReader(handle))


def test_changing_pdf_during_model_call_aborts_transaction():
    pytest.importorskip('pypdf')
    with TemporaryDirectory() as td:
        db_path,pdf=build_study(td)
        def mutating_chat(config,prompt,**kw):
            raw=evidence_chat(config,prompt,**kw)
            make_pdf(pdf,['A newly replaced paper, unrelated to previous experiments.'])
            return raw
        with pytest.raises(ValueError,match='分析期间被修改'):
            run_experiment_pipeline('study-v114',DEFAULTS,db_path=db_path,
                                    chat=mutating_chat,get_key=lambda _: 'key')
        with connect(db_path) as db:
            assert not db.execute("SELECT 1 FROM experimental_findings WHERE paper_id='study-v114'").fetchone()
