"""v1.47.1 regression tests: nontechnical research workflow, terminology and routes."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from http.server import ThreadingHTTPServer
from contextlib import contextmanager
from threading import Thread
from urllib.request import urlopen
import re
import shutil
import subprocess
import pytest

from paper_radar.webapp import Handler as LocalHandler
from paper_radar.public_portal import Portal, make_server

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'paper_radar' / 'static'
LOCAL = (STATIC / 'index.html').read_text('utf-8')
PUBLIC = (STATIC / 'portal.html').read_text('utf-8')
APP_JS = (STATIC / 'app.js').read_text('utf-8')
PORTAL_JS = (STATIC / 'portal.js').read_text('utf-8')
NAV = ('首页', '找论文', '我的文献', '研究分析', '我的报告', '设置')


def test_same_plain_language_navigation_in_both_workspaces():
    for label in NAV:
        assert re.search(r'<button[^>]*data-view="[^"]+"[^>]*>[\s\S]*?<span>'+label+r'</span>', LOCAL)
        assert label in PUBLIC
    assert 'id="home"' in LOCAL and 'id="viewWorkspace"' in PUBLIC
    assert 'id="localExtraTools"' in LOCAL
    assert 'id="homeGuide"' in LOCAL
    assert 'id="portalQuickKeywords"' in PUBLIC


def test_all_dom_ids_are_unique():
    for html in (LOCAL, PUBLIC):
        ids = re.findall(r'\bid="([^"]+)"', html)
        assert len(ids) == len(set(ids)), [k for k,v in Counter(ids).items() if v > 1]


def test_core_previous_html_hooks_are_retained():
    local_ids = ('runSearch','libraryList','localInquiryWorkbench','localResearchProgram',
                 'fulltextDialog','latestReport','reportList','taskCenter','activeTopic',
                 'topicKeywords','sourceOptions','apiKey','zoteroSyncPaper')
    portal_ids = ('searchNow','paperList','fulltextArea','publicInquiryWorkbench',
                  'publicResearchProgram','reportHistory','reportReader',
                  'topicSelect','editTopicName','portalQuickKeywords')
    for id in local_ids:
        assert f'id="{id}"' in LOCAL, id
    for id in portal_ids:
        assert f'id="{id}"' in PUBLIC, id


def test_local_search_sends_partial_config_before_job():
    snippet = APP_JS.split('function searchValues()',1)[1].split('function refreshAiDiscovery',1)[0]
    assert 'max_queries_per_source' in snippet
    assert 'keywords:' in snippet and 'sources:' in snippet
    assert "await api('/api/config',payload)" in snippet
    assert 'if(search)await saveSearchOnly()' in snippet
    assert 'await save(true)' not in snippet
    assert 'api_key:' not in snippet
    assert 'llm_data_consent:' not in snippet
    assert 'schedule_time:' not in snippet


def test_public_search_changes_only_selected_topic_and_runs():
    section=PORTAL_JS.split('function fillPortalQuickSearch()',1)[1].split('const selectedSources',1)[0]
    assert "'/api/public/topics/update'" in section
    assert 'topic_id:currentTopic,name:item.name,keywords:words,sources' in section
    assert '请至少选择一个论文网站' in section
    assert 'words.length<1||words.length>6' in section
    assert 'fillTopicEditor()' in section
    assert "await preparePortalSearch()" in PORTAL_JS
    assert "'/api/public/search'" in PORTAL_JS


def test_research_question_is_shared_without_duplicate_primary_input():
    assert "getSharedQuestion:()=>localInquiry?.getQuestion()||''" in APP_JS
    assert 'getSharedQuestion' in (STATIC/'research_program.js').read_text('utf-8')
    assert 'publicInquiry?.getQuestion' in PORTAL_JS
    assert 'id="publicAnalysisDeepDive"' in PUBLIC
    assert 'class="panel analysis-advanced-tools"' in LOCAL or 'analysis-advanced-tools' in LOCAL


def test_advanced_public_library_operations_are_collapsed():
    code=PORTAL_JS.split('async function refreshPapers(',1)[1].split('function requestPaperRefresh',1)[0]
    assert "node('button','阅读论文',actions,'primary paper-read-main')" in code
    assert "node('details','',actions,'paper-expert-actions')" in code
    assert "node('button','上传 PDF',extra)" in code
    assert "node('button','分析 PDF',extra)" in code
    assert 'paper-expert-actions' in (STATIC/'portal_ux_v147.css').read_text('utf-8')


def test_onboarding_steps_and_user_guide_are_synced():
    assert "['topic','search','read','analysis','report']" in APP_JS
    assert LOCAL.count('data-guide-step=') == 5
    guide=(ROOT/'docs'/'USER_GUIDE.md').read_text('utf-8')
    for item in ('<!-- GUIDE:quickstart -->','<!-- GUIDE:usage -->','<!-- GUIDE:sources -->',
                 '<!-- GUIDE:reading -->','<!-- GUIDE:inquiry -->'):
        assert guide.count(item)==1
    assert '[首页](app:home)' in guide
    assert '普通检索不要求配置大模型 API Key' in guide


def test_version_and_new_css_routes_are_registered():
    import paper_radar
    from paper_radar.agents.catalog import VERSION
    assert paper_radar.__version__==VERSION
    assert 'v'+VERSION in LOCAL and 'v'+VERSION in PUBLIC
    assert '/ux_v147.css' in LOCAL and '/portal_ux_v147.css' in PUBLIC
    assert "'/ux_v147.css'" in (ROOT/'paper_radar'/'webapp.py').read_text('utf-8')
    assert "'/portal_ux_v147.css'" in (ROOT/'paper_radar'/'public_portal.py').read_text('utf-8')


@contextmanager
def serving(server):
    thread=Thread(target=server.serve_forever, daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown();thread.join(timeout=5);server.server_close()


def test_css_is_served_by_local_and_public_http(tmp_path):
    local=ThreadingHTTPServer(('127.0.0.1',0),LocalHandler)
    local.token='test-ux-147'
    with serving(local) as origin:
        with urlopen(origin+'/ux_v147.css',timeout=6) as rsp:
            assert rsp.status==200 and b'home-getting-started' in rsp.read()
    portal=Portal(tmp_path/'public',embedded_workers=False)
    public=make_server('127.0.0.1',0,portal)
    with serving(public) as origin:
        with urlopen(origin+'/portal_ux_v147.css',timeout=6) as rsp:
            assert rsp.status==200 and b'portal-search-field' in rsp.read()


@pytest.mark.skipif(shutil.which('node') is None,reason='Node unavailable')
def test_javascript_files_parse():
    for name in ('app.js','portal.js','ui_state.js','inquiry_workbench.js',
                 'research_program.js','reading_links.js'):
        result=subprocess.run(['node','--check',str(STATIC/name)],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=10)
        assert result.returncode==0,(name,result.stderr)
