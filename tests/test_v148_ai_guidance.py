# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""User-facing AI defaults, credential guidance, simple-navigation contracts."""
from pathlib import Path
from bs4 import BeautifulSoup
from paper_radar.core import DEFAULTS,validate_config,read_config
import json
import re

ROOT=Path(__file__).resolve().parents[1]
S=ROOT/'paper_radar/static'
LOCAL=(S/'index.html').read_text('utf-8')
PORTAL=(S/'portal.html').read_text('utf-8')
LOCAL_JS=(S/'app.js').read_text('utf-8')
PORTAL_JS=(S/'portal.js').read_text('utf-8')

def test_new_install_enables_two_local_ai_features_without_granting_consent():
    cfg=validate_config(DEFAULTS.copy())
    assert cfg['ai_enabled'] is True
    assert cfg['ai_rerank_enabled'] is True
    assert cfg['llm_data_consent'] is False

def test_persisted_opt_out_is_honored(tmp_path):
    settings=tmp_path/'settings.json'
    settings.write_text(json.dumps({'ai_enabled':False,'ai_rerank_enabled':False,'llm_data_consent':False}),'utf-8')
    c=read_config(settings)
    assert c['ai_enabled'] is False and c['ai_rerank_enabled'] is False
    assert c['llm_data_consent'] is False

def test_local_has_model_connection_banner_and_one_step_config_entry():
    page=BeautifulSoup(LOCAL,'html.parser')
    assert page.select_one('#aiConnectionBanner[role=status]') is not None
    assert page.select_one('#aiBannerSetup') is not None
    assert page.select_one('#aiSetupState') is not None
    assert page.select_one('#aiProviderGetKey') is not None
    assert page.select_one('#apiKey[type=password]') is not None
    assert page.select_one('#llmConsent[type=checkbox]') is not None
    assert 'checked' not in page.select_one('#llmConsent').attrs
    assert 'checked' in page.select_one('#aiEnabled').attrs
    assert 'checked' in page.select_one('#aiRerankEnabled').attrs
    assert '设置' in LOCAL and '模型服务' in LOCAL

def test_disabled_ai_does_not_become_implicitly_enabled_during_research():
    inquiry=(S/'inquiry_workbench.js').read_text('utf-8')
    synthesis=(S/'research_program.js').read_text('utf-8')
    assert '!!getModelState().enabled' in inquiry
    assert '!!getModelState().enabled' in synthesis
    assert '!!readiness.ready' in inquiry
    assert '&&ready' in synthesis
    assert 'confirm_transfer' in inquiry and 'confirm_transfer' in synthesis

def test_model_settings_save_isolated_from_search_and_automatic_update():
    assert 'async function saveAiOnly' in LOCAL_JS
    block=LOCAL_JS.split('async function saveAiOnly',1)[1].split('function setSettings',1)[0]
    for key in ('ai_enabled','ai_rerank_enabled','api_provider','api_model','llm_data_consent'):
        assert key in block
    for key in ('schedule_time','keywords:commaList','lookback_days','auto_update_on_startup','openreview_invitation'):
        assert key not in block
    assert "api('/api/config',c)" in block
    assert "api('/api/state')" in block
    assert "$('apiKey').value=''" in block

def test_user_can_skip_banner_temporarily_but_future_unconfigured_use_guides_again():
    assert 'aiReminderPaused' in LOCAL_JS
    assert 'openAiSetup' in LOCAL_JS
    assert 'aiReminderPaused=false;refreshAiDiscovery' in LOCAL_JS
    assert "localStorage.setItem('paper-radar-ai-prompt-dismissed'" not in LOCAL_JS

def test_keyword_planning_requires_model_ready_before_remote_task():
    prompt=LOCAL_JS.split('async function planKeywords',1)[-1]
    assert "if(!getLocalModelState().ready)" in LOCAL_JS
    assert "openAiSetup();return" in LOCAL_JS

def test_public_cloud_checkbox_defaults_on_after_login_but_remains_opt_out():
    assert "$('cloudAnalysis').checked=readPublicAiPreference()" in PORTAL_JS
    assert "localStorage.getItem(publicAiPreferenceKey())!=='off'" in PORTAL_JS
    assert "$('cloudAnalysis').addEventListener('change',savePublicAiPreference)" in PORTAL_JS
    assert 'cloud_analysis:false' in PORTAL_JS

def test_public_missing_key_can_continue_offline_and_no_key_is_embedded_in_page():
    page=BeautifulSoup(PORTAL,'html.parser')
    assert page.select_one('#portalAiBanner[role=status]') is not None
    assert page.select_one('#portalAiGuide') is not None
    assert page.select_one('#portalAiSettingsState') is not None
    assert page.select_one('#portalAdminAiSteps') is not None
    assert "api('/api/public/model/preflight?action=experiments')" in PORTAL_JS
    assert "cloud_analysis:false" in PORTAL_JS
    assert 'confirm_cloud_data_transfer:true' in PORTAL_JS
    assert 'PAPERRADAR_PUBLIC_LLM_KEY' in PORTAL
    assert not re.search(r'sk-[A-Za-z0-9]{16,}',PORTAL)

def test_simple_home_and_search_preserve_access_to_advanced_controls():
    page=BeautifulSoup(LOCAL,'html.parser')
    assert page.select_one('#homeGuide') is not None
    assert 'open' not in page.select_one('#homeGuide').attrs
    assert page.select_one('#searchResultsHistory') is not None
    assert 'open' not in page.select_one('#searchResultsHistory').attrs
    assert page.select_one('#searchAdvanced') is not None
    assert page.select_one('#aiPlannerDisclosure') is not None
    public=BeautifulSoup(PORTAL,'html.parser')
    assert public.select_one('.library-extra-tools summary') is not None
    assert public.select_one('.home-expert-queue') is not None
    assert public.select_one('#portalAiBanner') is not None

def test_no_duplicate_html_ids_or_missing_page_routes():
    for source in (LOCAL,PORTAL):
        bs=BeautifulSoup(source,'html.parser')
        ids=[el.get('id') for el in bs.find_all(id=True)]
        assert len(ids)==len(set(ids)),[x for x in ids if ids.count(x)>1][:10]
    for label in ['首页','找论文','我的文献','研究分析','我的报告']:
        assert label in LOCAL and label in PORTAL
