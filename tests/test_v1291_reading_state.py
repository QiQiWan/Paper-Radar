# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.29.1 bookmarkable paper/page/report state, contextual help and route safety."""
from __future__ import annotations

from pathlib import Path
from threading import Thread
from http.server import ThreadingHTTPServer
from zipfile import ZipFile
import http.client
import json
import subprocess

import pytest

from paper_radar.help_pages import markdown_html, render_guide

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'paper_radar' / 'static'


def _node(code: str) -> dict:
    stub = r'''
const fs=require('fs'),vm=require('vm');
const fake={location:{search:'',pathname:'/app/library',origin:'https://papers.example',href:'https://papers.example/app/library'}};
const visits=['/app/library'];let current=0;
function updateLocation(url){const u=new URL(url,fake.location.origin);fake.location.pathname=u.pathname;fake.location.search=u.search;fake.location.href=u.href;fake.saved=u.pathname+u.search;}
fake.history={pushState(a,b,url){visits.splice(++current);visits.push(url);fake.pushed=(fake.pushed||0)+1;updateLocation(url);},
 replaceState(a,b,url){visits[current]=url;fake.replaced=(fake.replaced||0)+1;updateLocation(url);},
 back(){if(current>0){current--;updateLocation(visits[current]);}}};
const context={window:fake,navigator:{clipboard:{writeText(x){fake.copied=x;return Promise.resolve()}}},
 URL:URL,URLSearchParams:URLSearchParams,console:console};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
(async()=>{
'''
    finish = r'''
console.log(JSON.stringify(context.result));
})().catch(e=>{console.error(e.stack);process.exit(1)});
'''
    result = subprocess.run(['node', '-e', stub + code + finish, str(STATIC / 'reading_links.js')],
                            capture_output=True, text=True, timeout=6, check=True)
    return json.loads(result.stdout.strip())


def test_local_paper_link_preserves_filters_pdf_page_and_topic():
    result = _node("context.result={url:fake.PRReading.link('local','library',{topic:'work-1',q:'neural operator',filter:'starred',sort:'recent',scope:'all',group:'source',paper:'crossref:10.1000/abc',page:19})};")
    url = result['url']
    assert url.startswith('/app/library?')
    from urllib.parse import urlsplit,parse_qs
    query=parse_qs(urlsplit(url).query)
    assert {k:v[0] for k,v in query.items()} == {
        'topic':'work-1','q':'neural operator','filter':'starred','sort':'recent',
        'scope':'all','group':'source','paper':'crossref:10.1000/abc','page':'19'}


def test_public_report_link_has_immutable_report_key_and_progress():
    result = _node("context.result={url:fake.PRReading.link('public','reports',{topic:'topic-2',report:'report_a9f',pos:6842})};")
    assert result['url']=='/reports?topic=topic-2&report=report_a9f&pos=6842'


def test_public_library_selected_paper_and_search_context():
    result = _node("context.result={url:fake.PRReading.link('public','library',{topic:'topic-2',q:'FNO',filter:'to_read',sort:'title',focus:'arxiv:123',paper:'crossref:10.3333/a',page:11})};")
    assert 'q=FNO' in result['url']
    assert 'filter=to_read' in result['url']
    assert 'focus=arxiv%3A123' in result['url']
    assert 'page=11' in result['url']


def test_only_canonical_state_and_bounds_are_written():
    result = _node(r"""
const R=fake.PRReading;
context.result={
 route:R.link('public','library',{topic:'../../secret',q:'x',filter:'destroy',paper:'a',page:999999,apiKey:'secret',password:'secret'}),
 invalidReport:R.link('local','reports',{report:'../../token.env',pos:9000}),
 bounded:R.clean('pos','-100'), blank:R.clean('paper','\x00bad'),
 unknown:R.link('local','whatever',{q:'secret'}),
 help:R.help('local','library')};
""")
    assert 'secret' not in result['route']
    assert 'apiKey' not in result['route'] and 'password' not in result['route']
    assert 'filter' not in result['route']
    assert 'page=9999' in result['route']
    assert 'report=' not in result['invalidReport']
    assert result['bounded']=='0'
    assert result['blank']==''
    assert result['unknown']=='/app/dashboard'
    assert result['help']=='/guide/triage'


def test_shared_url_writer_avoids_duplicates_and_supports_history():
    result = _node(r"""
const R=fake.PRReading;
R.write('/app/library?q=x',{push:true});
R.write('/app/library?q=x',{push:true});
R.write('/app/library?q=y');
context.result={url:fake.saved,pushed:fake.pushed||0,replaced:fake.replaced||0};
""")
    assert result['pushed'] == 1 and result['replaced']==1
    assert result['url']=='/app/library?q=y'


def test_history_back_reestablishes_previous_page_and_filters():
    result = _node(r"""
const R=fake.PRReading;
R.write(R.link('local','library',{q:'physics',filter:'starred'}),{push:true});
R.write(R.link('local','reports',{report:'notes.md',pos:2430}),{push:true});
fake.history.back();
context.result={path:fake.location.pathname,q:R.get('q'),filter:R.get('filter'),report:R.get('report')};
""")
    assert result=={'path':'/app/library','q':'physics','filter':'starred','report':''}


def test_progress_normalization_is_resolution_independent():
    result = _node(r"""
const R=fake.PRReading;
let e={scrollTop:450,scrollHeight:1200,clientHeight:600};
const progress=R.progress(e);e={scrollTop:0,scrollHeight:2400,clientHeight:600};
R.restoreProgress(e,progress);context.result={progress,restored:e.scrollTop};
""")
    assert result == {'progress':7500,'restored':1350}


def test_help_anchor_safe_and_images_present():
    content=render_guide('reading')
    assert content and b'id="library"' in content and b'id="pdf"' in content
    assert b'id="reports"' in content and b'<figure' in content
    assert b'<table>' in content and b'context' not in content.lower()[:20]
    assert '<h2 id="reports">' in markdown_html('## {#reports} Reading report')
    assert '<script>' not in markdown_html('## {#onload-alert} <script>alert(1)</script>')


def test_local_and_public_have_contextual_help_and_share_controls():
    local=(STATIC/'index.html').read_text(encoding='utf-8')
    public=(STATIC/'portal.html').read_text(encoding='utf-8')
    for key in ['contextHelpLocal','sharePageLocal','shareFulltextLocal','shareMarkdownLocal',
                'fulltextPdfPage','fulltextPagePrev','fulltextPageNext']:
        assert f'id="{key}"' in local
    for key in ['contextHelpPortal','sharePagePortal']:
        assert f'id="{key}"' in public
    assert local.index('src="/reading_links.js"') < local.index('src="/app.js"')
    assert public.index('src="/reading_links.js"') < public.index('src="/portal.js"')
    # Absolute resources are required for a hard-refresh at /app/library, not only at /.
    assert 'href="/app.css"' in local
    assert 'src="/app.js"' in local
    assert 'src="/reading_links.js"' in local



def test_route_reading_implementation_keeps_browser_back_and_auth_guards():
    local=(STATIC/'app.js').read_text(encoding='utf-8')
    public=(STATIC/'portal.js').read_text(encoding='utf-8')
    for text in ['restoreLocalRoute','popstate','moveLocalPdfPage','syncLocalLink','ignoredLocalClose']:
        assert text in local
    for text in ['restoreReportPosition','applyRoute','popstate','previewPaper','syncPublicLink','portalRestoring']:
        assert text in public
    assert '/api/public/reports/view' in public and '/api/public/fulltext' in public


def _get(port: int,path: str):
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=6)
    conn.request('GET',path,headers={'Host':f'127.0.0.1:{port}'})
    r=conn.getresponse();out=(r.status,r.getheader('Content-Type',''),r.read());conn.close();return out


def test_local_deep_links_and_shared_resource_are_served():
    from paper_radar.webapp import Handler
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.token='test-token'
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        for path in ['/app/library?q=FNO&paper=arxiv:10&page=4',
                     '/app/reports?report=recent.md&pos=3000']:
            status,_,body=_get(server.server_port,path)
            assert status==200 and b'reading_links.js' in body
        status,kind,body=_get(server.server_port,'/reading_links.js')
        assert status==200 and b'PRReading' in body
        # A hard-refresh on /app/library must still fetch absolute static paths.
        for static in ['/app.css','/app.js']:
            status,kind,body=_get(server.server_port,static)
            assert status==200 and len(body)>200, static
        status,kind,body=_get(server.server_port,'/guide/reading')
        assert status==200 and b'id="pdf"' in body
        assert _get(server.server_port,'/guide/not-real')[0]==404
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_public_deep_links_still_require_session(tmp_path):
    from paper_radar.public_portal import Portal,make_server
    service=Portal(tmp_path/'portal',embedded_workers=False)
    server=make_server('127.0.0.1',0,service)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        assert _get(server.server_port,'/library?topic=default&filter=starred&paper=arxiv:123&page=4')[0]==200
        assert _get(server.server_port,'/reports?topic=default&report=foo&pos=1500')[0]==200
        assert _get(server.server_port,'/reading_links.js')[0]==200
        assert _get(server.server_port,'/api/public/fulltext?topic_id=default&paper_id=x&page=1')[0]==401
        assert _get(server.server_port,'/api/public/reports/view?id=foo')[0]==401
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_javascript_syntax_including_shared_helper():
    for filename in ['reading_links.js','app.js','portal.js']:
        subprocess.run(['node','--check',str(STATIC/filename)],check=True,capture_output=True,timeout=10)
