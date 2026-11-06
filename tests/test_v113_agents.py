# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.13: DAG contracts, private traces, faults and original API compatibility."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from urllib.request import urlopen
import json

import pytest

from paper_radar.agents import Agent, AgentSpec, AgentContext, AgentResult, AgentRegistry, AgentRunner
from paper_radar.agents.catalog import build_registry, ON_DEMAND
from paper_radar.agents.trace import read_traces, store_trace
from paper_radar.core import DEFAULTS, run_cycle
from paper_radar.models import Paper
from paper_radar.progress import TaskProgress
from paper_radar import webapp

NOW=datetime(2026,11,4,12,tzinfo=timezone.utc)


def paper():
    return Paper('fake-1','A neural operator with geometry generalization',
                 'A new neural operator improves geometry adaptation.',
                 'Test Author','2026-11-03T10:00:00Z','2026-11-03T10:00:00Z',
                 'cs.LG','https://arxiv.org/abs/2611.00001','')


def ctx(tmp, **kwargs):
    root=Path(tmp)
    cfg={**DEFAULTS,'output_dir':str(root/'reports'),'ai_enabled':False,'auto_download':False,
         **kwargs}
    return AgentContext(cfg,root/'papers.db',root/'reports',lambda *a,**kw:[paper()],
                        NOW,lambda *a:None,TaskProgress())


def specimen(name, deps=(), requires=(), produces=(), policy='stop', execute=None):
    spec=AgentSpec(name,name,'0.1','fixture',depends_on=deps,
                   requires=requires,produces=produces,failure_policy=policy)
    return Agent(spec,execute or (lambda _:AgentResult({key:name for key in produces})))


def test_builtin_registry_exports_separate_scheduled_and_manual_agents():
    registry=build_registry()
    names=[a.spec.name for a in registry.plan()]
    assert names==['profile','discovery','quality','ranking','storage','summary','download','selection','report']
    assert len({a.spec.owner_module for a in registry.plan()})==9
    assert registry.get('ranking').spec.sends_to_cloud
    assert not registry.get('storage').spec.sends_to_cloud
    assert any(x['name']=='fulltext_reader' for x in ON_DEMAND)
    assert all('requires' in item and 'produces' in item for item in registry.manifest())


def test_registry_rejects_duplicate_and_invalid_name():
    registry=AgentRegistry([specimen('first')])
    with pytest.raises(ValueError,match='重复'):
        registry.register(specimen('first'))
    with pytest.raises(ValueError,match='无效'):
        registry.register(specimen('../bad'))


def test_registry_rejects_cycle_and_missing_dependency():
    registry=AgentRegistry([specimen('alpha',deps=('beta',)),specimen('beta',deps=('alpha',))])
    with pytest.raises(ValueError,match='循环'):
        registry.plan()
    with pytest.raises(ValueError,match='缺少依赖'):
        AgentRegistry([specimen('alpha',deps=('other',))]).plan()


def test_registry_prevents_undeclared_output_owner_and_collisions():
    with pytest.raises(ValueError,match='生产者'):
        AgentRegistry([specimen('alpha',requires=('missing',))]).plan()
    with pytest.raises(ValueError,match='冲突'):
        AgentRegistry([specimen('alpha',produces=('shared',)),specimen('beta',produces=('shared',))]).plan()
    with pytest.raises(ValueError,match='未依赖'):
        AgentRegistry([specimen('alpha',produces=('shared',)),specimen('beta',requires=('shared',))]).plan()


def test_runner_detects_missing_output_and_prevents_false_success():
    registry=AgentRegistry([specimen('stage',produces=('out',),execute=lambda _:AgentResult({}))])
    with TemporaryDirectory() as td:
        run=AgentRunner(registry)
        with pytest.raises(ValueError,match='缺少输出'):
            run.run(ctx(td))
        assert run.last_trace['state']=='failed'
        assert run.last_trace['steps'][0]['state']=='failed'


def test_optional_failure_isolated_and_report_does_not_skip():
    def broken(_):
        raise RuntimeError('sk-secret-DO-NOT-LOG')
    registry=AgentRegistry([
        specimen('profile',produces=('available',)),
        specimen('ranking',deps=('profile',),requires=('available',),policy='continue',execute=broken),
        specimen('storage',deps=('profile',),requires=('available',),produces=('saved',)),
    ])
    with TemporaryDirectory() as td:
        run=AgentRunner(registry)
        output=run.run(ctx(td))
    assert output['saved']=='storage'
    assert run.last_trace['state']=='partial'
    assert [s['state'] for s in run.last_trace['steps']]==['success','failed','success']
    assert 'sk-secret' not in json.dumps(run.last_trace)


def test_failed_dependency_skips_dependant_but_not_unrelated():
    def broken(_): raise OSError('offline')
    registry=AgentRegistry([
        specimen('source',policy='continue',execute=broken),
        specimen('downstream',deps=('source',)),
        specimen('independent',produces=('ok',)),
    ])
    with TemporaryDirectory() as td:
        run=AgentRunner(registry)
        output=run.run(ctx(td))
    assert output['ok']=='independent'
    assert [s['state'] for s in run.last_trace['steps']]==['failed','skipped_dependency','success']


def test_critical_failure_stops_pipeline():
    def broken(_): raise RuntimeError('network down')
    registry=AgentRegistry([
        specimen('discovery',execute=broken),
        specimen('report',deps=('discovery',)),
    ])
    with TemporaryDirectory() as td:
        run=AgentRunner(registry)
        with pytest.raises(RuntimeError,match='network down'):
            run.run(ctx(td))
    assert run.last_trace['state']=='failed'
    assert len(run.last_trace['steps'])==1


def test_bounded_trace_persists_only_metadata():
    with TemporaryDirectory() as td:
        dest=Path(td)/'agent_traces.sqlite3'
        fixture={'run_id':'run-1','topic_id':'default','started_at':NOW.isoformat(),
                 'state':'success','steps':[{'agent':'search','state':'success','elapsed_ms':2,'metrics':{'retrieved':3}}]}
        store_trace(fixture,dest)
        assert read_traces(dest,topic_id='default')[0]['run_id']=='run-1'
        assert read_traces(dest,topic_id='another')==[]
        assert 'raw_prompt' not in dest.read_bytes().decode('utf-8','ignore')


def test_v112_run_cycle_is_now_backed_by_agent_dag_and_trace():
    with TemporaryDirectory() as td:
        root=Path(td)
        output=run_cycle({**DEFAULTS,'output_dir':str(root/'reports')},
                         db_path=root/'data'/'papers.sqlite3',now=NOW,
                         fetcher=lambda *a,**kw:[paper()],logger=lambda *_:None)
        assert output['new']==1 and output['selected']==1
        assert output['weekly'].is_file() and output['markdown'].is_file()
        assert output['agent_run']['state']=='success'
        steps=output['agent_run']['steps']
        assert steps[-1]['agent']=='report'
        assert any(s['agent']=='summary' and s['state']=='skipped' for s in steps)
        assert any(s['agent']=='download' and s['state']=='skipped' for s in steps)
        assert read_traces(root/'data'/'agent_traces.sqlite3')[0]['run_id']==output['agent_run']['run_id']


def test_source_error_still_does_not_generate_fake_report_and_is_traced():
    with TemporaryDirectory() as td:
        root=Path(td)
        def fail(*a,**kw):raise RuntimeError('mock provider offline')
        with pytest.raises(RuntimeError,match='offline'):
            run_cycle({**DEFAULTS,'output_dir':str(root/'reports')},
                      db_path=root/'data'/'papers.sqlite3',now=NOW,fetcher=fail)
        assert not (root/'reports'/'近期文献汇总.docx').exists()
        runs=read_traces(root/'data'/'agent_traces.sqlite3')
        assert runs[0]['state']=='failed'
        assert runs[0]['steps'][-1]['agent']=='discovery'


def test_get_agent_manifest_api_is_local_and_read_only():
    with TemporaryDirectory() as td:
        dest=Path(td)/'papers.sqlite3'
        server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
        server.token='test-123'
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch.object(webapp,'DB_FILE',dest),patch.object(webapp,'read_config',return_value={**DEFAULTS,'active_topic_id':'default'}):
                with urlopen(f'http://127.0.0.1:{server.server_port}/api/agents') as response:
                    payload=json.load(response)
            assert len(payload['pipeline'])==9
            assert payload['recent_runs']==[]
            assert payload['pipeline'][0]['name']=='profile'
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)


def test_agent_ui_controls_and_sanitized_template_present():
    root=Path(__file__).resolve().parent.parent
    html=(root/'paper_radar/static/index.html').read_text(encoding='utf-8')
    js=(root/'paper_radar/static/app.js').read_text(encoding='utf-8')
    assert 'id="agentGrid"' in html and 'id="refreshAgents"' in html
    assert 'async function loadAgents()' in js and "escape(a.purpose)" in js
    assert "api('/api/agents')" in js


def test_http_runtime_preserves_partial_agent_status():
    """The UI must not call an optional Agent failure an unconditional success."""
    engine=webapp.Runtime()
    cfg={**DEFAULTS,'active_topic_id':'default'}
    outcome={'retrieved':1,'new':0,'selected':1,'agent_run':{'state':'partial'}}
    captured=[]
    with patch.object(webapp,'run_cycle',return_value=outcome), \
         patch.object(webapp,'write_status',side_effect=captured.append), \
         patch.object(webapp,'record_run') as history, \
         patch.object(engine,'push',lambda *_:None):
        engine._worker('search',[],cfg)
    assert captured[-1]['state']=='partial'
    assert history.call_args.args[2]=='partial'


def test_batch_runner_preserves_partial_agent_status():
    from paper_radar.batch import run_all_topics
    recorded=[]
    fake=lambda *_args,**_kwargs:{'retrieved':1,'new':0,'selected':1,
                                  'agent_run':{'state':'partial'}}
    result=run_all_topics({**DEFAULTS,'active_topic_id':'default'},
                          runner=fake,recorder=lambda *a,**kw:recorded.append(a),
                          logger=lambda *_:None)
    assert result['topics_succeeded']==1 and result['topics_failed']==0
    assert result['topics_partial']==1
    assert result['topics'][0]['state']=='partial'
    assert recorded[0][2]=='partial'


def test_project_and_agent_versions_consistent():
    from paper_radar import __version__
    from paper_radar.agents.catalog import VERSION
    from pathlib import Path
    root=Path(__file__).resolve().parent.parent
    assert __version__==VERSION
    assert f'version = "{__version__}"' in (root/'pyproject.toml').read_text(encoding='utf-8')
    assert f'Paper-Radar v{__version__}' in (root/'paper_radar/static/index.html').read_text(encoding='utf-8')
