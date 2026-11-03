# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Read-only Zotero folder integration and topic-based paper recommendations."""
from __future__ import annotations
import io
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib import request, error
from http.server import ThreadingHTTPServer
from threading import Thread
import unittest

from paper_radar.zotero import ZoteroReader, build_profile, extract_keywords, SimilarityRanker
from paper_radar.zotero.sqlite_reader import read_only_database, ZoteroDatabaseError
from paper_radar.zotero.local_api import LocalAPIError
from paper_radar.zotero.models import Reference
from paper_radar.core import DEFAULTS, validate_config, run_cycle, connect, get_recent
from paper_radar.models import Paper
from paper_radar import webapp


def make_zotero_sqlite(folder:Path)->Path:
    path=folder/'zotero.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE collections (collectionID INTEGER PRIMARY KEY,collectionName TEXT,parentCollectionID INT,libraryID INT,key TEXT);
        CREATE TABLE collectionItems (collectionID INT,itemID INT,orderIndex INT DEFAULT 0);
        CREATE TABLE items (itemID INTEGER PRIMARY KEY,key TEXT,itemTypeID INT);
        CREATE TABLE itemTypes (itemTypeID INTEGER,typeName TEXT);
        CREATE TABLE itemData (itemID INT,fieldID INT,valueID INT);
        CREATE TABLE itemDataValues (valueID INT,value TEXT);
        CREATE TABLE fields (fieldID INT,fieldName TEXT);
        CREATE TABLE itemTags (itemID INT,tagID INT);
        CREATE TABLE tags (tagID INT,name TEXT);
        CREATE TABLE deletedItems (itemID INT);
        CREATE TABLE deletedCollections (collectionID INT);
        CREATE TABLE creators (creatorID INT,firstName TEXT,lastName TEXT);
        CREATE TABLE itemCreators (itemID INT,creatorID INT,creatorTypeID INT,orderIndex INT);
        INSERT INTO collections VALUES(1,'Neural Operators',NULL,1,'AAA12345');
        INSERT INTO collections VALUES(2,'Complex Geometry',1,1,'BBB12345');
        INSERT INTO collections VALUES(3,'Other Topics',NULL,1,'CCC12345');
        INSERT INTO collections VALUES(4,'Trash',NULL,1,'DDD12345');
        INSERT INTO deletedCollections VALUES (4);
        INSERT INTO itemTypes VALUES (1,'journalArticle'),(2,'attachment');
        INSERT INTO items VALUES (1,'ITEMA',1),(2,'ITEMB',1),(3,'ITEMC',1),(4,'PDFATT',2),(5,'DELETED',1);
        INSERT INTO deletedItems VALUES (5);
        INSERT INTO collectionItems VALUES (1,1,0),(2,2,0),(3,3,0),(1,4,0),(1,5,0);
        INSERT INTO fields VALUES (1,'title'),(2,'abstractNote'),(3,'date'),(4,'DOI');
        INSERT INTO itemDataValues VALUES
           (1,'Fourier Neural Operators for PDE Simulation'),(2,'Neural operator learning for partial differential equations and simulation'),
           (3,'2025'),(4,'10.1000/foo'),
           (5,'Geometric Neural Operators on Complex Meshes'),(6,'Neural operator learning for irregular geometry mesh simulation'),
           (7,'2026'),(8,'10.1000/bar'),
           (9,'Soil Mechanics in Bridge Engineering'),(10,'Bridge mechanics and soil behavior'),
           (11,'Attachment PDF'),(12,'Deleted Article');
        INSERT INTO itemData VALUES(1,1,1),(1,2,2),(1,3,3),(1,4,4),
          (2,1,5),(2,2,6),(2,3,7),(2,4,8),(3,1,9),(3,2,10),(4,1,11),(5,1,12);
        INSERT INTO tags VALUES(1,'neural operator'),(2,'operator learning'),(3,'irregular mesh');
        INSERT INTO itemTags VALUES(1,1),(1,2),(2,1),(2,3);
        INSERT INTO creators VALUES(1,'Example','Author');
        INSERT INTO itemCreators VALUES(1,1,1,0);
        ''')
    return path


def fake_paper(pid, title, abstract):
    now=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return Paper(pid,title,abstract,'A. Writer',now,now,'cs.LG',
        f'https://arxiv.org/abs/{pid}',f'https://arxiv.org/pdf/{pid}')


class ZoteroReaderTests(unittest.TestCase):
    def test_local_sqlite_folder_tree_excludes_trash_attachments_deleted(self):
        with TemporaryDirectory() as td:
            db=make_zotero_sqlite(Path(td))
            reader=ZoteroReader('sqlite',str(db))
            col=reader.collections()
            self.assertEqual(len(col),3)
            self.assertEqual({x.path for x in col}, {'Neural Operators','Neural Operators / Complex Geometry','Other Topics'})
            refs,total=reader.references('AAA12345',True)
            self.assertEqual(len(refs),2)
            self.assertEqual({r.title for r in refs}, {'Fourier Neural Operators for PDE Simulation','Geometric Neural Operators on Complex Meshes'})
            self.assertEqual(len(reader.references('AAA12345',False)[0]),1)
            self.assertIn('neural operator',next(r for r in refs if 'Fourier' in r.title).tags)
            self.assertIn('Example Author',next(r for r in refs if 'Fourier' in r.title).authors)
            with self.assertRaisesRegex(ZoteroDatabaseError,'不存在'):
                reader.references('UNKNOWN')

    def test_read_only_connection_rejects_writes(self):
        with TemporaryDirectory() as td:
            db=make_zotero_sqlite(Path(td))
            before=db.read_bytes()
            with read_only_database(str(db)) as (con,tables):
                with self.assertRaises(sqlite3.OperationalError):
                    con.execute("DELETE FROM collections")
            self.assertEqual(before,db.read_bytes())
            with self.assertRaises(ZoteroDatabaseError):
                ZoteroReader('sqlite',str(Path(td)/'unknown.sqlite')).collections()

    def test_extract_phrases_and_rank_references(self):
        with TemporaryDirectory() as td:
            db=make_zotero_sqlite(Path(td))
            profile=build_profile('sqlite',str(db),'AAA12345',True,8)
            self.assertEqual(len(profile.references),2)
            self.assertTrue(any('neural operator' in k for k in profile.keywords),profile.keywords)
            ranker=SimilarityRanker(profile.references)
            relevant=fake_paper('2601.11111','Neural Operator on Irregular Meshes','operator learning simulation of PDEs')
            irrelevant=fake_paper('2601.22222','Soil Carbon Emissions','Environmental studies and coral reefs')
            self.assertGreater(ranker.score(relevant),ranker.score(irrelevant))

    def test_profile_integration_generates_report_and_uses_reference_ranking(self):
        with TemporaryDirectory() as td:
            folder=Path(td)
            zot=make_zotero_sqlite(folder)
            cfg=dict(DEFAULTS)
            cfg.update({'zotero_enabled':True,'zotero_mode':'sqlite','zotero_db_path':str(zot),
              'zotero_collection_key':'AAA12345','zotero_include_children':True,
              'zotero_refresh_keywords':True,'output_dir':str(folder/'reports'),
              'keywords':['old irrelevant topic'],'lookback_days':7,'categories':['cs.LG']})
            items=[fake_paper('2601.12345','Neural Operator Learning on Complex Meshes','operator learning solutions'),
              fake_paper('2601.99999','Gravitational Waves','observations of space and stars')]
            db=folder/'local'/'papers.sqlite3'
            result=run_cycle(cfg,db_path=db,fetcher=lambda *args,**kw:items,logger=lambda *_:None)
            self.assertTrue(result['weekly'].exists())
            self.assertGreaterEqual(result['selected'],1)
            from docx import Document
            text=' '.join(p.text for p in Document(result['weekly']).paragraphs)
            self.assertIn('Neural Operators',text)
            self.assertIn('Neural Operator Learning',text)

    def test_missing_local_api_and_sqlite_auto_fallback(self):
        with TemporaryDirectory() as td:
            db=make_zotero_sqlite(Path(td))
            with patch('paper_radar.zotero.connector.detect_database',return_value=str(db)):
                with patch('paper_radar.zotero.local_api.list_collections',side_effect=LocalAPIError('not running')):
                    reader=ZoteroReader('auto')
                    self.assertEqual(len(reader.collections()),3)
                    self.assertEqual(reader.backend,'sqlite')
            with patch('paper_radar.zotero.local_api.list_collections',side_effect=LocalAPIError('not running')):
                with self.assertRaisesRegex(Exception,'not running'):
                    ZoteroReader('api').collections()

    def test_api_response_parses_collections_and_references(self):
        from paper_radar.zotero import local_api
        with patch.object(local_api,'_fetch') as fake:
            fake.side_effect=[
              [{'key':'AAA12345','data':{'name':'Neural','parentCollection':False}},
               {'key':'BBB12345','data':{'name':'Meshes','parentCollection':'AAA12345'}}],
              [{'key':'AAA12345','data':{'name':'Neural','parentCollection':False}},
               {'key':'BBB12345','data':{'name':'Meshes','parentCollection':'AAA12345'}}],
              [{'key':'ITEMA','data':{'itemType':'journalArticle','title':'Neural Operator Learning',
                         'abstractNote':'learning PDE operators','tags':[{'tag':'neural operator'}],
                         'creators':[{'firstName':'Test','lastName':'Author'}]}}],
              [{'key':'ITEMB','data':{'itemType':'preprint','title':'Operator Learning on Grids',
                         'abstractNote':'geometry aware operator learning','tags':[]}}],
            ]
            cols=local_api.list_collections()
            self.assertEqual(cols[1].path,'Neural / Meshes')
            refs,total=local_api.get_references('AAA12345')
            self.assertEqual(len(refs),2)
            self.assertEqual(total,2)
            self.assertEqual(refs[0].tags,['neural operator'])

    def test_web_api_collection_preview_and_config_apply(self):
        with TemporaryDirectory() as td:
            folder=Path(td)
            db=make_zotero_sqlite(folder)
            cfgfile=folder/'settings.json'
            from paper_radar.core import read_config,save_config
            from paper_radar.zotero import connector
            httpd=ThreadingHTTPServer(('127.0.0.1',0),webapp.Handler)
            httpd.token='demo-token'
            t=Thread(target=httpd.serve_forever,daemon=True);t.start()
            def send(path,data):
                req=request.Request(f'http://127.0.0.1:{httpd.server_port}'+path,
                    method='POST',data=json.dumps(data).encode(),headers={
                    'X-Local-Token':'demo-token','Content-Type':'application/json'})
                return json.load(request.urlopen(req))
            try:
                # Patching global config functions only, without requiring user secrets.
                with patch.object(webapp,'read_config',lambda:read_config(cfgfile)), \
                     patch.object(webapp,'save_config',lambda cfg:save_config(cfg,cfgfile)):
                    payload={'mode':'sqlite','db_path':str(db),'collection_key':'AAA12345','include_children':True}
                    collections=send('/api/zotero/collections',payload)
                    self.assertEqual(collections['count'],3)
                    preview=send('/api/zotero/preview',payload)
                    self.assertEqual(preview['count'],2)
                    self.assertIn('neural operator',' '.join(preview['keywords']))
                    result=send('/api/zotero/apply',{**payload,'keywords':preview['keywords'],'refresh_keywords':True})
                    self.assertTrue(result['ok'])
                    cfg=read_config(cfgfile)
                    self.assertTrue(cfg['zotero_enabled'])
                    self.assertEqual(cfg['zotero_collection_key'],'AAA12345')
                    send('/api/zotero/disable',{})
                    self.assertFalse(read_config(cfgfile)['zotero_enabled'])
            finally:
                httpd.shutdown();httpd.server_close();t.join(2)

if __name__=='__main__':unittest.main()


def test_keyword_extraction_keeps_domain_phrases():
    from paper_radar.zotero.keywords import extract_keywords
    from paper_radar.zotero.models import Reference
    refs = [
        Reference('X1', 'Neural Operator for PDE Simulation', 'Neural operator methods on irregular meshes', [], '', '', ''),
        Reference('X2', 'Learning Neural Operators for Irregular Meshes', 'Neural operator methods and geometry adaptation', [], '', '', ''),
        Reference('X3', 'Physics-Informed Neural Operator', 'Neural operator learning in computational physics', [], '', '', ''),
    ]
    phrases = extract_keywords(refs, 15)
    assert 'neural operator' in phrases, phrases
