# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Regression checks for changes in research topic and updated preprint abstracts."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from docx import Document
from paper_radar.core import (DEFAULTS, Paper, connect, get_recent, rank,
                              run_cycle, report_from_cache, save_papers)

FIXED_NOW = datetime(2026, 11, 3, 9, tzinfo=timezone.utc)


def paper(paper_id, title, abstract):
    return Paper(paper_id=paper_id, title=title, abstract=abstract,
                 authors='Alice Example', published='2026-11-03T01:00:00Z',
                 updated='2026-11-03T01:00:00Z', category='cs.LG',
                 url=f'https://arxiv.org/abs/{paper_id}', pdf_url='')


class RegressionTests(unittest.TestCase):
    def test_topic_change_excludes_unrelated_cached_items(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / 'papers.db'
            with connect(db) as conn:
                a = paper('2611.01101', 'Neural operator test', 'A neural operator model.')
                b = paper('2611.01102', 'Battery thermal management', 'A battery thermal model.')
                a.score, b.score = rank(a, ['neural operator']), rank(b, ['neural operator'])
                save_papers(conn, [a,b])
                picked = get_recent(conn, 7, 20, keywords=['battery thermal'], now=FIXED_NOW)
            self.assertEqual([p.paper_id for p in picked], ['2611.01102'])

    def test_abstract_revision_invalidates_previous_summary(self):
        with TemporaryDirectory() as temp:
            with connect(Path(temp) / 'papers.db') as conn:
                item = paper('2611.01103', 'Neural operator', 'First abstract.')
                save_papers(conn, [item])
                conn.execute("UPDATE papers SET summary='旧版摘要' WHERE paper_id=?", (item.paper_id,))
                changed = paper('2611.01103', 'Neural operator', 'Revised abstract.')
                updated = save_papers(conn, [changed])
                row = conn.execute('SELECT summary, abstract FROM papers WHERE paper_id=?', (item.paper_id,)).fetchone()
                self.assertEqual(len(updated), 0)
                self.assertEqual(row['summary'], '')
                self.assertEqual(row['abstract'], 'Revised abstract.')

    def test_empty_cache_does_not_resurface_previous_topic(self):
        with TemporaryDirectory() as temp:
            cfg = dict(DEFAULTS, keywords=['new topic'], output_dir=str(Path(temp)/'reports'))
            path = report_from_cache(cfg, db_path=Path(temp)/'papers.db')
            doc = Document(path)
            self.assertIn('暂无符合条件', '\n'.join(p.text for p in doc.paragraphs))

    def test_new_report_remains_a_normal_editable_docx(self):
        with TemporaryDirectory() as temp:
            cfg = dict(DEFAULTS, output_dir=str(Path(temp)/'reports'))
            res = run_cycle(cfg, db_path=Path(temp)/'papers.db', now=FIXED_NOW,
                            fetcher=lambda c,logger,now: [paper('2611.01104', 'Neural operator model', 'An operator learning method.')])
            doc = Document(res['weekly'])
            self.assertGreater(len(doc.paragraphs), 3)
            self.assertEqual(res['new'], 1)
            self.assertIn('Neural operator model', '\n'.join(p.text for p in doc.paragraphs))


if __name__ == '__main__':
    unittest.main()
