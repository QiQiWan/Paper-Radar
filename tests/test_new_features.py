# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""New release regression tests: legacy DB, scheduler script, exports and local API."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.request import urlopen, Request
from urllib.error import HTTPError
import json
import sqlite3
import unittest

from paper_radar.core import (DEFAULTS, connect, save_papers, Paper, list_library,
                              update_paper_flags, single_instance)
from paper_radar.report import write_report, write_markdown, write_csv
from paper_radar.scheduler import build_install_script
from paper_radar.webapp import Handler


def sample():
    return Paper('2611.12345', 'Neural Operator for Engineering',
                 'Neural operator simulation paper with numerical methods.',
                 'A. Example', '2026-11-03T00:00:00Z', '2026-11-03T00:00:00Z', 'cs.LG',
                 'https://arxiv.org/abs/2611.12345','https://arxiv.org/pdf/2611.12345',score=5.5)

class FeatureTests(unittest.TestCase):
    def test_scheduler_install_script_quotes_paths(self):
        script=build_install_script('08:30',r"C:\User's folder\pythonw.exe",r"D:\Work's files\main.py")
        self.assertIn("User''s folder",script)
        self.assertIn('StartWhenAvailable',script)
        self.assertIn('MultipleInstances IgnoreNew',script)
        self.assertIn('WorkingDirectory',script)
        with self.assertRaises(ValueError):
            build_install_script('25:90','python','main.py')

    def test_legacy_db_migration_and_reading_flags(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'old.db'
            db=sqlite3.connect(path)
            db.execute("""CREATE TABLE papers (
                paper_id TEXT PRIMARY KEY, title TEXT NOT NULL, abstract TEXT NOT NULL,
                authors TEXT, published TEXT, updated TEXT, category TEXT, url TEXT,
                pdf_url TEXT, score REAL NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '',
                first_seen TEXT NOT NULL)""")
            db.commit();db.close()
            with connect(path) as c:
                save_papers(c,[sample()])
                self.assertTrue(update_paper_flags(c,'2611.12345',starred=True,status='reading'))
                row=list_library(c,only_starred=True)[0]
                self.assertEqual(row.reading_status,'reading')
                self.assertEqual(row.is_starred,1)
                self.assertFalse(update_paper_flags(c,'nonexistent',starred=True))

    def test_lock_prevents_overlap(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'running.lock'
            with single_instance(path):
                with self.assertRaisesRegex(RuntimeError,'在运行'):
                    with single_instance(path):pass
            with single_instance(path):pass

    def test_multi_format_report(self):
        with TemporaryDirectory() as temp:
            folder=Path(temp);papers=[sample()];cfg=dict(DEFAULTS)
            doc=write_report(folder/'report.docx','近期文献汇总',papers,cfg,1)
            md=write_markdown(folder/'report.md',papers,cfg)
            csv=write_csv(folder/'report.csv',papers)
            self.assertTrue(doc.exists())
            self.assertIn('优先阅读',md.read_text('utf-8'))
            self.assertIn('论文详目','\n'.join(p.text for p in __import__('docx').Document(doc).paragraphs))
            self.assertTrue(csv.read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_csv_untrusted_title_does_not_execute_as_excel_formula(self):
        import csv
        with TemporaryDirectory() as temp:
            p=sample()
            p.title='=HYPERLINK("https://example.org", "Open")'
            target=write_csv(Path(temp)/'records.csv',[p])
            with target.open(encoding='utf-8-sig', newline='') as f:
                rows=list(csv.reader(f))
            self.assertTrue(rows[1][1].startswith("'="))

    def test_status_journal_survives_reload(self):
        from paper_radar.run_status import write_status,read_status
        with TemporaryDirectory() as temp:
            path=Path(temp)/'status.json'
            write_status({'state':'success','result':{'new':4}},path)
            state=read_status(path)
            self.assertEqual(state['result']['new'],4)
            path.write_text('{bad json',encoding='utf-8')
            self.assertEqual(read_status(path)['state'],'never')

    def test_http_local_token_and_security(self):
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        httpd.token='test-local-token'
        th=Thread(target=httpd.serve_forever,daemon=True);th.start()
        url=f'http://127.0.0.1:{httpd.server_port}'
        try:
            info=json.loads(urlopen(url+'/api/health').read())
            self.assertEqual(info['app'],'paper-radar')
            html=urlopen(url+'/').read().decode('utf-8')
            self.assertIn('test-local-token',html)
            def post(body,extra=None):
                headers={'Content-Type':'application/json','X-Local-Token':'test-local-token'}
                headers.update(extra or {})
                return Request(url+'/api/job',data=json.dumps(body).encode(),headers=headers,method='POST')
            with self.assertRaises(HTTPError) as ctx:
                urlopen(post({'mode':'invalid'}, {'Origin':'https://other.example'}))
            self.assertEqual(ctx.exception.code,403)
            with self.assertRaises(HTTPError) as ctx:
                urlopen(post({'mode':'invalid'}))
            self.assertEqual(ctx.exception.code,400)
            with self.assertRaises(HTTPError) as ctx:
                urlopen(url+'/download/..%2Fsettings.json')
            self.assertEqual(ctx.exception.code,400)
        finally:
            httpd.shutdown();httpd.server_close();th.join(timeout=2)

if __name__ == '__main__':unittest.main()
