# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Publication-date, evidence and UI regression scenarios from a real report."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import csv
import unittest
from docx import Document
from paper_radar.core import DEFAULTS, connect, save_papers, get_recent, run_cycle, rank
from paper_radar.models import Paper
from paper_radar.quality import score_relevance, valid_recent_publication, matches
from paper_radar.reports.evidence import paper_brief, result_evidence
from paper_radar.reports.synthesis import describe_research
from paper_radar.reports.text import write_markdown
from paper_radar.reports.word import write_report
from paper_radar import cloud_api

NOW = datetime(2026, 11, 3, 11, tzinfo=timezone.utc)
KEYWORDS = ['embankment', 'slope', 'numerical']

def paper(pid, title, abstract='', published='2026-11-02'):
    return Paper(paper_id=pid,title=title,abstract=abstract,authors='Research Author',
                 published=published,updated=published,category='article',
                 url=f'https://doi.org/10.1234/{pid}',pdf_url='',source='crossref')

class TestReportQuality(unittest.TestCase):
    def test_published_complete_valid(self):
        self.assertTrue(valid_recent_publication('2026-11-02', NOW, 7))
        self.assertTrue(valid_recent_publication('2026-11-03T00:00:00Z', NOW, 7))
    def test_published_future_and_partial_invalid(self):
        for value in ['2027-03','2027-04-18','2026-11','2026','2026-11-04','2026-11-02 garbage','2026-02-30','']:
            self.assertFalse(valid_recent_publication(value,NOW,7),value)
    def test_published_expired_invalid(self):
        self.assertFalse(valid_recent_publication('2026-09-01', NOW, 7))
    def test_generic_term_without_domain_rejected(self):
        self.assertEqual(score_relevance('Numerical thermoelastic damping in piezoelectric resonators','',KEYWORDS),0.0)
    def test_domain_match_title_retained(self):
        self.assertGreater(score_relevance('Slope stability analysis in weathered rock','',KEYWORDS),0)
    def test_match_no_subword_false_positive(self):
        self.assertFalse(matches('underpinning assumptions', 'pinn'))
    def test_generic_boost_only_with_anchor(self):
        specific=score_relevance('Slope stability','',KEYWORDS)
        with_generic=score_relevance('Numerical slope stability','',KEYWORDS)
        self.assertGreater(with_generic,specific)
    def test_results_evidence_is_direct_and_cautious(self):
        self.assertEqual(result_evidence('Sediment yield and soil erosion are important for slope restoration research.'), '')
        self.assertIn('Results show',result_evidence('This paper studies soft clay under embankments. Results show that settlement is reduced by 25% under the stated field conditions.'))
    def test_missing_abstract_does_not_claim_results(self):
        b=paper_brief(paper('x','Railway slope settlement monitoring'),KEYWORDS)
        self.assertIn('仅题录',b['status'])
        self.assertEqual(b['evidence'],'')
    def test_synthesis_no_unrelated_methods(self):
        p=paper('a','Embankment settlement and ground improvement','We present a field study that reports settlement responses for embankments.')
        s=describe_research([p],{'keywords':KEYWORDS,'lookback_days':7})
        self.assertIn('研究范围：',s)
        self.assertIn('待核查问题：',s)
        self.assertNotIn('Transformer',s)
        self.assertNotIn('神经算子',s)
    def test_existing_database_future_papers_excluded(self):
        with TemporaryDirectory() as d:
            with connect(Path(d)/'papers.db') as c:
                save_papers(c,[paper('future','Numerical slope analysis','','2027-03'),paper('valid','Slope stability in rock','A detailed abstract.','2026-11-02')])
                data=get_recent(c,days=7,keywords=KEYWORDS,now=NOW)
            self.assertEqual([x.paper_id for x in data],['valid'])
    def test_word_and_markdown_summaries_evidence(self):
        data=[paper('x','Slope stability in weathered rock','The research uses a response surface model to estimate failure probability. Results show that the probability is 2.7 percent under the tested conditions.')]
        cfg={**DEFAULTS,'keywords':KEYWORDS,'lookback_days':7}
        with TemporaryDirectory() as d:
            p=Path(d)
            write_report(p/'a.docx','近期文献汇总',data,cfg,1)
            write_markdown(p/'a.md',data,cfg)
            word='\n'.join(z.text for z in Document(p/'a.docx').paragraphs)
            md=(p/'a.md').read_text('utf-8')
        for val in (word,md):
            self.assertLess(val.index('本期研究综述'), val.index('优先阅读'))
            self.assertIn('2.7 percent',val)
            self.assertIn('待核查',val)
    def test_pipeline_rejects_future_and_broad(self):
        candidates=[paper('future','Numerical slope stability','', '2027-03'),
                    paper('broad','Numerical piezoelectric resonance','','2026-11-02'),
                    paper('good','Slope stability evaluation','This is an investigation of soil slopes.','2026-11-02')]
        cfg={**DEFAULTS,'keywords':KEYWORDS,'sources':['arxiv'],'output_dir':'some_unused','ai_enabled':False}
        with TemporaryDirectory() as d:
            cfg['output_dir']=d
            result=run_cycle(cfg,logger=lambda x:None,db_path=Path(d)/'data'/'papers.db',fetcher=lambda *a,**kw:candidates,now=NOW)
            self.assertEqual(result['retrieved'],1)
            self.assertEqual(result['selected'],1)
            self.assertTrue((Path(d)/'近期文献汇总.md').exists())
    def test_ux_primary_controls_and_collapsible_sections(self):
        root=Path(__file__).resolve().parent.parent
        html=(root/'paper_radar/static/index.html').read_text('utf-8')
        script=(root/'paper_radar/static/app.js').read_text('utf-8')
        for needle in ['id="runSearch"','id="topicKeywords"','id="searchAdvanced"',
                       'id="scheduleDisclosure"','id="markdownDialog"','id="afterSearch"']:
            self.assertIn(needle,html)
        self.assertIn('paper-summary',script)
    def test_ai_prompt_requests_methods_results_limits(self):
        p=paper('test','Slope stability','This paper evaluates field response using measurements.')
        with patch.object(cloud_api,'chat_completion',return_value='fake summary') as mocked:
            cloud_api.summarize_paper(p,{})
        prompt=mocked.call_args.args[1]
        for field in ('研究问题','研究方法','关键结果','局限与待核查'):
            self.assertIn(field,prompt)

if __name__=='__main__':unittest.main()

class TestOfflineAudit(unittest.TestCase):
    def test_audit_counts_and_csv(self):
        from tools.offline_review import audit,create_review
        candidates=[paper('a','Numerical slope stability','','2027-03'),
                    paper('b','Numerical electric resonance','','2026-11-02'),
                    paper('c','Embankment failure analysis','Results show that settlement is decreased by 14% at this site.','2026-11-02')]
        picked, details=audit(candidates,keywords=KEYWORDS,now=NOW,days=7)
        self.assertEqual([x.paper_id for x in picked],['c'])
        self.assertEqual([x['decision'] for x in details],['日期超出范围或不完整','主题相关性不足','入选'])
    def test_offline_review_does_not_require_network(self):
        from tools.offline_review import create_review
        with TemporaryDirectory() as d:
            source=Path(d)/'source.csv'
            with source.open('w',encoding='utf-8-sig',newline='') as f:
                w=csv.DictWriter(f,fieldnames=['论文ID','标题','日期','原始摘要','论文链接','来源']);w.writeheader()
                w.writerow({'论文ID':'x','标题':'Embankment deformation','日期':'2026-11-02','原始摘要':'This work investigates soil deformation.','论文链接':'https://example.org/x','来源':'openalex'})
            result=create_review(source,Path(d)/'out',KEYWORDS,NOW,7)
            self.assertEqual(result['selected'],1)
            self.assertTrue((Path(d)/'out'/'复核明细.csv').exists())
            self.assertTrue((Path(d)/'out'/'近期文献汇总_复核.docx').exists())
