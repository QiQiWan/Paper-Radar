"""v1.47.1: recovery, readable artifact status and evidence caveats."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'paper_radar'/'static'
APP=(STATIC/'app.js').read_text('utf-8')
PUBLIC=(STATIC/'portal.js').read_text('utf-8')
LOCAL_HTML=(STATIC/'index.html').read_text('utf-8')
PUBLIC_HTML=(STATIC/'portal.html').read_text('utf-8')
INQUIRY=(STATIC/'inquiry_workbench.js').read_text('utf-8')


def test_local_reconnect_is_read_only_and_visible():
    assert 'id="localConnectionRecovery"' in LOCAL_HTML
    assert 'id="retryLocalConnection"' in LOCAL_HTML
    assert 'async function retryLocalConnection()' in APP
    snippet=APP.split('async function retryLocalConnection()',1)[1].split('\n  }',1)[0]
    assert 'loadBootstrap' in snippet and 'updateState' in snippet
    assert "await api('/api/config'" not in snippet
    assert "await api('/api/search'" not in snippet


def test_public_poll_failed_state_does_not_enqueue():
    section=PUBLIC.split('async function refreshJobState()',1)[1].split("$('searchNow').onclick",1)[0]
    assert 'retryJobPoll()' in section
    assert '任务状态查询失败，正在自动重连；后台任务不会重复提交' in section
    assert "'/api/public/search'" not in section
    assert "'/api/public/research/synthesize'" not in section


def test_report_and_pdf_labels_are_for_readers():
    assert 'PDF 已保存，尚未读取全文' in PUBLIC
    assert '已完成全文读取' in PUBLIC
    assert "res.document.state} ·" not in PUBLIC
    assert "快照 ${report.source_digest" not in PUBLIC
    assert 'id="reportAuditMeta"' in PUBLIC_HTML
    assert '报告来源信息' in PUBLIC_HTML
    assert '生成研究报告' in PUBLIC_HTML
    assert '错误编号' in PUBLIC
    assert "$('reportAuditMeta').textContent" in PUBLIC


def test_evidence_empty_state_is_scientifically_qualified():
    assert '可靠的实验比较结论' in INQUIRY
    assert 'inquiry-next-actions' in INQUIRY
    assert 'onNavigate' in INQUIRY
    assert '未完成的来源' in INQUIRY


def test_version_matches_static_and_core():
    from paper_radar import __version__
    from paper_radar.agents.catalog import VERSION
    assert __version__==VERSION
    assert 'v'+__version__ in LOCAL_HTML and 'v'+__version__ in PUBLIC_HTML


@pytest.mark.skipif(not shutil.which('node'),reason='Node.js absent')
def test_status_summaries_include_artifact_outcomes():
    code=f'''const UI=require({json.dumps(str(STATIC/'ui_state.js'))});
const outcomes=[
UI.taskSummary({{state:'success',kind:'synthesize',result:{{paper_count:0,library_paper_count:13,finding_count:0}}}}),
UI.taskSummary({{state:'success',kind:'analyze',result:{{analysis:{{pages:4,evidence_count:1}}}}}}),
UI.taskSummary({{state:'failed',kind:'synthesize',error:'quota exhausted'}}),
UI.requestMessage(503,'internal details','public'),
UI.requestMessage(401,'expired','public'),
UI.requestMessage(429,'已达到过去 24 小时的报告生成配额','public')];
process.stdout.write(JSON.stringify(outcomes));'''
    run=subprocess.run(['node','-e',code],capture_output=True,text=True,timeout=12,check=True)
    report,pdf,failed,unavailable,expired,quota=json.loads(run.stdout)
    assert report['label']=='报告生成完成'
    assert '13 篇题录' in report['detail'] and '0 篇有证据的论文' in report['detail'] and '尚无可比较' in report['detail']
    assert pdf['label']=='PDF 分析完成' and '4 页' in pdf['detail']
    assert failed['label']=='报告生成失败'
    assert '重复提交' in unavailable
    assert '重新登录' in expired
    assert '报告生成配额' in quota


def test_qa_journeys_cover_actual_handlers():
    script=(ROOT/'tools'/'qa_v1471_journeys.py').read_text('utf-8')
    for key in ('ThreadingHTTPServer','make_server','portal.workspace','offline_search_and_library',
                'pdf_upload_read','generate_report_and_read','job_poll_network_recovery',
                'inquiry_empty_evidence','PAPER_RADAR_USER_HOME'):
        assert key in script


def test_metadata_only_papers_appear_in_report_without_becoming_verified(tmp_path):
    from paper_radar.public_portal import Portal
    from paper_radar.portal_reports import create_report,read_report
    portal=Portal(tmp_path/'portal',embedded_workers=False)
    uid='a'*32
    with portal.workspace(uid) as db:
        db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',(
            'synthetic:test','A paper with [unverified] results','QA-only metadata',
            '2026-10-01','https://example.invalid/p','', '2026-10-01'))
        db.execute('INSERT INTO topic_papers VALUES (?,?)',('default','synthetic:test'))
    report=create_report(portal,uid,'default')
    saved=read_report(portal,uid,report['id'])
    assert report['library_paper_count']==1
    assert report['paper_count']==0 and report['finding_count']==0
    assert saved['summary']['library_paper_count']==1
    assert '已收录文献概览' in saved['markdown']
    assert 'A paper with' in saved['markdown']
    assert '不能据此认定论文已完成全文核对' in saved['markdown']
    assert '有可核对证据的实验分析' in saved['markdown']
    assert '未核对的题录不会进入数值比较' in saved['markdown']
