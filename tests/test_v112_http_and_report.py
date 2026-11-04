# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Real localhost API contracts and on-disk evidence provenance regression."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json

import pytest

from paper_radar.core import connect, save_papers, DEFAULTS
from paper_radar.fulltext import index_paper, load_report_cards, paper_status, search_evidence
from paper_radar.models import Paper
from paper_radar import webapp
from test_v112_fulltext_evaluation import make_pdf


def setup_study(root: Path):
    db_path=root/'data'/'papers.sqlite3'
    db=connect(db_path)
    p=Paper(paper_id='sample-1', title='Geometry transfer study',
            abstract='Geometry transfer neural operator', authors='Test',
            published='2026-10-01', updated='2026-10-01', category='cs.LG',
            url='https://example.org', pdf_url='', source='arxiv')
    save_papers(db,[p],topic_id='default')
    (root/'downloads').mkdir(exist_ok=True)
    pdf=root/'downloads'/'study.pdf'
    make_pdf(pdf,['Methods. We use graph neural operators on mesh geometry.',
                  'Experiments. The study reports 12% lower error on the benchmark.'])
    db.execute("UPDATE papers SET local_file='study.pdf',download_status='downloaded' WHERE paper_id=?",(p.paper_id,))
    db.commit()
    index_paper(db,p.paper_id,db_path=db_path)
    return db,db_path,pdf


def test_report_drops_claims_after_external_pdf_replacement():
    pytest.importorskip('pypdf')
    with TemporaryDirectory() as td:
        root=Path(td)
        db,db_path,pdf=setup_study(root)
        record=db.execute('SELECT pdf_sha256 FROM fulltext_documents WHERE paper_id=?',('sample-1',)).fetchone()
        chunk=db.execute('SELECT id,page,content FROM fulltext_chunks WHERE paper_id=? ORDER BY id LIMIT 1',('sample-1',)).fetchone()
        db.execute('''INSERT INTO fulltext_claims(paper_id,pdf_sha256,cache_key,category,statement,chunk_id,page,quote,generated_at)
            VALUES (?,?,?,?,?,?,?,?,?)''',('sample-1',record['pdf_sha256'],'cache','method',
            '论文采用图结构算子处理不规则网格研究。',chunk['id'],chunk['page'],
            'We use graph neural operators on mesh geometry.','2026-11-04'))
        db.commit()
        assert len(load_report_cards(db,['sample-1'],db_path=db_path)['sample-1'])==1
        make_pdf(pdf,['Methods. Replacement paper with unrelated research content.'])
        assert load_report_cards(db,['sample-1'],db_path=db_path)=={}
        assert paper_status(db,'sample-1',db_path=db_path)['document']['state']=='stale'
        assert not search_evidence(db,'mesh geometry',paper_id='sample-1')
        db.close()


def test_http_fulltext_lookup_and_csrf_for_jobs():
    pytest.importorskip('pypdf')
    with TemporaryDirectory() as td:
        root=Path(td)
        db,db_path,pdf=setup_study(root)
        db.close()
        @contextmanager
        def open_local_db():
            with connect(db_path) as conn:
                yield conn
        server=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
        server.token='evidence-http-test'
        thread=Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        cfg={**DEFAULTS,'active_topic_id':'default'}
        try:
            with patch.object(webapp,'DB_FILE',db_path), \
                 patch.object(webapp,'read_config',lambda:cfg), \
                 patch.object(webapp,'open_database',open_local_db):
                with urlopen(base+'/api/fulltext/status?paper_id=sample-1') as req:
                    status=json.load(req)
                assert status['indexed'] and status['document']['indexed_pages']==2
                with urlopen(base+'/api/fulltext/search?q=graph%20neural&scope=topic&paper_id=sample-1') as req:
                    matches=json.load(req)
                assert matches['total']>=1 and matches['results'][0]['page']==1
                with urlopen(base+'/api/fulltext/search?scope=topic') as req:
                    assert json.load(req)['results']==[]
                with pytest.raises(HTTPError) as invalid:
                    urlopen(base+'/api/fulltext/status?paper_id=not-present')
                assert invalid.value.code == 404  # Missing paper is a client-visible not-found condition.
                payload=json.dumps({'mode':'index_fulltext','ids':['sample-1']}).encode()
                denied=Request(base+'/api/job',data=payload,headers={'Content-Type':'application/json'})
                with pytest.raises(HTTPError) as csrf:
                    urlopen(denied)
                assert csrf.value.code==403
                capture=[]
                with patch.object(webapp.runtime,'start',lambda mode,ids=None:capture.append((mode,ids))):
                    permitted=Request(base+'/api/job',data=payload,headers={
                        'Content-Type':'application/json','X-Local-Token':server.token,
                        'Origin':base})
                    with urlopen(permitted) as req:
                        assert req.status==202
                    assert capture==[('index_fulltext',['sample-1'])]
                    invalid_mode=Request(base+'/api/job',data=json.dumps({'mode':'analyze_fulltext',
                        'ids':['sample-1','other']}).encode(),headers={
                        'Content-Type':'application/json','X-Local-Token':server.token,'Origin':base})
                    with pytest.raises(HTTPError) as bad:
                        urlopen(invalid_mode)
                    assert bad.value.code==400
        finally:
            server.shutdown();server.server_close();thread.join(3)


def test_report_fresh_claims_are_not_mutated_in_database():
    pytest.importorskip('pypdf')
    with TemporaryDirectory() as td:
        root=Path(td);db,db_path,pdf=setup_study(root)
        assert load_report_cards(db,['sample-1'],db_path=db_path)=={}
        assert db.execute("SELECT COUNT(*) FROM fulltext_chunks WHERE paper_id='sample-1'").fetchone()[0]>=2
        db.close()


def test_word_markdown_reports_cite_verified_page_and_exclude_stale_pdf():
    pytest.importorskip('pypdf')
    from paper_radar.report import write_report, write_markdown
    from docx import Document
    with TemporaryDirectory() as td:
        root=Path(td)
        db,db_path,pdf=setup_study(root)
        info=db.execute("SELECT pdf_sha256 FROM fulltext_documents WHERE paper_id='sample-1'").fetchone()
        chunk=db.execute("SELECT id,page FROM fulltext_chunks WHERE paper_id='sample-1' ORDER BY id LIMIT 1").fetchone()
        db.execute('''INSERT INTO fulltext_claims(paper_id,pdf_sha256,cache_key,category,statement,chunk_id,page,quote,generated_at)
            VALUES (?,?,?,?,?,?,?,?,?)''',('sample-1',info['pdf_sha256'],'cache','method',
            '作者采用图结构神经算子分析网格泛化问题。',chunk['id'],chunk['page'],
            'We use graph neural operators on mesh geometry.','2026-11-04'))
        db.commit()
        from paper_radar.core import get_selected
        papers=get_selected(db,['sample-1'],topic_id='default')
        assert len(papers)==1
        config={**DEFAULTS,'_fulltext_cards':load_report_cards(db,['sample-1'],db_path=db_path)}
        md=root/'report.md';doc=root/'report.docx'
        write_markdown(md,papers,config)
        write_report(doc,'近期文献汇总',papers,config,1)
        assert 'PDF 第 1 页' in md.read_text('utf-8')
        assert 'We use graph neural operators' in md.read_text('utf-8')
        document='\n'.join(paragraph.text for paragraph in Document(doc).paragraphs)
        assert 'PDF 第 1 页' in document
        assert 'We use graph neural operators' in document
        # On an external PDF replacement, newly generated reports must discard
        # stale cards even if the database row was not explicitly reindexed.
        make_pdf(pdf,['A different PDF text, replacing the previous content.'])
        config['_fulltext_cards']=load_report_cards(db,['sample-1'],db_path=db_path)
        assert not config['_fulltext_cards']
        write_markdown(md,papers,config)
        write_report(doc,'近期文献汇总',papers,config,1)
        assert 'We use graph neural operators' not in md.read_text('utf-8')
        text='\n'.join(paragraph.text for paragraph in Document(doc).paragraphs)
        assert 'We use graph neural operators' not in text
        db.close()
