"""v1.37/38/39 deterministic offline acceptance; no account keys or remote calls."""
from __future__ import annotations
import hashlib
import io
import json
from datetime import datetime,timezone
from pathlib import Path
from urllib import error

import pytest

from paper_radar.sources.openreview import parse_note,OpenReviewSource
from paper_radar.citation_formats import export,bibtex,ris
from paper_radar.zotero.web_sync import ZoteroWebSync,ZoteroConflict,ZoteroSyncError,fingerprint
from paper_radar.pdf_geometry import locate,page_tables,preview_png


class MockResponse:
    def __init__(self, payload, headers=None, status=200):
        self.payload=json.dumps(payload).encode()
        self.headers=headers or {}
        self.status=status
    def __enter__(self): return self
    def __exit__(self,*args): return None
    def read(self,n=-1):return self.payload if n<0 else self.payload[:n]


def _note(id='abcdefghijkl',title='Physics informed operator learning',day='2026-11-08'):
    ts=int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()*1000)
    return {'id':id,'content':{'title':{'value':title},'abstract':{'value':'Generalization on meshes'},
        'authors':{'value':['A Researcher','B Researcher']},'venue':{'value':'ICLR 2026'},'pdf':{'value':'/pdf'}},
        'cdate':ts,'mdate':ts}


def test_openreview_public_note_parsing_and_versions():
    p=parse_note(_note())
    assert p.source=='openreview' and p.paper_id=='openreview:abcdefghijkl'
    assert p.published=='2026-11-08' and p.updated=='2026-11-08'
    assert p.pdf_url=='https://openreview.net/pdf?id=abcdefghijkl'
    assert p.venue=='ICLR 2026'
    assert parse_note({**_note(), 'replyto':'parent'}) is None
    assert parse_note({**_note(), 'id':'../../etc'}) is None
    assert parse_note({**_note(), 'content':{'abstract':{'value':'x'}}}) is None


def test_openreview_pagination_and_date_window(monkeypatch):
    from paper_radar.sources import openreview
    urls=[]
    def fake(req,timeout):
        urls.append(req.full_url)
        from urllib.parse import parse_qs,urlsplit
        params=parse_qs(urlsplit(req.full_url).query)
        assert params['source']==['forum']
        if params['offset']==['0']:
            return MockResponse({'notes':[_note(id=str(i).zfill(12)) for i in range(100)]})
        return MockResponse({'notes':[_note(id='last_page_',title='unknown'),_note(id='valid23456789')]})
    monkeypatch.setattr(openreview.request,'urlopen',fake)
    rows=OpenReviewSource().fetch({'keywords':['operator learning'],'max_results':150,'lookback_days':7},
        now=datetime(2026,11,8,tzinfo=timezone.utc))
    assert len(rows)==102 and len(urls)==2
    assert 'offset=100' in urls[1]


def test_openreview_query_validation_and_temporal_filter(monkeypatch):
    from paper_radar.sources import openreview
    monkeypatch.setattr(openreview.request,'urlopen',lambda *a,**k: MockResponse({'notes':[
        _note(day='2020-01-01'),_note(id='fresh2345678')]}))
    rows=OpenReviewSource().fetch({'keywords':['operator'],'max_results':20,'lookback_days':7},
        now=datetime(2026,11,8,tzinfo=timezone.utc))
    assert len(rows)==1
    with pytest.raises(ValueError):
        OpenReviewSource().fetch({'keywords':['x'],'openreview_invitation':'https://bad.example.com','max_results':2},
            now=datetime(2026,11,8,tzinfo=timezone.utc))


def test_citation_formats_sanitize_metadata_and_stable_keys():
    records=[dict(paper_id='a',title='A title\nER  - malicious',authors='A Smith, B Doe',
         published='2026-01-01',doi='10.1/test',url='https://doi.org/10.1/test',venue='Nature',abstract='x'),
         dict(paper_id='b',title='A title\nER  - malicious',authors='A Smith, B Doe',published='2026-01-01')]
    b=bibtex(records).decode();r=ris(records).decode()
    assert '@article{Smith2026AtitleERmalicious,' in b and '@article{Smith2026AtitleERmalicious2,' in b
    assert r.count('ER  - ') == 2  # forged line was flattened
    assert 'ER  - malicious\n' not in r
    assert 'DO  - 10.1/test' in r
    assert export(records,'bib')[2].endswith('.bib')
    assert export(records,'ris')[2].endswith('.ris')
    with pytest.raises(ValueError):export(records,'xml')
    with pytest.raises(ValueError):bibtex([{}]*2001)


def test_zotero_preview_and_explicit_versioned_note_write():
    calls=[]
    parent='ABCDEFGH'
    def fake(req,timeout):
        calls.append((req.get_method(),req.full_url,dict(req.headers),req.data))
        if '?q=' in req.full_url:
            return MockResponse([{'key':parent,'data':{'key':parent,'title':'Matching article','DOI':'10.1234/ABC'}}],
                                {'Last-Modified-Version':'42'})
        if '?limit=1' in req.full_url:
            return MockResponse([] ,{'Last-Modified-Version':'42'})
        assert req.get_method()=='POST'
        return MockResponse({'successful':{'0':{'key':'KLMNOPQR'}}})
    c=ZoteroWebSync(user_id='12345',api_key='abcdefghijklmnopqrst',opener=fake)
    paper={'paper_id':'x','title':'Title <script>','doi':'10.1234/abc','url':'https://x'}
    note='Reviewed & confirmed\nExperiment score 0.3'
    preview=c.preview(paper,note)
    assert preview['zotero_parent']==parent and preview['library_version']==42
    out=c.confirm(paper,note,parent=parent,version=42,expected_fingerprint=preview['fingerprint'])
    assert out['ok'] and out['zotero_note_key']=='KLMNOPQR'
    assert calls[-1][2]['If-unmodified-since-version']=='42'
    assert '<script>' not in calls[-1][3].decode()
    with pytest.raises(ZoteroConflict):
        c.confirm({**paper,'title':'changed'},note,parent=parent,version=42,expected_fingerprint=preview['fingerprint'])


def test_zotero_fail_closed_and_conflict():
    with pytest.raises(ZoteroSyncError):ZoteroWebSync(user_id='a',api_key='abcdefghijklm')
    def fake(req,timeout):
        raise error.HTTPError(req.full_url,412,'Conflict',{},None)
    c=ZoteroWebSync(user_id='1234',api_key='abcdefghijklm',opener=fake)
    with pytest.raises(ZoteroConflict):
        c.confirm({'paper_id':'x','title':'t'},'n',parent='ABCDEFGH',version=1,
                  expected_fingerprint=fingerprint({'paper_id':'x','title':'t'},'n'))


def test_pdf_geometry_sha_mismatch_and_plain_text(tmp_path):
    fitz=pytest.importorskip('fitz')
    f=tmp_path/'paper.pdf'
    doc=fitz.open();p=doc.new_page(width=595,height=842)
    p.insert_text((70,120),'Operator learning improves accuracy on this benchmark.')
    doc.save(str(f));doc.close()
    sha=hashlib.sha256(f.read_bytes()).hexdigest()
    found=locate(f,sha,1,'Operator learning')
    assert found['matched'] and len(found['boxes'])>=1
    assert all(0 <= rect['x'] <= 1 and 0 <= rect['y'] <= 1 for rect in found['boxes'])
    png=preview_png(f,sha,1,'Operator learning')
    assert png.startswith(b'\x89PNG')
    assert page_tables(f,sha,1)['tables']==[]
    with pytest.raises(ValueError,match='已更新'):
        locate(f,'0'*64,1,'Operator learning')
    with pytest.raises(ValueError,match='页码'):
        locate(f,sha,0,'Operator learning')
    with pytest.raises(ValueError,match='定位'):
        preview_png(f,sha,1,'No matching quote in this PDF')


def test_registered_sources_and_portal_source_isolation():
    from paper_radar.sources import available_sources
    from paper_radar.portal_discovery import validate_sources
    assert 'openreview' in {s['name'] for s in available_sources()}
    assert validate_sources(['crossref','openreview']) == ['crossref','openreview']
    with pytest.raises(ValueError):validate_sources(['openreview','openreview'])
    with pytest.raises(ValueError):validate_sources(['privateurl'])


def test_zotero_manual_import_preview_confirm_and_conflict():
    from paper_radar.zotero.web_sync import ZoteroWebSync, ZoteroConflict
    parent='ABCDEFGH';child='KLMNOPQR'
    note_html='<h2>Paper-Radar 阅读笔记</h2><p><strong>Article Title</strong></p><p>DOI: 10.1234/abc</p><p>My note &amp; results<br/>Check units</p>'
    version=[7]
    calls=[]
    def fake(req,timeout):
        calls.append(req.full_url)
        if '?q=' in req.full_url:
            return MockResponse([{'key':parent,'data':{'key':parent,'title':'Matching article',
                'DOI':'10.1234/abc'}}])
        if '?limit=1' in req.full_url:
            return MockResponse([] ,{'Last-Modified-Version':'42'})
        data={'itemType':'note','parentItem':parent,'note':note_html,'version':version[0]}
        if '/children?' in req.full_url:
            return MockResponse([{'key':child,'data':data}])
        if req.full_url.endswith('/'+child):
            return MockResponse({'key':child,'data':data})
        raise AssertionError(req.full_url)
    paper={'paper_id':'p','title':'T','doi':'10.1234/abc','url':'https://example.com'}
    c=ZoteroWebSync(user_id='12345',api_key='abcdefghijklmnopqrst',opener=fake)
    preview=c.preview_import(paper)
    assert preview['zotero_note_key']==child
    assert preview['length']==len('My note & results\nCheck units')
    assert c.confirm_import(paper,parent=parent,note_key=child,
            expected_fingerprint=preview['remote_fingerprint'])=='My note & results\nCheck units'
    version[0]=8
    with pytest.raises(ZoteroConflict,match='已变更'):
        c.confirm_import(paper,parent=parent,note_key=child,
            expected_fingerprint=preview['remote_fingerprint'])


def test_zotero_import_rejects_other_notes_and_multiple():
    from paper_radar.zotero.web_sync import _import_text,ZoteroSyncError
    with pytest.raises(ZoteroSyncError,match='仅支持'):
        _import_text('<h2>Personal notes</h2><p>Hello</p><p>Two</p><p>Three</p>')
    with pytest.raises(ZoteroSyncError,match='缺少'):
        _import_text('<h2>Paper-Radar 阅读笔记</h2><p>A</p>')
