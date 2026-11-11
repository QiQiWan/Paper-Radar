# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.49.0: grounded PDF question answering and guided setup regressions."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sqlite3

import pytest

from paper_radar.core import connect, save_papers
from paper_radar.models import Paper
from paper_radar.fulltext import index_paper
from paper_radar.paper_questions import ask_local, ask_public, keywords, _safe_answer


def demo_paper(pid: str) -> Paper:
    return Paper(paper_id=pid, title='Neural operator in complex geometry',
                 abstract='Generalization and experiments', authors='Test Author',
                 published='2026-11-01',updated='2026-11-01',category='cs.LG',
                 url='https://example.org/a',pdf_url='',source='arxiv')


def make_pdf(path: Path, text: str):
    pytest.importorskip('pypdf')
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
    writer=PdfWriter()
    font=writer._add_object(DictionaryObject({
        NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/Type1'),
        NameObject('/BaseFont'):NameObject('/Helvetica')}))
    page=writer.add_blank_page(width=612,height=792)
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):
        DictionaryObject({NameObject('/F1'):font})})
    stream=DecodedStreamObject()
    clean=text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
    stream.set_data(('BT /F1 13 Tf 72 700 Td ('+clean+') Tj ET').encode('ascii'))
    page[NameObject('/Contents')]=writer._add_object(stream)
    writer.write(path)


@pytest.fixture
def indexed_paper(tmp_path):
    db=tmp_path/'data'/'papers.sqlite3'
    conn=connect(db)
    save_papers(conn,[demo_paper('paper-149'),demo_paper('other-paper')],topic_id='default')
    download=tmp_path/'downloads'
    download.mkdir()
    sample=download/'study.pdf'
    make_pdf(sample,'Experiments. Neural operator baseline error 12 percent. We compare graph methods on geometry meshes.')
    conn.execute("UPDATE papers SET local_file='study.pdf',download_status='downloaded' WHERE paper_id='paper-149'")
    conn.commit()
    assert index_paper(conn,'paper-149',db_path=db)['status']=='indexed'
    yield conn,db,sample
    conn.close()


def test_chinese_query_bilingual_heuristic():
    # English PDF + Chinese question should find a common academic term offline.
    terms=keywords('作者对比了哪些实验基线，结果如何？')
    assert 'baseline' in terms and 'experiment' in terms
    assert 'geometry' in keywords('这篇论文如何实现跨几何泛化？')
    assert keywords('What is the baseline error?')
    with pytest.raises(ValueError):keywords('短')


def test_unindexed_paper_is_honest(indexed_paper):
    conn,db,_=indexed_paper
    result=ask_local(conn,paper_id='other-paper',question='Compare baseline experiments',db_path=db)
    assert result['status']=='needs_index' and not result['references']


def test_local_quotes_page_and_file_change(indexed_paper):
    conn,db,pdf=indexed_paper
    result=ask_local(conn,paper_id='paper-149',question='作者对比了哪些实验基线，结果如何？',db_path=db)
    assert result['status']=='evidence_only'
    assert result['references'][0]['page']==1
    assert 'baseline' in result['references'][0]['quote'].lower()
    make_pdf(pdf,'Unrelated. A new file replaces the previous experiment paper.')
    stale=ask_local(conn,paper_id='paper-149',question='Compare baseline experiments',db_path=db)
    assert stale['status']=='needs_index' and not stale['references']


def test_local_model_requires_permission_and_rejects_invented_numbers(indexed_paper):
    conn,db,_=indexed_paper
    cfg={'api_provider':'DeepSeek','llm_data_consent':False,'api_model':'stub'}
    with pytest.raises(ValueError,match='明确确认'):
        ask_local(conn,paper_id='paper-149',question='Compare baseline experiments',db_path=db,
                  use_model=True,confirm_transfer=True,config=cfg)
    cfg['llm_data_consent']=True
    with patch('paper_radar.cloud_api.get_api_key',return_value='only-test'):
        fake=lambda *_a,**_k:'The neural operator improves error by 99 percent [1].'
        result=ask_local(conn,paper_id='paper-149',question='Compare baseline experiments',db_path=db,
                         use_model=True,confirm_transfer=True,config=cfg,chat=fake)
        assert result['status']=='model_unverified' and not result['model_used']
        good=ask_local(conn,paper_id='paper-149',question='Compare baseline experiments',db_path=db,
                       use_model=True,confirm_transfer=True,config=cfg,
                       chat=lambda *_a,**_k:'The source reports baseline error of 12 percent [1].')
    assert good['status']=='model_cited' and good['model_used']
    assert good['references'][0]['page']==1


def test_reference_ids_and_numerical_support():
    r=[{'quote':'On geometry: error 12 percent'}]
    assert not _safe_answer('error 22 percent [1]',r)
    assert not _safe_answer('The error is 12 percent [3]',r)
    assert _safe_answer('The error is 12 percent [1]',r)
    assert not _safe_answer('No source ID',r)


def test_public_scoped_pdf_sources_and_change(tmp_path):
    pdf=tmp_path/'paper.pdf'
    make_pdf(pdf,'Neural operator experiment compares baseline model and geometry mesh.')
    stamp=sha256(pdf.read_bytes()).hexdigest()
    db=sqlite3.connect(':memory:')
    db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE pdf_pages (paper_id TEXT, sha256 TEXT, page INTEGER, text TEXT)')
    db.execute('INSERT INTO pdf_pages VALUES (?,?,?,?)',('paper-149',stamp,1,'Baseline model and geometry experiment result.'))
    class Dummy:
        @contextmanager
        def workspace(self,user):
            assert user==7
            yield db
    portal=Dummy()
    with patch('paper_radar.portal_research.check_member') as perm, \
         patch('paper_radar.portal_research.state',return_value={'state':'indexed','sha256':stamp}) as state, \
         patch('paper_radar.portal_research.path_for',return_value=pdf):
        answer=ask_public(portal,7,'topic-a','paper-149','Compare baseline experiments')
        assert answer['references'][0]['page']==1
        assert 'Baseline' in answer['references'][0]['quote']
        perm.assert_called_with(portal,7,'topic-a','paper-149')
        # Member validation is performed before any source text is accessed.
        perm.side_effect=ValueError('Paper is outside your research topic')
        with pytest.raises(ValueError,match='outside'):
            ask_public(portal,7,'topic-b','paper-149','Compare baseline experiments')
        perm.side_effect=None
        pdf.write_bytes(pdf.read_bytes()+b'\n%change')
        stale=ask_public(portal,7,'topic-a','paper-149','Compare baseline experiments')
        assert stale['status']=='stale' and not stale['references']
    db.close()


def test_static_wizard_routes():
    root=Path(__file__).resolve().parents[1]
    html=(root/'paper_radar/static/index.html').read_text(encoding='utf-8')
    public=(root/'paper_radar/static/portal.html').read_text(encoding='utf-8')
    app=(root/'paper_radar/static/app.js').read_text(encoding='utf-8')
    assert 'id="modelConnectWizard"' in html
    assert all('data-model-step="%s"'%i in html for i in (1,2,3))
    assert 'id="followScheduleWizard"' in html
    assert 'id="readerQuestionRun"' in html and "'/api/fulltext/ask'" in app
    assert 'id="portalModelGuide"' in public and 'id="portalModelGuideRefresh"' in public
