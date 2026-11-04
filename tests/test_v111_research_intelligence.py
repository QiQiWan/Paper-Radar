# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.11 regression: topic isolation, evidence-gated AI, batch, history, source."""
from __future__ import annotations
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib import error
import json
import sqlite3
import unittest

from paper_radar.models import Paper
from paper_radar.core import (DEFAULTS,connect,save_papers,get_recent,count_library,
                              list_library,update_paper_flags,get_selected,save_config,read_config)
from paper_radar.recommend import parse_rerank,rerank_papers
from paper_radar.batch import run_all_topics,topic_snapshots
from paper_radar.history import record_run,recent_runs
from paper_radar.sources.openalex_semantic import OpenAlexSemanticSource
from paper_radar.sources import available_sources

NOW=datetime(2026,11,4,tzinfo=timezone.utc)


def paper(pid='paper:1',doi='10.1234/neural',title='Neural operator for geometry generalization'):
    return Paper(paper_id=pid,title=title,
        abstract='We explore a neural operator for geometry generalization over meshes in computational science.',
        authors='A. Researcher',published='2026-11-03',updated='2026-11-03',
        category='computer science',url='https://example.org/p',pdf_url='',
        doi=doi,source='arxiv')


def config():
    return {**DEFAULTS,'keywords':['neural operator'],'research_goal':'Investigate geometry generalization for neural operators',
            'active_topic_id':'default','ai_rerank_enabled':True,'ai_rerank_limit':4}


class ThemeStorageTests(unittest.TestCase):
    def test_old_papers_migrate_once_and_preserve_legacy_flags(self):
        with TemporaryDirectory() as t:
            db=Path(t)/'papers.sqlite3'
            c=connect(db)
            save_papers(c,[paper()],topic_id='default')
            c.execute("UPDATE papers SET is_starred=1,reading_status='done' WHERE paper_id='paper:1'")
            c.execute("DELETE FROM app_meta WHERE key='topic_migrated_v111'")
            c.execute('DELETE FROM topic_papers')
            c.commit();c.close()
            c=connect(db)
            a=list_library(c,topic_id='default',topic_only=True)
            self.assertEqual(len(a),1)
            self.assertEqual(a[0].is_starred,1)
            self.assertEqual(a[0].reading_status,'done')
            c.close()
            c=connect(db)
            self.assertEqual(count_library(c,topic_id='default',topic_only=True),1)
            c.close()

    def test_old_topic_table_missing_new_columns_upgrade(self):
        with TemporaryDirectory() as t:
            p=Path(t)/'papers.sqlite3'
            c=connect(p)
            c.execute('DROP TABLE topic_papers')
            c.execute('''CREATE TABLE topic_papers (topic_id TEXT,paper_id TEXT,score REAL,
                  is_starred INTEGER,reading_status TEXT,feedback INTEGER,
                  recommend_reason TEXT,ai_relevance INTEGER,matched_at TEXT,
                  PRIMARY KEY(topic_id,paper_id))''')
            c.commit();c.close()
            c=connect(p)
            cols={row[1] for row in c.execute('PRAGMA table_info(topic_papers)')}
            self.assertIn('recommend_evidence',cols)
            self.assertIn('rerank_fingerprint',cols)
            c.close()

    def test_same_paper_has_independent_theme_flags_and_recommendation(self):
        with TemporaryDirectory() as t:
            c=connect(Path(t)/'papers.sqlite3')
            save_papers(c,[paper()],topic_id='default')
            save_papers(c,[paper(pid='alternative',doi='https://doi.org/10.1234/neural')],topic_id='my-research')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM papers').fetchone()[0],1)
            update_paper_flags(c,'paper:1',starred=True,status='reading',feedback=1,topic_id='default')
            update_paper_flags(c,'paper:1',starred=False,status='done',feedback=-1,topic_id='my-research')
            a=list_library(c,topic_id='default',topic_only=True)[0]
            b=list_library(c,topic_id='my-research',topic_only=True)[0]
            self.assertEqual((a.is_starred,a.reading_status,a.feedback),(1,'reading',1))
            self.assertEqual((b.is_starred,b.reading_status,b.feedback),(0,'done',-1))
            self.assertEqual(len(get_recent(c,days=7,now=NOW,keywords=['neural operator'],topic_id='default')),1)
            self.assertEqual(len(get_recent(c,days=7,now=NOW,keywords=['neural operator'],topic_id='my-research')),0)
            self.assertEqual(count_library(c,topic_id='default',only_starred=True,topic_only=True),1)
            self.assertEqual(count_library(c,topic_id='my-research',only_starred=True,topic_only=True),0)
            c.close()

    def test_feedback_positive_changes_sort_order_without_cross_theme_effect(self):
        with TemporaryDirectory() as t:
            c=connect(Path(t)/'papers.sqlite3')
            items=[paper('low',doi='10.1234/a'),paper('high',doi='10.1234/b')]
            for p in items:p.score=2
            save_papers(c,items,topic_id='default')
            save_papers(c,items,topic_id='second')
            update_paper_flags(c,'low',topic_id='default',feedback=1)
            self.assertEqual(list_library(c,topic_id='default',topic_only=True,sort='relevance')[0].paper_id,'low')
            self.assertEqual(list_library(c,topic_id='second',topic_only=True,sort='relevance')[0].paper_id,'high')
            c.close()

    def test_new_paper_report_reads_active_theme_labels(self):
        with TemporaryDirectory() as t:
            c=connect(Path(t)/'papers.sqlite3')
            save_papers(c,[paper()],topic_id='default')
            save_papers(c,[paper()],topic_id='other')
            update_paper_flags(c,'paper:1',topic_id='other',starred=True,feedback=1)
            values=get_selected(c,['paper:1'],topic_id='other')
            self.assertEqual((values[0].is_starred,values[0].feedback),(1,1))
            self.assertEqual(get_selected(c,['paper:1'],topic_id='default')[0].is_starred,0)
            c.close()

    def test_new_papers_for_other_theme_do_not_auto_enter_default(self):
        with TemporaryDirectory() as t:
            c=connect(Path(t)/'papers.sqlite3')
            save_papers(c,[paper()],topic_id='other')
            self.assertEqual(count_library(c,topic_id='default',topic_only=True),0)
            self.assertEqual(count_library(c,topic_id='other',topic_only=True),1)
            c.close()


class EvidenceTests(unittest.TestCase):
    def test_accept_valid_exact_excerpt(self):
        p=paper()
        raw=json.dumps({'items':[{'id':'p0','level':3,'reason':'针对复杂几何的神经算子具有相关性',
                  'evidence':'neural operator for geometry generalization'}]},ensure_ascii=False)
        self.assertEqual(parse_rerank(raw,[p])['p0'][0],3)

    def test_reject_fabricated_evidence_and_unhashable_ids(self):
        p=paper()
        raw=json.dumps({'items':[{'id':['p0'],'level':3,'reason':'有很强的相关性需要精读',
                   'evidence':'neural operator for geometry generalization'},
                  {'id':'p0','level':3,'reason':'未经原文支持的高相关结果',
                   'evidence':'99.9 percent accuracy improvement'}]},ensure_ascii=False)
        self.assertEqual(parse_rerank(raw,[p]),{})

    def test_skip_incorrect_level_and_duplicate_id(self):
        p=paper()
        item={'id':'p0','level':3,'reason':'与研究主题直接相关的论文',
               'evidence':'neural operator for geometry generalization'}
        raw=json.dumps({'items':[{**item,'level':'3'},item,item]},ensure_ascii=False)
        self.assertEqual(list(parse_rerank(raw,[p])),['p0'])

    def test_opt_in_off_never_calls_model(self):
        with TemporaryDirectory() as t:
            c=connect(Path(t)/'data.sqlite3')
            with patch('paper_radar.recommend.chat_completion',side_effect=AssertionError('unexpected')):
                self.assertEqual(rerank_papers([paper()],{**config(),'ai_rerank_enabled':False},c)['analyzed'],0)
            c.close()

    def test_valid_model_result_cached_by_theme_and_reused(self):
        with TemporaryDirectory() as t:
            c=connect(Path(t)/'data.sqlite3')
            p=paper();p.score=4.0
            settings=config()
            response=json.dumps({'items':[{'id':'p0','level':3,'reason':'几何泛化研究高度相关，建议优先阅读',
                   'evidence':'neural operator for geometry generalization'}]},ensure_ascii=False)
            with patch('paper_radar.recommend.get_api_key',return_value='fake_key'),\
                 patch('paper_radar.recommend.chat_completion',return_value=response) as call:
                self.assertEqual(rerank_papers([p],settings,c)['analyzed'],1)
                self.assertEqual(p.score,8)
                save_papers(c,[p],topic_id='default')
                second=paper();second.score=4.0
                self.assertEqual(rerank_papers([second],settings,c)['cached'],1)
                self.assertEqual(second.score,8)
                self.assertEqual(call.call_count,1)
                settings['active_topic_id']='different'
                third=paper();third.score=4
                self.assertEqual(rerank_papers([third],settings,c)['analyzed'],1)
                self.assertEqual(call.call_count,2)
            c.close()

    def test_timeout_fallback_preserves_baseline_score(self):
        with TemporaryDirectory() as t:
            c=connect(Path(t)/'data.sqlite3')
            p=paper();p.score=5
            with patch('paper_radar.recommend.get_api_key',return_value='mock_key'),\
                 patch('paper_radar.recommend.chat_completion',side_effect=RuntimeError('timeout')):
                report=rerank_papers([p],config(),c,logger=lambda m:None)
                self.assertEqual(report['failed_batches'],1)
                self.assertEqual(p.score,5)
            c.close()

    def test_invalid_response_does_not_upgrade_rank(self):
        with TemporaryDirectory() as t:
            c=connect(Path(t)/'data.sqlite3')
            p=paper();p.score=4
            with patch('paper_radar.recommend.get_api_key',return_value='mock_key'),\
                 patch('paper_radar.recommend.chat_completion',return_value='{"items":[{"id":"p0","level":3,"reason":"有相关研究价值","evidence":"invented facts"}]}'):
                d=rerank_papers([p],config(),c)
                self.assertEqual(d['invalid'],1)
                self.assertEqual(p.score,4)
            c.close()


class BatchAndHistoryTests(unittest.TestCase):
    def test_topic_snapshots_do_not_mutate_settings(self):
        cfg={**config(),'topics':[
            {'id':'default','name':'A','keywords':['neural operator']},
            {'id':'other','name':'B','keywords':['thermal management']}],
            'active_topic_id':'default'}
        copies=topic_snapshots(cfg)
        self.assertEqual([c['active_topic_id'] for c in copies],['default','other'])
        self.assertEqual(copies[1]['keywords'],['thermal management'])
        self.assertEqual(cfg['keywords'],['neural operator'])

    def test_batch_partial_failure_does_not_block_other_topics(self):
        cfg={**config(),'topics':[{'id':'default','name':'A'}, {'id':'other','name':'B'}]}
        seen=[];records=[]
        def runner(c,logger,progress):
            seen.append(c['active_topic_id'])
            if c['active_topic_id']=='default':raise ValueError('offline')
            return {'retrieved':5,'new':2,'selected':4}
        def recorder(*args,**kwargs):records.append((args,kwargs))
        result=run_all_topics(cfg,runner=runner,recorder=recorder,logger=lambda m:None)
        self.assertEqual(seen,['default','other'])
        self.assertEqual((result['topics_succeeded'],result['topics_failed']),(1,1))
        self.assertEqual(len(records),2)

    def test_batch_all_failure_fails_explicitly(self):
        with self.assertRaisesRegex(RuntimeError,'全部研究主题'):
            run_all_topics(config(),runner=lambda *a,**kw: (_ for _ in ()).throw(ValueError('broken')),
                           recorder=lambda *a,**kw:None,logger=lambda m:None)

    def test_history_db_only_saves_bounded_summary(self):
        with TemporaryDirectory() as t:
            path=Path(t)/'history.sqlite3'
            record_run('topic','search','success',result={'retrieved':5,'new':2,'private_text':'super secret',
                'source_results':[{'name':'arxiv','ok':True,'count':5,'queries':['alpha','beta']}]},path=path)
            self.assertEqual(len(recent_runs(topic_id='topic',path=path)),1)
            self.assertEqual(len(recent_runs(topic_id='other',path=path)),0)
            raw=path.read_bytes()
            self.assertNotIn(b'super secret',raw)
            self.assertEqual(recent_runs(path=path)[0]['summary']['sources'][0]['query_count'],2)


class SemanticSourceTests(unittest.TestCase):
    def test_semantic_source_off_by_default_and_registered(self):
        self.assertNotIn('openalex_semantic',DEFAULTS['sources'])
        self.assertIn('openalex_semantic',{s['name'] for s in available_sources()})

    def test_semantic_api_uses_bounded_query_and_parses(self):
        cfg={**config(),'max_results':12}
        payload={'results':[{'id':'https://openalex.org/W1234567',
                  'display_name':'Neural operator geometry generalization',
                  'publication_date':'2026-11-03',
                  'abstract_inverted_index':{'Neural':[0],'operator':[1]}}]}
        def fake_open(request,timeout):
            self.assertIn('search.semantic=',request.full_url)
            self.assertIn('per_page=12',request.full_url)
            self.assertEqual(timeout,35)
            return BytesIO(json.dumps(payload).encode('utf-8'))
        with patch('paper_radar.sources.openalex_semantic.request.urlopen',side_effect=fake_open):
            papers=OpenAlexSemanticSource().fetch(cfg,logger=lambda _:None,now=NOW)
            self.assertEqual(len(papers),1)
            self.assertEqual(papers[0].source,'openalex_semantic')

    def test_quota_error_does_not_fabricate_papers(self):
        cfg=config()
        with patch('paper_radar.sources.openalex_semantic.request.urlopen',
                   side_effect=error.HTTPError('https://api.openalex.org/works',429,'Rate limit',None,None)):
            with self.assertRaisesRegex(RuntimeError,'429'):
                OpenAlexSemanticSource().fetch(cfg,logger=lambda _:None,now=NOW)


if __name__=='__main__':unittest.main()
