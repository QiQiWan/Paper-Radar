# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""UI cleanup contract: less default clutter without losing existing actions.

All checks are offline, standard-library-only and safe for GitHub CI.
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'paper_radar' / 'static'


class Controls(HTMLParser):
    """Track ID locations and native disclosure ownership."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.ids: dict[str, tuple[str, tuple[str, ...], dict[str, str | None]]] = {}
        self.duplicates: list[str] = []
        self.disclosures: list[dict[str, str | None]] = []
        self.stack: list[str] = []
        self.feed(source)

    def handle_starttag(self, tag: str, attributes):
        attrs = dict(attributes)
        if tag == 'details':
            self.disclosures.append(attrs)
            self.stack.append(attrs.get('id') or 'details:' + ' '.join((attrs.get('class') or '').split()))
        if attrs.get('id'):
            value = attrs['id']
            if value in self.ids:
                self.duplicates.append(value)
            self.ids[value] = tag, tuple(self.stack), attrs

    def handle_endtag(self, tag: str):
        if tag == 'details' and self.stack:
            self.stack.pop()


def view(name):
    return Controls((STATIC / name).read_text(encoding='utf-8'))


def test_ui_has_no_duplicate_ids_or_missing_direct_js_selectors():
    for html_file, js_file in [('index.html', 'app.js'), ('portal.html', 'portal.js')]:
        html = view(html_file)
        assert not html.duplicates, f'{html_file}: duplicate IDs {html.duplicates}'
        js = (STATIC / js_file).read_text(encoding='utf-8')
        referenced = set(re.findall(r'\$\(\s*[\'\"]([a-zA-Z][\w-]*)[\'\"]\s*\)', js))
        assert not (referenced - set(html.ids)), f'{html_file}: missing {referenced - set(html.ids)}'


def test_local_first_screen_has_one_unhidden_primary_search():
    html = view('index.html')
    assert html.ids['runSearch'][1] == ()
    for action in ('runExport', 'runAllTopics', 'aiPlanRequest', 'queryBudget',
                   'quickMaxResults', 'quickSort', 'createTopic', 'renameTopic', 'deleteTopic'):
        assert html.ids[action][1], f'{action} should be in an on-demand disclosure'
    assert html.ids['topicKeywords'][1] == ()
    assert 'hidden' not in html.ids['progressText'][2]


def test_local_settings_are_grouped_and_closed_by_default():
    html = view('index.html')
    for key in ('settings-search', 'settings-ai', 'settings-sources', 'settings-pdf',
                'settings-update', 'settings-advanced'):
        assert html.ids[key][0] == 'details'
        assert 'open' not in html.ids[key][2]
    for control in ('apiKey', 'saveSettings', 'sourceTimeoutSeconds'):
        # The group must be collapsible; controls remain in the document.
        if control in html.ids:
            assert html.ids[control][1], f'{control} unexpectedly visible on settings landing'


def test_local_library_reports_and_zotero_keep_advanced_actions():
    html = view('index.html')
    for control in ('exportAll', 'exportExperiments', 'generateSynthesis',
                    'downloadSelected', 'downloadStarred', 'zoteroDisable'):
        assert control in html.ids, control
        assert html.ids[control][1], f'{control} should be in a collapsed disclosure'
    for control in ('librarySearch', 'exportReports', 'zoteroPath'):
        assert control in html.ids


def test_public_workspace_has_direct_tasks_and_closed_advanced_info():
    html = view('portal.html')
    for control in ('quickStart', 'openReadingQueue', 'openEvidenceQueue', 'openTopicSettings',
                    'metricToRead', 'metricPendingEvidence'):
        assert control in html.ids
        assert not html.ids[control][1], f'{control} should be visible without extra clicks'
    assert html.ids['topicSetup'][0] == 'details'
    assert 'open' not in html.ids['topicSetup'][2]
    for control in ('oldPassword', 'newPassword', 'fetchPdf', 'cloudAnalysis', 'evidenceCsv'):
        assert html.ids[control][1], f'{control} should be contextual or on-demand'


def test_public_creating_and_editing_topic_do_not_show_two_forms():
    text = (STATIC / 'portal.html').read_text(encoding='utf-8')
    assert re.search(r'data-topic-mode="new"\s+hidden', text)
    assert 'data-topic-tab="edit"' in text and 'data-topic-tab="new"' in text
    js = (STATIC / 'portal.js').read_text(encoding='utf-8')
    assert 'selectTopicMode' in js
    assert 'openTopicSettings' in js


def test_model_cost_warning_and_research_review_controls_remain_available():
    local = (STATIC / 'index.html').read_text('utf-8')
    public = (STATIC / 'portal.html').read_text('utf-8')
    assert '会将输入的研究目标发送' in local
    assert ('云模型可能产生费用' in public or '模型分析可能计费' in public)
    for key in ('fulltextArea', 'importEvidence', 'reportDownload', 'retrySafeJob'):
        assert key in view('portal.html').ids


def test_desktop_manager_retains_process_controls_and_hides_logs():
    source = (ROOT / 'paper_radar' / 'desktop_manager.py').read_text('utf-8')
    for expected in ('controller.start()', 'controller.stop()', 'controller.open_browser()',
                     'ttk.Progressbar(', '查看运行日志', '查看数据位置', 'root.geometry(\'680x365\')',
                     'root.geometry(\'680x610\')'):
        assert expected in source
    assert 'root.after(300,do_start)' in source
    assert 'root.after(500,tick)' in source


def test_shortcut_to_publisher_credentials_reveals_collapsed_settings():
    # A jump to a collapsed element has no visible effect unless its parent opens.
    js = (STATIC / 'app.js').read_text('utf-8')
    assert "$('settings-sources').open=true" in js
    assert "$('publisherPanel').scrollIntoView" in js
    assert '点击“开始检索”' in js and '检索论文并更新报告' not in js
