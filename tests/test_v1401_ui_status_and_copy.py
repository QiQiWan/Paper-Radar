"""v1.40.1: correct retrieval outcomes and consistent desktop/public copy."""
from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path
import pytest
from paper_radar.task_outcome import retrieval_outcome
from tools.ui_copy_lint import check as check_ui

ROOT=Path(__file__).resolve().parents[1]
S=ROOT/'paper_radar'/'static'

def test_server_truth_table():
    examples=[
        ([{'ok':False,'error_code':'DNS_ERROR'},{'ok':False,'timed_out':True}],0,0,'failed',0),
        ([{'ok':True,'count':0},{'ok':True,'count':0}],0,0,'empty',2),
        ([{'ok':True,'count':1},{'ok':False,'error_code':'RATE_LIMIT'}],1,1,'partial',1),
        ([{'ok':True,'count':1,'partial':True}],1,1,'partial',0),
        ([{'ok':True,'cache_state':'stale'}],2,0,'partial',0),
        ([{'ok':True,'count':1}],1,1,'success',1),
        ([],0,0,'unknown',0),
    ]
    for rows,found,added,label,ok in examples:
        state=retrieval_outcome(rows,found=found,added=added)
        assert state['outcome']==label
        assert state['sources_ok']==ok
        assert state['sources_ok']+state['sources_partial']+state['sources_failed']==len(rows)


def test_ui_copy_lint():
    info=check_ui()
    assert info['status_component']
    assert info['nav_labels']==7


def test_technical_ids_and_features_retained():
    local=(S/'index.html').read_text('utf-8')
    portal=(S/'portal.html').read_text('utf-8')
    trace=(S/'trace_workbench.js').read_text('utf-8')
    assert 'id="runSearch"' in local and 'id="aiPlanRequest"' in local
    assert 'id="openSourceSettings"' in local
    assert 'id="modelEstimateAction"' in local and 'id="llmConsent"' in local
    assert 'id="searchNow"' in portal and 'id="cloudAnalysis"' in portal
    assert '/guide/ai' in local and '/guide/ai' in portal
    assert 'SHA-256' in trace and "'技术详情'" in trace


def test_shared_task_status_in_node():
    node=shutil.which('node')
    if not node:pytest.skip('Node executable unavailable')
    script=f"""
const u=require({json.dumps(str(S/'ui_state.js'))});
const f=(state,rows,extra={{}})=>u.taskSummary({{state,mode:'search',result:{{source_results:rows,...extra}}}});
const failed=f('success',[{{ok:false,error_code:'DNS_ERROR'}},{{ok:false,timed_out:true}}]);
const empty=f('success',[{{ok:true,count:0}}]);
const partial=f('success',[{{ok:true,count:2}},{{ok:false,error_code:'RATE_LIMIT'}}],{{new:2,retrieved:2}});
const good=f('success',[{{ok:true,count:3}}],{{new:3,retrieved:3}});
const unknown=f('success',[]);
const all=u.taskSummary({{state:'success',mode:'search_all',result:{{topics_succeeded:2,topics_failed:1,new:7}}}});
const nonsearch=u.taskSummary({{state:'failed',mode:'export',error:'disk I/O error'}});
console.log(JSON.stringify({{failed,empty,partial,good,unknown,all,nonsearch,error:u.sourceState({{ok:false,error_code:'DNS_ERROR'}})}}));
"""
    result=subprocess.run([node,'-e',script],capture_output=True,text=True,timeout=10,check=True)
    states=json.loads(result.stdout)
    assert states['failed']['kind']=='failed' and states['failed']['sources_ok']==0
    assert states['empty']['kind']=='empty' and '未找到' in states['empty']['label']
    assert states['partial']['kind']=='partial' and states['partial']['sources_ok']==1
    assert states['good']['kind']=='success'
    assert states['unknown']['kind']=='unknown'
    assert states['all']['kind']=='partial' and '主题' in states['all']['detail']
    assert states['nonsearch']['label']=='任务失败'
    assert '域名' in states['error']


def test_static_routes_are_authorized():
    web=(ROOT/'paper_radar/webapp.py').read_text('utf-8')
    public=(ROOT/'paper_radar/public_portal.py').read_text('utf-8')
    assert "'/ui_state.js'" in web and "'/ui_state.js'" in public
    assert 'retrieval_summary' in web
