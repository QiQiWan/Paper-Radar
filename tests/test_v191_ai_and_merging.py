# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.9.1 regression tests: verified merge and optional AI search planner."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from paper_radar import ai_planner
from paper_radar.cloud_api import validate_summary_evidence, summary_fingerprint
from paper_radar.core import (DEFAULTS, connect, save_papers, annotate_papers,
                              normalize_doi, read_config, validate_config)
from paper_radar.models import Paper

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def item(pid='x', abstract='The paper evaluates a neural operator for PDE solutions.',
         doi='10.1000/abc', source='arxiv'):
    return Paper(paper_id=pid, title='Neural operator for complex geometries',
                 abstract=abstract,authors='Example Researcher',
                 published='2026-11-02',updated='2026-11-02',category='cs.LG',
                 url='https://arxiv.org/abs/1234.5678',pdf_url='',doi=doi,source=source)


class NewQualityTests(unittest.TestCase):
    def test_cross_source_short_abstract_does_not_destroy_original(self):
        rich = 'A full abstract discusses neural operators and geometries. ' * 12
        with TemporaryDirectory() as d:
            with connect(Path(d)/'x.sqlite3') as conn:
                first = item('arxiv:1', rich, 'HTTPS://DOI.ORG/10.1000/ABC')
                save_papers(conn, [first])
                conn.execute("UPDATE papers SET summary='earlier review',summary_fingerprint='abc' WHERE paper_id='arxiv:1'")
                other = item('openalex:W22', 'A short abstract.', 'doi:10.1000/abc', 'openalex')
                save_papers(conn, [other])
                rows = conn.execute('SELECT * FROM papers').fetchall()
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['abstract'],rich)
                self.assertEqual(rows[0]['summary'],'earlier review')
                self.assertEqual(rows[0]['summary_fingerprint'],'abc')
                self.assertEqual(rows[0]['doi'],'10.1000/abc')
                self.assertEqual(rows[0]['source'],'arxiv,openalex')

    def test_new_richer_evidence_invalidates_summary(self):
        with TemporaryDirectory() as d:
            with connect(Path(d)/'x.sqlite3') as conn:
                save_papers(conn,[item()])
                conn.execute("UPDATE papers SET summary='old',summary_fingerprint='abc',summary_model='old' WHERE paper_id='x'")
                save_papers(conn,[item('x', 'A longer and revised neural operator abstract with results and methods. '*5)])
                row=conn.execute('SELECT summary,summary_fingerprint,summary_model FROM papers').fetchone()
                self.assertEqual(tuple(row),('','',''))

    def test_revised_title_invalidates_cached_analysis(self):
        with TemporaryDirectory() as d:
            with connect(Path(d)/'x.sqlite3') as conn:
                old=item()
                old.title='Short title'
                save_papers(conn,[old])
                conn.execute("UPDATE papers SET summary='old cache',summary_fingerprint='old-key' WHERE paper_id='x'")
                updated=item()
                save_papers(conn,[updated])
                row=conn.execute('SELECT title,summary,summary_fingerprint FROM papers').fetchone()
                self.assertEqual(row['title'],updated.title)
                self.assertEqual(row['summary'],'')
                self.assertEqual(row['summary_fingerprint'],'')

    def test_partial_config_keeps_manual_zotero_mode(self):
        cfg=validate_config({**DEFAULTS,'zotero_refresh_keywords':False})
        self.assertFalse(cfg['zotero_refresh_keywords'])

    def test_summary_fingerprint_changes_with_model_and_text(self):
        p=item()
        cfg={**DEFAULTS,'api_model':'model-one'}
        a=summary_fingerprint(p,cfg)
        self.assertNotEqual(a,summary_fingerprint(p,{**cfg,'api_model':'model-two'}))
        self.assertNotEqual(a,summary_fingerprint(p,{**cfg,'api_base_url':'https://other.example/v1'}))
        p.abstract='New evidence.'
        self.assertNotEqual(a,summary_fingerprint(p,cfg))

    def test_unsourced_percentage_fails_evidence_check(self):
        p=item()
        summary='研究问题：问题。研究方法：方法。关键结果：精度提高了37%。局限与待核查：条件。'
        self.assertTrue(any(s.startswith('定量信息') for s in validate_summary_evidence(summary,p)))
        with TemporaryDirectory() as d:
            with connect(Path(d)/'x.sqlite3') as conn:
                save_papers(conn,[p])
                cfg={**DEFAULTS,'keywords':['neural operator'],'ai_enabled':True,'llm_data_consent':True,'ai_limit':1}
                with patch('paper_radar.cloud_api.get_api_key',return_value='test-key'):
                    with patch('paper_radar.cloud_api.summarize_paper',return_value=summary):
                        log=[]
                        n=annotate_papers(conn,cfg,logger=log.append,now=NOW)
                self.assertEqual(n,0)
                self.assertEqual(conn.execute('SELECT summary FROM papers').fetchone()[0],'')
                self.assertTrue(any('量化信息' in s for s in log))

    def test_ai_planner_accepts_structured_result_and_removes_broad_terms(self):
        obj={'topic':'复杂几何神经算子',
             'keywords':['numerical','neural operator','geometry generalization',
                         'multiphysics surrogate','operator learning','neural operator'],
             'notes':['建议与原文核对'], 'exclusions':['chatbot']}
        with patch.object(ai_planner,'chat_completion',return_value='```json\n'+json.dumps(obj)+'\n```') as mocked:
            out=ai_planner.plan_research('研究非规则网格及复杂几何上的神经算子泛化。',DEFAULTS)
        self.assertEqual(out['keywords'],['neural operator','geometry generalization','multiphysics surrogate','operator learning'])
        self.assertIn('只输出一个 JSON',mocked.call_args.kwargs['system_prompt'])
        self.assertIn('研究非规则网格',mocked.call_args.args[1])

    def test_ai_planner_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            ai_planner.clean_plan('{"keywords":["numerical", "model"]}')
        with self.assertRaises(ValueError):
            ai_planner.clean_plan('not json')
        with self.assertRaises(ValueError):
            ai_planner.plan_research('too short',DEFAULTS)

    def test_manual_zotero_refresh_defaults_off(self):
        self.assertFalse(DEFAULTS['zotero_refresh_keywords'])

    def test_config_rejects_invalid_keyword_shape(self):
        with self.assertRaises(ValueError):
            validate_config({**DEFAULTS,'keywords':{'untrusted':'values'}})

if __name__=='__main__':unittest.main()
