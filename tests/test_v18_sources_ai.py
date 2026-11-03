"""Paper-Radar v1.8: publisher entitlements, provider usage and UI contracts.
Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""
from __future__ import annotations
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from paper_radar.core import DEFAULTS, validate_config, connect, list_library
from paper_radar.sources import available_sources
from paper_radar.sources.crossref import CrossrefSource, parse_item
from paper_radar.sources.springer import SpringerSource, parse_record
from paper_radar.sources.elsevier import ElsevierSource, parse_entry
from paper_radar.sources.credentials import get_credential, save_credential
from paper_radar.cloud_api import PRESETS, chat_completion, get_balance
from paper_radar.provider_usage import record_usage, read_usage
from paper_radar.models import Paper

class JsonReply(BytesIO):
    def __init__(self, obj):
        super().__init__(json.dumps(obj).encode())

class SourceAndUsageTests(unittest.TestCase):
    def test_defaults_are_small_and_publishers_opt_in(self):
        self.assertEqual(DEFAULTS['max_results'], 30)
        self.assertEqual(DEFAULTS['report_limit'], 30)
        self.assertNotIn('springer', DEFAULTS['sources'])
        self.assertNotIn('elsevier', DEFAULTS['sources'])

    def test_sources_explain_access_and_link_to_official_docs(self):
        sources={s['name']:s for s in available_sources()}
        self.assertEqual(sources['arxiv']['access'],'public')
        self.assertEqual(sources['crossref']['access'],'public')
        self.assertEqual(sources['springer']['access'],'api_key')
        self.assertEqual(sources['elsevier']['access'],'institutional')
        self.assertTrue(sources['elsevier']['guide_url'].startswith('https://'))

    def test_crossref_parser(self):
        p=parse_item({'DOI':'10.1234/TEST', 'title':['Neural operator for meshes'],
                      'published':{'date-parts':[[2026,11,1]]},'container-title':['Mock Journal'],
                      'author':[{'given':'A','family':'B'}]})
        self.assertEqual(p.doi,'10.1234/test')
        self.assertEqual(p.source,'crossref')
        self.assertEqual(p.venue,'Mock Journal')
        self.assertEqual(p.published,'2026-11-01')
        self.assertEqual(p.download_url,'')

    def test_crossref_uses_public_metadata_only(self):
        data={'message':{'items':[{'DOI':'10.1234/A','title':['Operator learning'], 'published':{'date-parts':[[2026,11,2]]}}]}}
        with patch('paper_radar.sources.crossref.request.urlopen',return_value=JsonReply(data)) as opened:
            papers=CrossrefSource().fetch({**DEFAULTS,'keywords':['operator learning']},now=datetime(2026,11,3,tzinfo=timezone.utc))
        self.assertEqual(len(papers),1)
        self.assertNotIn('api_key=',opened.call_args.args[0].full_url)

    def test_springer_requires_key(self):
        with patch('paper_radar.sources.springer.get_credential',return_value=''):
            with self.assertRaisesRegex(RuntimeError,'API Key'):
                SpringerSource().fetch(DEFAULTS)

    def test_springer_works_with_entitled_key_and_no_licensed_pdf(self):
        resp={'records':[{'title':'Complex geometries','doi':'10.1007/123',
                          'publicationDate':'2026-11-02','abstract':'About mesh','creators':[{'creator':'Q'}]}]}
        with patch('paper_radar.sources.springer.get_credential',return_value='mock-key'):
            with patch('paper_radar.sources.springer.request.urlopen',return_value=JsonReply(resp)) as opened:
                papers=SpringerSource().fetch(DEFAULTS,now=datetime(2026,11,3,tzinfo=timezone.utc))
        self.assertEqual(papers[0].source,'springer')
        self.assertEqual(papers[0].download_url,'')
        self.assertIn('api_key=mock-key',opened.call_args.args[0].full_url)

    def test_elsevier_requires_key(self):
        with patch('paper_radar.sources.elsevier.get_credential',return_value=''):
            with self.assertRaisesRegex(RuntimeError,'API Key'):
                ElsevierSource().fetch(DEFAULTS)

    def test_elsevier_insttoken_header_and_no_subscription_download(self):
        data={'search-results':{'entry':[{'eid':'2-s2.0-123','dc:title':'Test',
                     'prism:coverDate':'2026-11-02','prism:doi':'10.1016/test'}]}}
        with patch('paper_radar.sources.elsevier.get_credential',side_effect=lambda name,field='key': 'test-key' if field=='key' else 'insttoken'):
            with patch('paper_radar.sources.elsevier.request.urlopen',return_value=JsonReply(data)) as opened:
                papers=ElsevierSource().fetch(DEFAULTS,now=datetime(2026,11,3,tzinfo=timezone.utc))
        self.assertEqual(papers[0].source,'elsevier')
        self.assertEqual(papers[0].download_url,'')
        self.assertEqual(opened.call_args.args[0].get_header('X-els-insttoken'),'insttoken')

    def test_ai_presets(self):
        for name in ('DeepSeek','火山引擎豆包','Claude (Anthropic)','OpenAI / ChatGPT API','SiliconFlow','Google Gemini'):
            self.assertIn(name,PRESETS)

    def test_claude_native_messages_and_usage(self):
        cfg={'api_provider':'Claude (Anthropic)','api_model':'test-model','api_base_url':'https://api.anthropic.com/v1'}
        with patch('paper_radar.cloud_api.request.urlopen',return_value=JsonReply({
                'content':[{'type':'text','text':'中文简介'}],'usage':{'input_tokens':21,'output_tokens':8}})) as opened:
            with patch('paper_radar.cloud_api.record_usage') as acc:
                result=chat_completion(cfg,'mock title',key='sk-mock')
        self.assertEqual(result,'中文简介')
        self.assertEqual(opened.call_args.args[0].full_url,'https://api.anthropic.com/v1/messages')
        self.assertEqual(opened.call_args.args[0].get_header('X-api-key'),'sk-mock')
        acc.assert_called_once_with('Claude (Anthropic)',21,8)

    def test_balance_is_not_faked_for_other_providers(self):
        self.assertFalse(get_balance('火山引擎豆包')['available'])

    def test_local_usage_does_not_store_key_or_prompt(self):
        with TemporaryDirectory() as tmp:
            loc=Path(tmp)/'private.json'
            record_usage('DeepSeek',45,11,path=loc)
            record_usage('DeepSeek',2,3,path=loc)
            data=read_usage(loc)
            self.assertEqual(data['DeepSeek']['requests'],2)
            self.assertEqual(data['DeepSeek']['input_tokens'],47)
            self.assertNotIn('sk-',loc.read_text())

    def test_frontend_exposes_required_controls(self):
        root=Path(__file__).resolve().parent.parent
        html=(root/'paper_radar/static/index.html').read_text(encoding='utf-8')
        js=(root/'paper_radar/static/app.js').read_text(encoding='utf-8')
        for id in ('publisherPanel','quickMaxResults','libraryGroup','librarySort','markdownDialog',
                   'taskProgressBar','refreshProviderUsage','springerKey','elsevierKey','zoteroConnect'):
            self.assertIn(f'id="{id}"',html)
        for route in ('/api/source-credential','/api/provider-usage','/api/report-preview'):
            self.assertIn(route,js if route != '/api/report-preview' else js)
        self.assertIn('@EatRice',html)

if __name__=='__main__':unittest.main()
