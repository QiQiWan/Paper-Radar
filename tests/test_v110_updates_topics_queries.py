# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.10: Release checking, legacy topic migration, fair source retrieval."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
import json
import time
import unittest

from paper_radar.core import DEFAULTS, get_output_dir, read_config, save_config
from paper_radar.topics import topic_list, manage_topic
from paper_radar.updates import UpdateChecker, fetch_latest, version_tuple, API_URL
from paper_radar.sources import base
from paper_radar.models import Paper


class ReleaseMetadataTests(unittest.TestCase):
    def test_semver_order_is_numeric_and_strict(self):
        self.assertGreater(version_tuple('v1.10.0'), version_tuple('v1.9.99'))
        self.assertIsNone(version_tuple('1.10.0-rc1'))
        self.assertIsNone(version_tuple('v01.10.0'))
        self.assertIsNone(version_tuple('fake-version'))

    def fake_response(self, tag='v1.11.1', link=None):
        return BytesIO(json.dumps({'tag_name':tag,'draft':False,'prerelease':False,
                                   'body':'更新日志：测试内容',
                                   'html_url':link or 'https://github.com/QiQiWan/Paper-Radar/releases/tag/'+tag,
                                   'published_at':'2026-11-04T00:00:00Z'}).encode('utf-8'))

    def test_latest_public_release_detected(self):
        with patch('paper_radar.updates.__version__','1.11.0'), patch('paper_radar.updates.request.urlopen',return_value=self.fake_response()) as opened:
            result=fetch_latest()
        self.assertEqual(result['state'],'available')
        self.assertEqual(result['latest_version'],'1.11.1')
        self.assertEqual(opened.call_args.args[0].full_url,API_URL)
        self.assertEqual(opened.call_args.kwargs['timeout'],8)

    def test_older_release_does_not_trigger_downgrade(self):
        with patch('paper_radar.updates.request.urlopen',return_value=self.fake_response(tag='v1.9.9')):
            self.assertEqual(fetch_latest()['state'],'up_to_date')

    def test_no_release_is_not_called_up_to_date(self):
        err=HTTPError(API_URL,404,'not found',None,None)
        with patch('paper_radar.updates.request.urlopen',side_effect=err):
            self.assertEqual(fetch_latest()['state'],'unpublished')

    def test_reject_non_official_release_redirect(self):
        url='https://example.org/download/file.exe'
        with patch('paper_radar.updates.request.urlopen',return_value=self.fake_response(link=url)):
            with self.assertRaisesRegex(RuntimeError,'链接'):
                fetch_latest()

    def test_persisted_cooldown_and_manual_override(self):
        with TemporaryDirectory() as td:
            loc=Path(td)/'update_check.json'
            now=[datetime(2026,11,4,tzinfo=timezone.utc)]
            calls=[]
            def fake():
                calls.append('called')
                return {'state':'available','tag_name':'v1.12.1', 'latest_version':'1.12.1',
                        'release_url':'https://github.com/QiQiWan/Paper-Radar/releases/tag/v1.10.1'}
            cfg={**DEFAULTS,'update_check_enabled':True,'update_check_interval_hours':24}
            checker=UpdateChecker(loc, fetcher=fake, now=lambda:now[0])
            self.assertTrue(checker.schedule(cfg))
            for _ in range(100):
                if not checker.status(cfg)['checking']:break
                time.sleep(.01)
            self.assertEqual(checker.status(cfg)['state'],'available')
            self.assertTrue(loc.exists())
            new_checker=UpdateChecker(loc,fetcher=fake, now=lambda:now[0])
            self.assertFalse(new_checker.schedule(cfg))
            self.assertEqual(len(calls),1)
            self.assertTrue(new_checker.status({**cfg,'dismissed_update_tag':'v1.12.1'})['dismissed'])
            now[0]+=timedelta(hours=25)
            self.assertTrue(new_checker.schedule(cfg))
            for _ in range(100):
                if not new_checker.status(cfg)['checking']:break
                time.sleep(.01)
            self.assertEqual(len(calls),2)
            self.assertFalse(new_checker.schedule({**cfg,'update_check_enabled':False}))
            self.assertTrue(new_checker.schedule({**cfg,'update_check_enabled':False},force=True))
            for _ in range(150):
                if not new_checker.status(cfg)['checking']:break
                time.sleep(.01)
            self.assertFalse(new_checker.status(cfg)['checking'])

    def test_cached_update_rechecked_after_software_upgrade(self):
        """Avoid false 'new release' banners after keeping old data/ on upgrade."""
        with TemporaryDirectory() as td:
            loc=Path(td)/'status.json'
            loc.write_text(json.dumps({'state':'available', 'tag_name':'v1.10.0',
                                       'latest_version':'1.10.0',
                                       'release_url':'https://github.com/QiQiWan/Paper-Radar/releases/tag/v1.10.0'}))
            checker=UpdateChecker(loc)
            self.assertEqual(checker.status({**DEFAULTS})['state'],'up_to_date')
            with patch('paper_radar.updates.__version__','1.9.1'):
                self.assertEqual(checker.status({**DEFAULTS})['state'],'available')

    def test_invalid_cached_link_not_exposed(self):
        with TemporaryDirectory() as td:
            loc=Path(td)/'status.json'
            loc.write_text(json.dumps({'state':'available', 'tag_name':'v9.9.9',
                                       'latest_version':'9.9.9',
                                       'release_url':'http://malicious.example/version'}))
            checker=UpdateChecker(loc)
            self.assertNotIn('release_url', checker.status({**DEFAULTS}))

    def test_offline_preserves_known_release_and_returns_quickly(self):
        with TemporaryDirectory() as td:
            loc=Path(td)/'state.json'
            loc.write_text(json.dumps({'state':'available','tag_name':'v1.12.2',
                                       'latest_version':'1.12.2','checked_at':'2026-11-03T00:00:00+00:00'}))
            checker=UpdateChecker(loc,fetcher=lambda: (_ for _ in ()).throw(RuntimeError('offline')))
            cfg={**DEFAULTS,'update_check_enabled':True}
            self.assertTrue(checker.schedule(cfg,force=True))
            for _ in range(100):
                if not checker.status(cfg)['checking']:break
                time.sleep(.01)
            self.assertEqual(checker.status(cfg)['state'],'available')
            self.assertIn('offline',checker.status(cfg)['message'])


class ResearchTopicTests(unittest.TestCase):
    def test_legacy_flat_config_remains_default_topic(self):
        with TemporaryDirectory() as td:
            settings=Path(td)/'settings.json'
            settings.write_text(json.dumps({'keywords':['old neural operator'], 'max_results':17}))
            cfg=read_config(settings)
            self.assertEqual(cfg['active_topic_id'],'default')
            self.assertEqual(cfg['topics'][0]['keywords'],['old neural operator'])
            self.assertEqual(cfg['max_results'],17)
            self.assertEqual(get_output_dir({**cfg,'output_dir':str(Path(td)/'reports')}), Path(td)/'reports')

    def test_create_switch_and_no_cross_topic_overwrite(self):
        with TemporaryDirectory() as td:
            settings=Path(td)/'settings.json'
            save_config({**DEFAULTS,'keywords':['geometry neural operator'],
                         'output_dir':str(Path(td)/'reports')},settings)
            created=manage_topic('create',name='跨几何泛化',path=settings)
            tid=created['active_topic_id']
            self.assertNotEqual(tid,'default')
            self.assertEqual(len(created['topics']),2)
            edited=read_config(settings)
            self.assertFalse(edited['zotero_enabled'])
            edited['keywords']=['new topic keyword']
            save_config(edited, settings)
            self.assertEqual(get_output_dir(read_config(settings)), Path(td)/'reports'/'themes'/tid)
            manage_topic('switch',topic_id='default',path=settings)
            self.assertEqual(read_config(settings)['keywords'],['geometry neural operator'])
            manage_topic('switch',topic_id=tid,path=settings)
            self.assertEqual(read_config(settings)['keywords'],['new topic keyword'])
            manage_topic('rename',topic_id=tid,name='多网格研究',path=settings)
            self.assertEqual(topic_list(settings)['topics'][1]['name'],'多网格研究')
            manage_topic('delete',topic_id=tid,path=settings)
            self.assertEqual(read_config(settings)['active_topic_id'],'default')
            self.assertEqual(len(read_config(settings)['topics']),1)

    def test_forbid_duplicate_names_and_default_deletion(self):
        with TemporaryDirectory() as td:
            path=Path(td)/'settings.json'
            save_config(DEFAULTS,path)
            with self.assertRaisesRegex(ValueError,'默认'):
                manage_topic('delete',topic_id='default',path=path)
            with self.assertRaisesRegex(ValueError,'同名'):
                manage_topic('create',name='默认研究主题',path=path)


def sample(pid, doi=''):
    return Paper(pid, pid, 'operator learning test', 'A', '2026-11-01',
                 '2026-11-01','cs.LG','https://example.org/p/'+pid,'',doi=doi)


class MultiQueryTests(unittest.TestCase):
    def test_plan_uses_more_than_three_keywords(self):
        config={**DEFAULTS,'keywords':['alpha','beta','gamma','delta','epsilon','zeta'],
                'max_results':30,'max_queries_per_source':6}
        plan=base.query_plan(config,'crossref')
        self.assertEqual(len(plan),6)
        self.assertEqual(plan[-1]['keywords'],['zeta'])
        self.assertEqual(len(base.query_plan(config,'arxiv')),1)

    def test_balanced_recall_and_partial_failure(self):
        class Fake:
            name='fake'
            label='Fake indexed source'
            def fetch(self,config,logger=print,now=None):
                keyword=config['keywords'][0]
                if keyword=='broken':raise RuntimeError('test-only failure')
                return [sample(keyword+'-'+str(i)) for i in range(4)]
        config={'sources':['fake'],'keywords':['alpha','broken','gamma','delta'],
                'max_results':6,'max_queries_per_source':4}
        with patch.dict(base._REGISTRY,{'fake':Fake}), patch('paper_radar.sources.base.sleep'):
            result=base.fetch_enabled(config,logger=lambda *_:None)
        self.assertEqual(len(result),6)
        self.assertEqual([p.paper_id for p in result[:3]],['alpha-0','gamma-0','delta-0'])
        self.assertTrue(config['_source_results'][0]['partial'])
        self.assertEqual(len(config['_source_results'][0]['queries']),4)
        self.assertEqual(sum(bool(q['ok']) for q in config['_source_results'][0]['queries']),3)


if __name__=='__main__':
    unittest.main()
