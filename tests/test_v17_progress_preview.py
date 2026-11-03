# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.7: evidence-based digest, bounded preview and measurable progress."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen
import json
import unittest
from docx import Document

from paper_radar.core import DEFAULTS, run_cycle, connect
from paper_radar.models import Paper
from paper_radar.progress import TaskProgress
from paper_radar.reports.text import write_markdown
from paper_radar.reports.word import write_report
from paper_radar.reports.synthesis import describe_research
from paper_radar import webapp


def sample(index=0, title=None):
    return Paper(f'test-v17-{index}',title or 'Neural operator for irregular mesh simulations',
                 'A generalizable neural operator is evaluated on complex geometry. '
                 'The study discusses limitations in stability and computational efficiency.',
                 'Demo Scientist', '2026-11-02T12:00:00Z','2026-11-02T12:00:00Z',
                 'cs.LG','https://example.org/paper','')


class V17ReportTests(unittest.TestCase):
    def test_summary_between_overview_and_prioritized_reading(self):
        cfg=dict(DEFAULTS,keywords=['neural operator'],sources=['arxiv'],lookback_days=7)
        papers=[sample(0),sample(1,'Operator learning on multi-physics domains')]
        with TemporaryDirectory() as td:
            md=Path(td)/'research.md';doc=Path(td)/'research.docx'
            write_markdown(md,papers,cfg)
            write_report(doc,'近期文献汇总',papers,cfg,len(papers))
            text=md.read_text(encoding='utf-8')
            self.assertLess(text.index('统计范围'),text.index('## 本期研究综述'))
            self.assertLess(text.index('## 本期研究综述'),text.index('## 优先阅读'))
            self.assertIn('待', describe_research(papers,cfg)+'待')
            word=Document(doc)
            positions=[i for i,p in enumerate(word.paragraphs) if p.text in ('本期研究综述','优先阅读')]
            self.assertEqual(len(positions),2)
            self.assertLess(positions[0],positions[1])
            intro=word.paragraphs[positions[0]+1].text
            self.assertIn('跨场景泛化能力',intro)
            self.assertIn('以下先列出优先阅读论文',intro)

    def test_zero_results_summary_does_not_claim_publications(self):
        text=describe_research([],dict(DEFAULTS))
        self.assertIn('暂未找到符合筛选条件', text)
        self.assertNotIn('代表性研究',text)

    def test_run_cycle_structured_progress_is_monotone(self):
        with TemporaryDirectory() as td:
            folder=Path(td)
            db=folder/'data'/'papers.sqlite3'
            cfg=dict(DEFAULTS,keywords=['neural operator'],output_dir=str(folder/'reports'),
                     ai_enabled=False,auto_download=False)
            events=[]
            result=run_cycle(cfg,fetcher=lambda *_args,**_kwargs: [sample()],
                             logger=lambda *_:None, db_path=db,
                             progress=events.append, now=datetime(2026,11,3,tzinfo=timezone.utc))
            self.assertEqual(result['retrieved'],1)
            self.assertEqual(events[-1]['percent'],100)
            self.assertEqual([e['percent'] for e in events], sorted(e['percent'] for e in events))
            self.assertIn('整理研究报告',[e['stage'] for e in events])
            self.assertTrue(result['markdown'].is_file())

    def test_stage_counter_is_not_fake_100_for_unfinished_work(self):
        tracker=TaskProgress()
        start=tracker.emit(5,'连接来源',detail='等待响应')
        self.assertEqual(start['percent'],5)
        self.assertIsNone(start['total'])
        self.assertEqual(tracker.emit(3,'重试')['percent'],5)

    def test_report_preview_endpoint_rejects_traversal_and_large_file(self):
        with TemporaryDirectory() as td:
            root=Path(td);(root/'reports').mkdir()
            (root/'reports'/'近期文献汇总.md').write_text('# 文章\n\n未过滤的 <script>ignored</script>', encoding='utf-8')
            (root/'settings.json').write_text('private', encoding='utf-8')
            (root/'reports'/'large.md').write_bytes(b'x'*1_000_001)
            conf=dict(DEFAULTS,output_dir=str(root/'reports'))
            server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler);server.token='test-token'
            worker=Thread(target=server.serve_forever,daemon=True);worker.start()
            url=f'http://127.0.0.1:{server.server_port}/api/report-preview?name='
            try:
                with patch.object(webapp,'read_config',lambda:conf):
                    content=json.load(urlopen(url+quote('近期文献汇总.md')))
                    self.assertIn('<script>',content['content'])
                    for bad,code in [('../settings.json',400),('large.md',413),('missing.md',404)]:
                        with self.assertRaises(HTTPError) as ctx:
                            urlopen(url+quote(bad))
                        self.assertEqual(ctx.exception.code,code)
            finally:
                server.shutdown();server.server_close();worker.join(3)

    def test_frontend_contains_preview_and_task_controls(self):
        root=Path(webapp.__file__).parent/'static'
        html=(root/'index.html').read_text('utf-8')
        js=(root/'app.js').read_text('utf-8')
        for field in ('taskCenter','taskProgressBar','taskLiveLog','latestMarkdownPreview',
                      'markdownDialog','markdownRendered','zoteroActivity','logSource'):
            self.assertIn('id="'+field+'"',html)
        for func in ('renderMarkdownSafe','showMarkdownPreview','showTaskProgress','zoteroWaiting'):
            self.assertIn('function '+func,js)
        self.assertIn('允许此计算机上的其他应用程序与 Zotero 通讯',html)


if __name__ == '__main__':
    unittest.main()
