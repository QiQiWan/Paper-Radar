"""Integrated release checks for discovery authorization, report and offline UI."""
# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
from __future__ import annotations
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest
from urllib.error import HTTPError, URLError

from docx import Document
from paper_radar.core import DEFAULTS, validate_config, make_demo
from paper_radar.models import Paper
from paper_radar.reports.text import write_markdown
from paper_radar.reports.word import write_report
from paper_radar.sources.sciencedirect import ScienceDirectSource
from paper_radar.sources.credentials import credential_state
from paper_radar.cloud_api import PRESETS


class Response(BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())


class UnifiedAcceptance(unittest.TestCase):
    def test_sciencedirect_credentials_required(self):
        with patch('paper_radar.sources.sciencedirect.get_credential', return_value=''):
            with self.assertRaisesRegex(RuntimeError, 'API Key'):
                ScienceDirectSource().fetch(DEFAULTS)

    def test_sciencedirect_entitled_metadata_without_pdf(self):
        response={'search-results':{'entry':[{'eid':'2-s2.0-999','dc:title':'Neural operator designs',
                   'prism:doi':'10.1016/j.mock.2026.01.001','prism:coverDate':'2026-11-02',
                   'prism:publicationName':'Test Journal','openaccess':'1'}]}}
        with patch('paper_radar.sources.sciencedirect.get_credential', side_effect=lambda *args:'test-key' if len(args)==1 else 'test-token'):
            with patch('paper_radar.sources.sciencedirect.request.urlopen', return_value=Response(response)) as sent:
                papers=ScienceDirectSource().fetch(DEFAULTS, now=datetime(2026,11,3,tzinfo=timezone.utc))
        self.assertEqual(len(papers),1)
        self.assertEqual(papers[0].source,'sciencedirect')
        self.assertEqual(papers[0].doi,'10.1016/j.mock.2026.01.001')
        self.assertEqual(papers[0].download_url,'')
        self.assertIn('/content/search/sciencedirect',sent.call_args.args[0].full_url)
        self.assertEqual(sent.call_args.args[0].get_header('X-els-insttoken'),'test-token')

    def test_sciencedirect_does_not_leak_key_in_error(self):
        with patch('paper_radar.sources.sciencedirect.get_credential',return_value='private-secret'):
            with patch('paper_radar.sources.sciencedirect.request.urlopen', side_effect=HTTPError('https://x',403,'Forbidden',None,None)):
                with self.assertRaises(RuntimeError) as raised:
                    ScienceDirectSource().fetch(DEFAULTS)
        self.assertIn('HTTP 403',str(raised.exception))
        self.assertNotIn('private-secret',str(raised.exception))

    def test_springer_url_key_never_reaches_error_message(self):
        from paper_radar.sources.springer import SpringerSource
        with patch('paper_radar.sources.springer.get_credential',return_value='private-secret'):
            with patch('paper_radar.sources.springer.request.urlopen',side_effect=URLError('https://api.springernature.com?api_key=private-secret')):
                with self.assertRaises(RuntimeError) as raised:
                    SpringerSource().fetch(DEFAULTS)
        self.assertNotIn('private-secret',str(raised.exception))

    def test_credential_alias_for_sciencedirect(self):
        with patch('paper_radar.sources.credentials.get_credential', side_effect=lambda name,field='key': name == 'elsevier'):
            state=credential_state()
        self.assertEqual(state['sciencedirect'],state['elsevier'])
        self.assertTrue(state['sciencedirect']['key'])

    def test_reports_contain_intro_and_copyright_and_active_sources(self):
        papers=[Paper(paper_id='mock',title='Neural operator for generalization',abstract='This work discusses robust transfer across meshes and benchmark evaluation.',
                      authors='A. B.',published='2026-11-02',updated='2026-11-02',category='cs.LG',url='https://doi.org/10.1234/ab',pdf_url='',
                      source='crossref',doi='10.1234/ab',venue='Test Journal',score=6)]
        cfg={**DEFAULTS,'sources':['crossref','sciencedirect']}
        with TemporaryDirectory() as tmp:
            folder=Path(tmp)
            md=write_markdown(folder/'recent.md',papers,cfg)
            report=write_report(folder/'recent.docx','近期文献汇总',papers,cfg,1)
            content=md.read_text(encoding='utf-8')
            doc=Document(report)
            full=' '.join(p.text for p in doc.paragraphs)
            self.assertIn('本期研究综述',content)
            self.assertLess(content.index('本期研究综述'),content.index('优先阅读'))
            self.assertIn('跨场景泛化',content)
            self.assertIn('本期研究综述',full)
            self.assertIn('优先阅读',full)
            self.assertIn('sciencedirect',content)
            self.assertIn('@EatRice',' '.join(x.text for x in doc.sections[0].footer.paragraphs))

    def test_site_has_grouping_preview_and_advanced_search(self):
        root=Path(__file__).resolve().parent.parent
        index=(root/'paper_radar/static/index.html').read_text(encoding='utf-8')
        script=(root/'paper_radar/static/app.js').read_text(encoding='utf-8')
        for fragment in ('value="year"','value="initial"','id="providerConfigured"','id="taskProgressBar"',
                         'id="markdownDialog"','id="publisherPanel"','id="quickMaxResults"'):
            self.assertIn(fragment,index)
        self.assertIn("limit:'30'",script)
        self.assertIn('renderLibraryGroups',script)
        self.assertIn('api/provider-catalog',script)
        self.assertIn('DeepSeek',PRESETS)

if __name__=='__main__':unittest.main()
