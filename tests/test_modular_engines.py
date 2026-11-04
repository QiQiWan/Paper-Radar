# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Contract tests for built-in source adapters, download engines and storage."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib import request, error
import io
import json
import socket
import unittest

from paper_radar.core import Paper, DEFAULTS, connect, save_papers, get_recent, validate_config
from paper_radar.sources import available_sources
from paper_radar.sources.openalex import parse_work, rebuild_abstract, OpenAlexSource
from paper_radar.downloads import available_downloaders
from paper_radar.downloads.manager import (assert_public_https, download_selected,
     stream_pdf, resolve_saved_file, safe_filename)
from paper_radar import webapp


def paper(pid='2610.01234', doi='', source='arxiv', url='https://arxiv.org/abs/2610.01234', direct='https://arxiv.org/pdf/2610.01234'):
    return Paper(pid, 'Neural Operator for PDE Simulation', 'operator learning methods for numerical PDE solutions',
                 'A. Example', '2026-10-25T00:00:00Z', '2026-10-25T00:00:00Z', 'cs.LG',
                 url, direct, source=source, doi=doi, is_oa=bool(direct), download_url=direct)


class EngineTests(unittest.TestCase):
    def test_registered_source_and_downloaders(self):
        self.assertEqual({s['name'] for s in available_sources()}, {'arxiv','openalex','crossref','springer','elsevier','sciencedirect','openalex_semantic'})
        self.assertEqual(set(available_downloaders()),{'arxiv_pdf','open_access_pdf'})
        config=dict(DEFAULTS); config['sources']=['arxiv','openalex']
        self.assertEqual(validate_config(config)['sources'], ['arxiv','openalex'])
        with self.assertRaisesRegex(ValueError,'有效的文献来源'):
            validate_config({**config,'sources':['unexpected']})

    def test_openalex_parse_abstract_and_oa_url(self):
        item = {'id':'https://openalex.org/W12345', 'title':'Study of Neural Operators',
                'doi':'https://doi.org/10.1000/example',
                'abstract_inverted_index':{'Neural':[0], 'operators':[1], 'work':[2]},
                'publication_date':'2026-10-30', 'type':'article',
                'authorships':[{'author':{'display_name':'Example Author'}}],
                'primary_location':{'source':{'display_name':'Journal Example'}},
                'open_access':{'is_oa':True},
                'best_oa_location':{'is_oa':True,'pdf_url':'https://repository.example.org/ab.pdf','license':'cc-by'}}
        x=parse_work(item)
        self.assertEqual(x.paper_id,'openalex:W12345')
        self.assertEqual(x.doi,'10.1000/example')
        self.assertEqual(x.venue,'Journal Example')
        self.assertEqual(x.abstract,'Neural operators work')
        self.assertEqual(x.download_url,'https://repository.example.org/ab.pdf')
        self.assertIsNone(parse_work({'id':'','title':'no id'}))

    def test_openalex_mocks_api(self):
        class Response(io.BytesIO):
            pass
        data={'results':[{'id':'https://openalex.org/W99','title':'OpenAlex neural operator','publication_date':'2026-11-01'}]}
        fake=lambda *args,**kwargs: Response(json.dumps(data).encode())
        with patch('paper_radar.sources.openalex.request.urlopen',side_effect=fake):
            cfg={**DEFAULTS,'keywords':['neural operator'], 'max_results':10}
            rows=OpenAlexSource().fetch(cfg,logger=lambda *_:None)
        self.assertEqual([p.paper_id for p in rows],['openalex:W99'])

    def test_legacy_schema_download_fields_and_doi_dedup(self):
        with TemporaryDirectory() as td:
            db_path=Path(td)/'data'/'papers.sqlite3'
            with connect(db_path) as db:
                a=paper(doi='10.5555/example')
                self.assertEqual(len(save_papers(db,[a])),1)
                self.assertEqual(db.execute('SELECT download_url FROM papers').fetchone()[0],a.pdf_url)
                b=paper('openalex:W777',doi='10.5555/example',source='openalex',
                    url='https://doi.org/10.5555/example',direct='https://oa.example.org/x.pdf')
                b.venue='Example Journal'
                self.assertEqual(len(save_papers(db,[b])),0)
                row=db.execute('SELECT * FROM papers').fetchone()
                self.assertEqual(row['paper_id'],a.paper_id)
                self.assertEqual(row['venue'],'Example Journal')
                self.assertIn('openalex',row['source'])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM papers').fetchone()[0],1)

    def test_manual_pdf_download_then_skip_without_repeat(self):
        with TemporaryDirectory() as td:
            db=Path(td)/'data'/'papers.sqlite3'; cfg=dict(DEFAULTS)
            with connect(db) as con:
                save_papers(con,[paper()])
                def transfer(url,path,max_bytes):
                    path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_bytes(b'%PDF-1.4\n%%EOF\n')
                    return path.stat().st_size,'demo_sha'
                results=download_selected(con,['2610.01234'],cfg,db_path=db,transfer=transfer,logger=lambda *_:None)
                self.assertEqual(results[0]['status'],'downloaded')
                local=con.execute('SELECT local_file FROM papers').fetchone()[0]
                self.assertTrue(resolve_saved_file(db,local).exists())
                results=download_selected(con,['2610.01234'],cfg,db_path=db,transfer=lambda *x:1,logger=lambda *_:None)
                self.assertEqual(results[0]['status'],'skipped')

    def test_no_oa_pdf_and_failed_download_are_recorded(self):
        with TemporaryDirectory() as td:
            db=Path(td)/'data'/'papers.sqlite3'
            with connect(db) as con:
                a=paper(pid='openalex:W1',source='openalex',direct='',url='https://openalex.org/W1')
                b=paper()
                save_papers(con,[a,b])
                results=download_selected(con,[a.paper_id],DEFAULTS,db_path=db,logger=lambda *_:None)
                self.assertEqual(results[0]['status'],'unavailable')
                def fail(*args):raise ValueError('PDF 服务器连接失败')
                results=download_selected(con,[b.paper_id],DEFAULTS,db_path=db,transfer=fail,logger=lambda *_:None)
                self.assertEqual(results[0]['status'],'failed')
                self.assertFalse(list((Path(td)/'downloads').glob('*.part')))

    def test_download_path_and_url_security(self):
        with TemporaryDirectory() as td:
            db=Path(td)/'data'/'papers.sqlite3'
            self.assertIsNone(resolve_saved_file(db,'../../secret.pdf'))
            self.assertIsNone(resolve_saved_file(db,'/etc/passwd'))
        self.assertNotIn('/',safe_filename(paper(pid='../../some/evil')).split('_')[0])
        with self.assertRaises(ValueError):assert_public_https('http://127.0.0.1/secret')
        with self.assertRaises(ValueError):assert_public_https('https://127.0.0.1/private')

    def test_stream_pdf_magic_and_limit(self):
        with TemporaryDirectory() as td:
            path=Path(td)/'paper.pdf'
            class Stream(io.BytesIO):
                def __enter__(self):return self
                def __exit__(self,*args):self.close()
            with patch('paper_radar.downloads.manager.assert_public_https',return_value=None):
                n,hexdigest=stream_pdf('https://arxiv.org/pdf/1',path,1000,opener=lambda *a,**kw:Stream(b'%PDF-1.4\n...\n%%EOF'))
                self.assertGreater(n,10)
                with self.assertRaisesRegex(ValueError,'不是 PDF'):
                    stream_pdf('https://arxiv.org/pdf/1',path,1000,opener=lambda *a,**kw:Stream(b'<html>no pdf</html>'))
                self.assertFalse(path.with_suffix('.pdf.part').exists())
                with self.assertRaisesRegex(ValueError,'大小限制'):
                    stream_pdf('https://arxiv.org/pdf/1',path,20,opener=lambda *a,**kw:Stream(b'%PDF-'+b'X'*100))

    def test_http_download_endpoint_and_saved_pdf_serving(self):
        with TemporaryDirectory() as td:
            folder=Path(td);db_file=folder/'data'/'papers.sqlite3'
            with connect(db_file) as db:
                p=paper();p.local_file='saved.pdf';p.download_status='downloaded'
                save_papers(db,[p])
            out=folder/'downloads';out.mkdir();(out/'saved.pdf').write_bytes(b'%PDF-1.4\nDemo\n%%EOF')
            original_db=webapp.open_database
            @contextmanager
            def dbctx():
                conn=connect(db_file)
                try:yield conn
                finally:conn.close()
            class FakeRuntime:
                def start(self,mode,ids):
                    self.mode,self.ids=mode,ids
            fake=FakeRuntime()
            httpd=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
            httpd.token='test-token'
            thread=Thread(target=httpd.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{httpd.server_port}'
            try:
                with patch.object(webapp,'open_database',dbctx), patch.object(webapp,'DB_FILE',db_file),patch.object(webapp,'runtime',fake):
                    pdf=request.urlopen(base+'/api/pdf/2610.01234').read()
                    self.assertTrue(pdf.startswith(b'%PDF-'))
                    body=json.dumps({'mode':'download','ids':['2610.01234']}).encode()
                    req=request.Request(base+'/api/job', data=body, method='POST',headers={'X-Local-Token':'test-token','Content-Type':'application/json'})
                    result=json.load(request.urlopen(req))
                    self.assertTrue(result['ok'])
                    self.assertEqual(fake.ids,['2610.01234'])
                    bad=request.Request(base+'/api/job',data=json.dumps({'mode':'download','ids':['a']*21}).encode(),method='POST',headers={'X-Local-Token':'test-token','Content-Type':'application/json'})
                    with self.assertRaises(error.HTTPError) as ex:request.urlopen(bad)
                    self.assertEqual(ex.exception.code,400)
            finally:
                httpd.shutdown();httpd.server_close();thread.join(timeout=2)

if __name__ == '__main__':unittest.main()
