# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.12 evidence indexing, hallucination gates and offline recommendation eval."""
from __future__ import annotations
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import csv
import json

import pytest
from paper_radar.core import connect, save_papers, DEFAULTS
from paper_radar.models import Paper
from paper_radar.fulltext import (index_paper, paper_status, search_evidence,
    validate_claims, analyze_paper, _chunks, _term_tokens)
from paper_radar.evaluation import metrics, read_labels, evaluate_database, export_labeling_template, main as eval_main


def demo_paper(pid='demo-a'):
    return Paper(paper_id=pid,title='Generalization of neural operators',
        abstract='Operator learning on geometry and meshes.',authors='A Researcher',
        published='2026-11-01',updated='2026-11-01',category='cs.LG',
        url='https://example.org',pdf_url='',source='arxiv')


def make_pdf(path: Path, texts: list[str]):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
    writer=PdfWriter()
    font=writer._add_object(DictionaryObject({
        NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/Type1'),
        NameObject('/BaseFont'):NameObject('/Helvetica')}))
    for sentence in texts:
        page=writer.add_blank_page(width=612,height=792)
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):
            DictionaryObject({NameObject('/F1'):font})})
        stream=DecodedStreamObject()
        safe=sentence.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        stream.set_data(('BT /F1 13 Tf 72 700 Td ('+safe+') Tj ET').encode('ascii'))
        page[NameObject('/Contents')]=writer._add_object(stream)
    writer.write(path)


class TestFulltext:
    def prepare(self, root, pid='demo-a', title='Generalization of neural operators'):
        db_path=root/'data'/'papers.sqlite3'
        conn=connect(db_path)
        p=demo_paper(pid);p.title=title
        save_papers(conn,[p],topic_id='default')
        root.joinpath('downloads').mkdir(exist_ok=True)
        return conn,db_path

    def test_index_real_pdf_search_scope_cache_and_reindex(self):
        pytest.importorskip('pypdf')
        with TemporaryDirectory() as td:
            root=Path(td)
            conn,db=self.prepare(root)
            pdf=root/'downloads'/'sample.pdf'
            make_pdf(pdf,['Introduction. Neural operator geometry generalization on meshes.',
                          'Methods. Graph neural network improves geometry transfer over meshes.',
                          'Experiments. On benchmark data the model reaches 5 percent error.'])
            conn.execute("UPDATE papers SET local_file='sample.pdf',download_status='downloaded' WHERE paper_id='demo-a'")
            conn.commit()
            r=index_paper(conn,'demo-a',db_path=db)
            assert r['status']=='indexed' and r['pages']==3
            assert r['chunks']>=3
            assert index_paper(conn,'demo-a',db_path=db)['status']=='cached'
            hits=search_evidence(conn,'geometry transfer',topic_id='default')
            assert hits and hits[0]['page'] in (1,2)
            assert not search_evidence(conn,'geometry transfer',topic_id='other')
            assert search_evidence(conn,'geometry transfer',paper_id='demo-a')
            assert paper_status(conn,'demo-a',db_path=db)['indexed']
            assert paper_status(conn,'demo-a',db_path=db)['has_pdf']
            # PDF changed: old chunks and any previous claim must be invalidated.
            conn.execute("INSERT INTO fulltext_claims(paper_id,pdf_sha256,cache_key,category,statement,chunk_id,page,quote,generated_at) VALUES ('demo-a',?,?,?,?,?,?,?,?)",
                (paper_status(conn,'demo-a',db_path=db)['document']['pdf_sha256'],'stale','method','The method uses an operator over meshes',1,1,'Neural operator geometry generalization','now'))
            conn.commit()
            make_pdf(pdf,['Introduction. New unrelated text replaces the original document.'])
            updated=index_paper(conn,'demo-a',db_path=db)
            assert updated['status']=='indexed'
            assert not search_evidence(conn,'geometry transfer',paper_id='demo-a')
            assert paper_status(conn,'demo-a',db_path=db)['claims']==[]
            conn.close()

    def test_missing_local_pdf_invalid_path_and_no_text(self):
        pytest.importorskip('pypdf')
        with TemporaryDirectory() as td:
            root=Path(td)
            conn,db=self.prepare(root)
            with pytest.raises(ValueError,match='先下载'):
                index_paper(conn,'demo-a',db_path=db)
            conn.execute("UPDATE papers SET local_file='../outside.pdf' WHERE paper_id='demo-a'")
            conn.commit()
            with pytest.raises(ValueError,match='先下载'):
                index_paper(conn,'demo-a',db_path=db)
            pdf=root/'downloads'/'blank.pdf'
            make_pdf(pdf,[''])
            conn.execute("UPDATE papers SET local_file='blank.pdf' WHERE paper_id='demo-a'")
            conn.commit()
            out=index_paper(conn,'demo-a',db_path=db)
            assert out['status']=='no_text'
            assert not paper_status(conn,'demo-a',db_path=db)['indexed']
            conn.close()

    def test_pdf_bad_magic_size_and_text_chunks(self):
        with TemporaryDirectory() as td:
            root=Path(td);conn,db=self.prepare(root)
            f=root/'downloads'/'fake.pdf';f.write_bytes(b'not-a-pdf-with-data')
            conn.execute("UPDATE papers SET local_file='fake.pdf' WHERE paper_id='demo-a'")
            conn.commit()
            with pytest.raises(ValueError,match='文件头'):
                index_paper(conn,'demo-a',db_path=db)
            sample='First method. '+('scientific paper text '*125)
            windows=_chunks(sample,size=220,overlap=30)
            assert len(windows)>3 and all(len(x)<=220 for x in windows)
            assert len(_term_tokens('a "foo bar" and geometry 2026'))<=8
            conn.close()

    def test_claims_require_exact_quote_and_numeric_support(self):
        context={1:{'page':2,'content':'Experiments show a 12% improvement on the benchmark and better efficiency.'}}
        good={'category':'experiment','chunk_id':1,
              'statement':'实验报告在该基准上提升了 12% 的表现，需核实具体指标。',
              'quote':'Experiments show a 12% improvement on the benchmark'}
        forged={'category':'experiment','chunk_id':1,
                'statement':'实验报告提升了 88% 的表现，缺少证据。','quote':good['quote']}
        wrong={'category':'experiment','chunk_id':2,
                'statement':'实验报告提升了 12% 的表现，缺少对应片段。','quote':good['quote']}
        result=validate_claims(json.dumps({'claims':[good,forged,wrong]},ensure_ascii=False),context)
        assert len(result)==1 and result[0]['page']==2
        assert validate_claims('{bad JSON',context)==[]
        assert validate_claims(json.dumps({'claims':{'chunk_id':1}}),context)==[]

    def test_model_analysis_explicit_cache_and_invalid_drop(self):
        pytest.importorskip('pypdf')
        with TemporaryDirectory() as td:
            root=Path(td);conn,db=self.prepare(root)
            f=root/'downloads'/'study.pdf'
            make_pdf(f,['Methods. We use a graph neural operator on unstructured meshes.',
                        'Experiments. The evaluation shows 12 percent lower error than baseline.'])
            conn.execute("UPDATE papers SET local_file='study.pdf',download_status='downloaded' WHERE paper_id='demo-a'")
            conn.commit()
            index_paper(conn,'demo-a',db_path=db)
            chunks=conn.execute("SELECT id,page,content FROM fulltext_chunks WHERE paper_id='demo-a' ORDER BY id").fetchall()
            cid=chunks[1]['id']
            good={'claims':[{'category':'experiment','statement':'实验误差与基线相比下降了 12 percent。',
                   'chunk_id':cid,'quote':'The evaluation shows 12 percent lower error than baseline.'}]}
            calls=[]
            def fake_chat(config,prompt,**kwargs):
                calls.append((prompt,kwargs))
                assert len(prompt)<20000
                return json.dumps(good,ensure_ascii=False)
            config={**DEFAULTS,'api_model':'demo-model'}
            first=analyze_paper(conn,'demo-a',config,chat=fake_chat,get_key=lambda p:'secret')
            assert first['status']=='analyzed' and first['claims']==1
            second=analyze_paper(conn,'demo-a',config,chat=fake_chat,get_key=lambda p:'secret')
            assert second['status']=='cached' and len(calls)==1
            assert paper_status(conn,'demo-a',db_path=db)['claims'][0]['page']==2
            conn.close()


class TestRecommendationEvaluation:
    def test_metrics_with_unjudged_and_no_relevant(self):
        report=metrics(['a','b','c'],{'a':3,'b':0,'c':2},k=2)
        assert report['precision_at_k_lower_bound']==0.5
        assert report['recall_at_k_judged_pool']==0.5
        assert report['ndcg_at_k_judged_pool'] is not None
        assert metrics(['a','b'],{'a':0,'b':0},k=2)['recall_at_k_judged_pool'] is None
        assert metrics(['a','x'],{'a':3},k=2)['topk_label_coverage']==0.5

    def test_csv_validation_and_database_eval(self):
        with TemporaryDirectory() as td:
            root=Path(td);conn=connect(root/'paper.sqlite3')
            a=demo_paper('a');a.score=8
            b=demo_paper('b');b.score=2
            save_papers(conn,[a,b]);conn.close()
            labels=root/'labels.csv'
            labels.write_text('topic_id,paper_id,label\ndefault,a,3\ndefault,b,0\n',encoding='utf-8')
            data=evaluate_database(root/'paper.sqlite3',labels,k=2)
            assert data['topics']['default']['judged_count']==2
            assert data['topics']['default']['judged_relevant_total']==1
            assert data['topics']['default']['precision_at_k_lower_bound']==0.5
            labels.write_text('topic_id,paper_id,label\ndefault,a,5\n',encoding='utf-8')
            with pytest.raises(ValueError,match='0 至 3'):
                read_labels(labels)
            labels.write_text('topic_id,paper_id,label\ndefault,a,2\ndefault,a,3\n',encoding='utf-8')
            with pytest.raises(ValueError,match='重复'):
                read_labels(labels)


def test_annotation_template_export_and_cli():
    with TemporaryDirectory() as td:
        root=Path(td)
        database=root/'paper.sqlite3'
        with connect(database) as db:
            paper=demo_paper('one')
            paper.title='=HYPERLINK("bad")'
            save_papers(db,[paper],topic_id='default')
        output=root/'labels.csv'
        result=export_labeling_template(database,output,per_topic=10)
        assert result['papers']==1
        with output.open(encoding='utf-8-sig',newline='') as stream:
            row=list(csv.DictReader(stream))[0]
        assert row['label']=='' and row['paper_id']=='one'
        assert row['title'].startswith("'=")  # Prevent spreadsheet formula injection.
        assert eval_main(['--db',str(database),'--export-template',str(root/'auto.csv')])==0
        assert (root/'auto.csv').exists()
