# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.5 regression suite: paging, status, export, download repair, UI elements."""
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
import time
import unittest

from paper_radar.core import DEFAULTS, connect, list_library, count_library, save_papers
from paper_radar.models import Paper
from paper_radar import webapp


def fixture(n=12):
    return [Paper(paper_id=f'v15-{i:04d}', title=f'Neural operator paper {i:04d}',
                  abstract='Scientific machine learning with operators', authors=f'Author {i}',
                  published=f'2026-11-03T09:{i%60:02d}:00Z',
                  updated='2026-11-03T00:00:00Z',category='cs.LG',
                  url=f'https://arxiv.org/abs/{i}', pdf_url='')
               for i in range(n)]


class TestV15(unittest.TestCase):
    def test_library_pages_do_not_overlap(self):
        with TemporaryDirectory() as td:
            with connect(Path(td)/'papers.db') as db:
                save_papers(db, fixture(101))
                self.assertEqual(count_library(db), 101)
                first = list_library(db, limit=40)
                second = list_library(db, limit=40, offset=40)
                last = list_library(db, limit=40, offset=80)
                self.assertEqual([len(first),len(second),len(last)], [40,40,21])
                self.assertEqual(len({p.paper_id for p in first+second+last}),101)

    def test_filter_counts_and_literal_wildcards(self):
        with TemporaryDirectory() as td:
            with connect(Path(td)/'papers.db') as db:
                papers=fixture(4)
                papers[0].title = 'Study A_0'
                papers[1].title = 'Study ABC0'
                papers[2].title = 'Percentage 25% correction'
                save_papers(db, papers)
                self.assertEqual(count_library(db, query='A_0'),1)
                self.assertEqual(count_library(db, query='25%'),1)
                self.assertEqual(count_library(db, query='operator'),4)
                db.execute('UPDATE papers SET is_starred=1 WHERE paper_id=?',(papers[0].paper_id,))
                self.assertEqual(count_library(db, only_starred=True),1)
                self.assertEqual(list_library(db, query='A_0')[0].title,'Study A_0')

    def test_recent_sort_does_not_prioritize_starred(self):
        with TemporaryDirectory() as td:
            with connect(Path(td)/'papers.db') as db:
                save_papers(db,fixture(2))
                db.execute("UPDATE papers SET first_seen='2026-10-01T00:00:00', is_starred=1 WHERE paper_id='v15-0000'")
                db.execute("UPDATE papers SET first_seen='2026-11-03T00:00:00' WHERE paper_id='v15-0001'")
                self.assertEqual(list_library(db,sort='recent',limit=1)[0].paper_id,'v15-0001')

    def test_http_paged_and_filtered_download_api(self):
        with TemporaryDirectory() as td:
            dbpath=Path(td)/'papers.db'
            with connect(dbpath) as db:
                save_papers(db,fixture(52))
                db.execute("UPDATE papers SET download_status='failed',download_error='timeout',download_url='https://example.org/a.pdf' WHERE paper_id='v15-0001'")
                db.execute("UPDATE papers SET download_status='downloaded' WHERE paper_id='v15-0002'")
                db.commit()
            @contextmanager
            def connection():
                with connect(dbpath) as db: yield db
            server=ThreadingHTTPServer(('127.0.0.1',0), webapp.Handler);server.token='local-test'
            thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}'
            try:
                with patch.object(webapp,'open_database', connection):
                    result=json.load(urlopen(url+'/api/papers?limit=20&offset=40'))
                    self.assertEqual(result['total'],52)
                    self.assertEqual(len(result['papers']),12)
                    self.assertFalse(result['has_more'])
                    self.assertEqual(result['offset'],40)
                    failed=json.load(urlopen(url+'/api/downloads?status=failed'))
                    self.assertEqual([x['paper_id'] for x in failed['items']],['v15-0001'])
                    self.assertTrue(failed['items'][0]['download_url'])
                    all_status=json.load(urlopen(url+'/api/downloads'))
                    self.assertEqual(len(all_status['items']),2)
            finally:
                server.shutdown();server.server_close();thread.join(2)

    def test_full_index_export_does_not_truncate_at_report_limit(self):
        with TemporaryDirectory() as td:
            folder=Path(td);dbpath=folder/'papers.db'
            with connect(dbpath) as db:save_papers(db,fixture(107))
            cfg=dict(DEFAULTS,output_dir=str(folder/'reports'),report_limit=5)
            @contextmanager
            def connection():
                with connect(dbpath) as db:yield db
            engine=webapp.Runtime()
            with patch.object(webapp,'open_database',connection), \
                 patch.object(webapp,'read_config',lambda:cfg), \
                 patch.object(webapp,'LOCK_FILE',folder/'running.lock'), \
                 patch.object(webapp,'write_status',lambda x:None):
                engine._worker('export_all',[])
            output=folder/'reports'/'全部文献索引.csv'
            self.assertTrue(output.is_file())
            self.assertEqual(len(output.read_text('utf-8-sig').splitlines()),108)
            self.assertEqual(engine.last_result['total'],107)

    def test_scheduler_status_is_async_for_browser(self):
        engine=webapp.Runtime()
        def slow():
            time.sleep(.18)
            return {'installed':True,'next_run':'tomorrow'}
        with patch.object(webapp.scheduler,'status',slow):
            start=time.monotonic();first=engine.schedule();elapsed=time.monotonic()-start
            self.assertEqual(first['state'],'checking')
            self.assertLess(elapsed,.12)
            limit=time.monotonic()+2
            while time.monotonic()<limit and not engine.scheduler_cache:
                time.sleep(.015)
            self.assertTrue(engine.schedule()['installed'])

    def test_host_header_rejects_external_name(self):
        from urllib.request import Request
        from urllib.error import HTTPError
        server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler);server.token='host-test'
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            target=f'http://127.0.0.1:{server.server_port}/api/health'
            request=Request(target,headers={'Host':'attacker.example'})
            with self.assertRaises(HTTPError) as ctx:urlopen(request)
            self.assertEqual(ctx.exception.code,403)
            self.assertEqual(json.load(urlopen(target))['version'],'1.9.0')
        finally:
            server.shutdown();server.server_close();thread.join(2)

    def test_partial_source_failure_is_marked_in_report_metadata(self):
        from paper_radar.sources import base
        from paper_radar.reports.text import write_markdown
        class Success:
            label='Local success'
            def fetch(self,config,logger=print,now=None):return fixture(1)
        class Failure:
            label='Local failure'
            def fetch(self,config,logger=print,now=None):raise RuntimeError('offline')
        with patch.dict(base._REGISTRY,{'good':Success,'bad':Failure}):
            cfg={'sources':['good','bad'],'keywords':['neural operator'],'lookback_days':7}
            records=base.fetch_enabled(cfg,logger=lambda s:None)
            self.assertEqual(len(records),1)
            self.assertEqual([x['ok'] for x in cfg['_source_results']],[True,False])
            with TemporaryDirectory() as td:
                report=write_markdown(Path(td)/'report.md',records,cfg).read_text('utf-8')
                self.assertIn('good 完成 1 篇',report)
                self.assertIn('bad 失败',report)

    def test_frontend_controls_exist_and_are_wired(self):
        path=Path(webapp.__file__).parent/'static'
        html=(path/'index.html').read_text('utf-8')
        js=(path/'app.js').read_text('utf-8')
        for ident in ('libraryMore','libraryPagination','libraryStatus','downloadFilter','exportAll','firstRunGuide'):
            self.assertIn(f'id="{ident}"',html)
        for route in ('/api/papers?','/api/downloads?status=','export_all'):
            self.assertIn(route,js)
        self.assertIn('Paper-Radar v1.9.0',html)

if __name__=='__main__':unittest.main()
