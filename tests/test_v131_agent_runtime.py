# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.13.1 isolated stage governance regression suite; all tests use mock I/O."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import sleep
from unittest.mock import patch
import json
import math
import subprocess
import sys

import pytest

from paper_radar.agents import Agent, AgentSpec, AgentResult, AgentContext, AgentRegistry, AgentRunner
from paper_radar.agents.catalog import build_registry
from paper_radar.agents.contracts import AgentCancelled, AgentTransientError
from paper_radar.progress import TaskProgress
from paper_radar.provider_usage import record_usage, read_usage
from paper_radar.core import validate_config, DEFAULTS


def make_ctx(root, **cfg):
    return AgentContext({**DEFAULTS, **cfg},Path(root)/'papers.sqlite3',Path(root)/'reports',
                        lambda *_a,**_kw:[],datetime(2026,11,4,tzinfo=timezone.utc),
                        lambda *_a:None,TaskProgress())


def agent(name, execute, *, deps=(), produces=('value',), caps=(), policy='stop',
          attempts=1, budget=0.0):
    return Agent(AgentSpec(name,name,'1.0','test',depends_on=deps,produces=produces,
                           capabilities=caps, failure_policy=policy, max_attempts=attempts,
                           time_budget_seconds=budget),execute)


def test_builtin_manifest_has_declared_privileges_and_read_only_retry():
    reg=build_registry()
    specs={a['name']:a for a in reg.manifest()}
    assert specs['discovery']['max_attempts']==2
    assert specs['discovery']['capabilities']==['network_read']
    assert 'cloud_model' in specs['ranking']['capabilities']
    assert 'database_write' in specs['storage']['capabilities']
    assert specs['summary']['max_attempts']==1
    assert all(float(item['time_budget_seconds'])>=0 for item in specs.values())


@pytest.mark.parametrize('caps',[('database_write',),('filesystem_write',),('cloud_model',)])
def test_registry_forbids_side_effect_retries(caps):
    with pytest.raises(ValueError,match='禁止自动重试'):
        AgentRegistry([agent('unsafe',lambda c:AgentResult({'value':True}),caps=caps,attempts=2)])


@pytest.mark.parametrize('invalid',[-1,4,0,True])
def test_attempt_limit_rejects_invalid_types(invalid):
    with pytest.raises(ValueError,match='执行预算'):
        AgentRegistry([Agent(AgentSpec('my_agent','x','1','test',max_attempts=invalid),lambda c:AgentResult())])


def test_cloud_flag_requires_declaration():
    with pytest.raises(ValueError,match='权限'):
        AgentRegistry([Agent(AgentSpec('my_agent','x','1','test',sends_to_cloud=True),lambda c:AgentResult())])


def test_safe_transient_retry_reaches_success_and_no_exception_message_in_trace():
    with TemporaryDirectory() as root:
        calls=[]
        def unstable(c):
            calls.append(1)
            if len(calls)==1:raise AgentTransientError('sk-private-retry-error')
            return AgentResult({'value':'success'},metrics={'processed':1})
        runner=AgentRunner(AgentRegistry([agent('discovery',unstable,attempts=2,caps=('network_read',))]))
        result=runner.run(make_ctx(root), faults=None)
        assert result['value']=='success'
        step=runner.last_trace['steps'][0]
        assert step['attempts']==2 and step['state']=='success'
        assert 'sk-private-retry-error' not in json.dumps(runner.last_trace)


def test_fault_injection_is_test_only_and_does_not_call_source():
    with TemporaryDirectory() as root:
        calls=[]
        def run(c):
            calls.append(1);return AgentResult({'value':'ok'})
        runner=AgentRunner(AgentRegistry([agent('discovery',run,attempts=2)]))
        result=runner.run(make_ctx(root),faults={'discovery':[TimeoutError('injected fault')]})
        assert result['value']=='ok'
        assert calls==[1]
        assert runner.last_trace['steps'][0]['attempts']==2


def test_permanent_error_never_retried_even_with_budget():
    with TemporaryDirectory() as root:
        calls=[]
        def fail(c):
            calls.append(1);raise ValueError('bad parse')
        runner=AgentRunner(AgentRegistry([agent('reader',fail,attempts=3)]))
        with pytest.raises(ValueError,match='bad parse'):
            runner.run(make_ctx(root))
        assert len(calls)==1
        assert runner.last_trace['state']=='failed'
        assert runner.last_trace['steps'][0]['attempts']==1


def test_undeclared_output_rejected():
    with TemporaryDirectory() as root:
        runner=AgentRunner(AgentRegistry([agent('reader',lambda c:AgentResult({'value':1,'secret':'x'}))]))
        with pytest.raises(ValueError,match='未声明的字段'):
            runner.run(make_ctx(root))
        assert 'secret' not in json.dumps(runner.last_trace)


def test_application_capability_guard_is_enforced_at_declared_boundary():
    with TemporaryDirectory() as root:
        runner=AgentRunner(AgentRegistry([agent('reader',lambda c:(c.require('database_write'),AgentResult({'value':1}))[1],
                                               caps=('database_read',))]))
        with pytest.raises(PermissionError,match='未声明能力'):
            runner.run(make_ctx(root))
        assert runner.last_trace['steps'][0]['error_type']=='PermissionError'


def test_cooperative_deadline_marks_failed_without_retry():
    with TemporaryDirectory() as root:
        def slow(c):
            sleep(.01)
            c.checkpoint()
            return AgentResult({'value':1})
        runner=AgentRunner(AgentRegistry([
            agent('slow_reader',slow,budget=.001,policy='continue'),
            agent('independent',lambda c:AgentResult({'value2':'ok'}),produces=('value2',))]))
        result=runner.run(make_ctx(root))
        assert result['value2']=='ok'
        assert runner.last_trace['state']=='partial'
        assert runner.last_trace['steps'][0]['error_type']=='AgentDeadlineExceeded'
        assert runner.last_trace['steps'][0]['budget_exceeded']


def test_cancellation_is_not_silently_swallowed():
    with TemporaryDirectory() as root:
        ctx=make_ctx(root);ctx.cancel_event=Event();ctx.cancel_event.set()
        runner=AgentRunner(AgentRegistry([agent('reader',lambda c:AgentResult({'value':1}),policy='continue')]))
        with pytest.raises(AgentCancelled):runner.run(ctx)
        assert runner.last_trace['state']=='cancelled'


def test_bounded_token_usage_and_user_supplied_price_estimate():
    with TemporaryDirectory() as root:
        outfile=Path(root)/'usage.json'
        def model(c):
            record_usage('fake-provider',103,209,path=outfile)
            return AgentResult({'value':'ok'},metrics={'valid':1,'bad':'secret','nan':float('nan')})
        runner=AgentRunner(AgentRegistry([agent('model',model,caps=('cloud_model',))]))
        runner.run(make_ctx(root,llm_input_usd_per_million=2,llm_output_usd_per_million=10))
        step=runner.last_trace['steps'][0]
        assert step['api_usage']=={'requests':1,'input_tokens':103,'output_tokens':209}
        assert step['estimated_cost_usd']==0.002296
        assert step['metrics']=={'valid':1}
        assert runner.last_trace['api_usage']['input_tokens']==103
        assert read_usage(outfile)['fake-provider']['requests']==1
        assert 'secret' not in json.dumps(runner.last_trace)


def test_no_pricing_means_no_misleading_zero_cost():
    with TemporaryDirectory() as root:
        def model(c):
            record_usage('p',1000,100,path=Path(root)/'usage.json')
            return AgentResult({'value':1})
        runner=AgentRunner(AgentRegistry([agent('model',model,caps=('cloud_model',))]))
        runner.run(make_ctx(root))
        assert 'estimated_cost_usd' not in runner.last_trace
        assert 'estimated_cost_usd' not in runner.last_trace['steps'][0]


def test_cost_fields_validate_and_preserve_legacy_configs():
    cfg=validate_config(DEFAULTS)
    assert cfg['llm_input_usd_per_million']==0
    assert validate_config({**DEFAULTS,'llm_input_usd_per_million':1.25})['llm_input_usd_per_million']==1.25
    with pytest.raises(ValueError,match='单价'):
        validate_config({**DEFAULTS,'llm_input_usd_per_million':float('nan')})
    with pytest.raises(ValueError,match='单价'):
        validate_config({**DEFAULTS,'llm_output_usd_per_million':-1})


def test_doctor_offline_and_ui_field_bindings():
    root=Path(__file__).resolve().parent.parent
    result=subprocess.run([sys.executable,'-m','paper_radar.agents','doctor'],
                          cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          text=True,timeout=15)
    assert result.returncode==0
    payload=json.loads(result.stdout)
    assert payload['agent_count']==16 and payload['state']=='ok'
    html=(root/'paper_radar/static/index.html').read_text(encoding='utf-8')
    js=(root/'paper_radar/static/app.js').read_text(encoding='utf-8')
    for ident in ('llmInputRate','llmOutputRate','agentOverview'):
        assert f'id="{ident}"' in html and f"$('{'%s'%ident}')" in js
    assert '最近执行：' in js and '重试 ' in js
