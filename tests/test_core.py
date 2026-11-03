# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from docx import Document
from paper_radar.core import (DEFAULTS, build_query, parse_atom, Paper,
    rank, run_cycle, report_from_cache, save_config, read_config)

ATOM_SAMPLE = '''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2610.01234v2</id>
    <updated>2026-10-27T00:00:00Z</updated>
    <published>2026-10-26T00:00:00Z</published>
    <title>  Neural Operator for a Test Geometry </title>
    <summary> A test neural operator method for scientific simulation. </summary>
    <author><name>Alice Example</name></author>
    <author><name>Bob Example</name></author>
    <link title="pdf" href="https://arxiv.org/pdf/2610.01234" />
    <arxiv:primary_category term="cs.LG" />
  </entry>
</feed>'''.encode('utf-8')

class CoreTests(unittest.TestCase):
    def test_query_composes_documented_fields(self):
        c = dict(DEFAULTS, categories=['cs.LG'])
        q = build_query(c, now=datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc))
        self.assertIn('ti:"neural operator"', q)
        self.assertIn('abs:"neural operator"', q)
        self.assertIn('cat:cs.LG', q)
        self.assertIn('submittedDate:[202610211200 TO 202610281200]', q)

    def test_atom_metadata(self):
        items = parse_atom(ATOM_SAMPLE)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].paper_id, '2610.01234')
        self.assertEqual(items[0].category, 'cs.LG')
        self.assertEqual(items[0].authors, 'Alice Example, Bob Example')
        self.assertGreater(rank(items[0], ['neural operator']), 0)

    def test_end_to_end_offline_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            settings = dict(DEFAULTS)
            settings['output_dir'] = str(base / 'reports')
            settings['ai_enabled'] = False
            db = base / 'papers.sqlite3'
            def fake_fetch(config, logger, now=None):
                return parse_atom(ATOM_SAMPLE)
            first = run_cycle(settings, db_path=db, fetcher=fake_fetch,
                              now=datetime(2026, 10, 28, 12, tzinfo=timezone.utc))
            self.assertEqual(first['new'], 1)
            self.assertTrue(first['new_file'].exists())
            doc = Document(first['weekly'])
            paragraphs = '\n'.join(p.text for p in doc.paragraphs)
            self.assertIn('Neural Operator', paragraphs)
            self.assertIn('原始摘要', paragraphs)
            second = run_cycle(settings, db_path=db, fetcher=fake_fetch,
                               now=datetime(2026, 10, 28, 12, tzinfo=timezone.utc))
            self.assertEqual(second['new'], 0)
            self.assertIsNone(second['new_file'])
            docfile = report_from_cache(settings, db_path=db)
            self.assertTrue(docfile.exists())

    def test_errors_cannot_look_like_success(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            settings = dict(DEFAULTS)
            settings['output_dir'] = str(base / 'reports')
            def failure(config, logger, now=None):
                raise RuntimeError('Network unavailable')
            with self.assertRaisesRegex(RuntimeError, 'Network unavailable'):
                run_cycle(settings, db_path=base / 'database.sqlite3', fetcher=failure)
            self.assertFalse((base / 'reports' / '近期文献汇总.docx').exists())

    def test_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'settings.json'
            save_config(dict(DEFAULTS, keywords='neural operator, heat transfer'), path)
            c = read_config(path)
            self.assertEqual(c['keywords'], ['neural operator', 'heat transfer'])

if __name__ == '__main__':
    unittest.main()
