# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Offline acceptance tests for quota handling, recall and stage diagnostics."""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import json
import sys

import pytest

from paper_radar.models import Paper
from paper_radar.sources.rate_limit import safe_quota_headers, backoff_for_429, retry_after_seconds
from paper_radar.sources.failures import SourceQueryError
from paper_radar.sources.openalex import OpenAlexSource
from paper_radar.sources.openalex_semantic import OpenAlexSemanticSource
from paper_radar.sources.openreview import OpenReviewSource
from paper_radar.sources.base import fetch_enabled
from paper_radar.sources.retrieval_trace import RetrievalTrace, ACTIVE_TRACE, read_trace
from paper_radar.task_outcome import retrieval_outcome

NOW = datetime(2026,11,8,tzinfo=timezone.utc)


def paper(pid, doi='', source='crossref', title='Neural operator'):
    return Paper(paper_id=pid,title=title,abstract='Neural operator learning for meshes',
                 authors='A',published='2026-11-07',updated='2026-11-07',
                 category='article',url='https://example.org/p',pdf_url='',doi=doi,source=source)


def config(**kw):
    return {'keywords':['neural operator'],'lookback_days':7,'max_results':10,**kw}


def test_openalex_quota_headers_safe_and_retry_after_date():
    headers={'X-RateLimit-Remaining':'0','X-RateLimit-Reset':'3600','Retry-After':'7200',
             'Authorization':'Bearer PRIVATE','X-Private':'SENSITIVE'}
    output=safe_quota_headers(headers,status=429)
    assert output=={'rate_limit_remaining':0.0,'rate_limit_reset_seconds':3600,
                    'retry_after_seconds':7200,'rate_limit_scope':'daily_budget'}
    assert 'PRIVATE' not in repr(output)
    assert backoff_for_429(headers,attempt=0,remaining_seconds=35) is None
    assert retry_after_seconds('Sun, 08 Nov 2026 12:01:03 GMT',now=datetime(2026,11,8,12,0,tzinfo=timezone.utc)) == 63


def test_openalex_short_429_retries_once_then_returns(monkeypatch):
    from paper_radar.sources import openalex
    calls=[];delays=[]
    def get(req,timeout):
        calls.append(req.full_url)
        if len(calls)==1:
            raise HTTPError(req.full_url,429,'rate limit',{'Retry-After':'1','X-RateLimit-Remaining':'50'},None)
        return BytesIO(json.dumps({'results':[{'id':'https://openalex.org/W123',
                     'title':'Neural operator','publication_date':'2026-11-07'}]}).encode())
    monkeypatch.setattr(openalex.request,'urlopen',get)
    monkeypatch.setattr(openalex.time,'sleep',lambda sec:delays.append(sec))
    rows=OpenAlexSource().fetch(config(),now=NOW,logger=lambda *_:None)
    assert len(rows)==1 and len(calls)==2 and delays==[1.0]


def test_openalex_daily_quota_429_stops_immediately(monkeypatch):
    from paper_radar.sources import openalex
    urls=[]
    def fail(req,timeout):
        urls.append(req.full_url)
        raise HTTPError(req.full_url,429,'rate limit',
                        {'Retry-After':'500','X-RateLimit-Remaining':'0'},None)
    monkeypatch.setattr(openalex.request,'urlopen',fail)
    with pytest.raises(SourceQueryError) as exc:
        OpenAlexSource().fetch(config(),now=NOW,logger=lambda *_:None)
    assert exc.value.code=='RATE_LIMIT' and len(urls)==1


def test_semantic_uses_supported_year_filter_and_local_date_gate(monkeypatch):
    from paper_radar.sources import openalex_semantic
    calls=[]
    def fetch(req,timeout):
        qs=parse_qs(urlsplit(req.full_url).query)
        calls.append(qs)
        return BytesIO(json.dumps({'results':[
            {'id':'https://openalex.org/W12','title':'Neural operator recent',
             'publication_date':'2026-11-07'},
            {'id':'https://openalex.org/W13','title':'Neural operator stale',
             'publication_date':'2026-01-01'}]}).encode())
    monkeypatch.setattr(openalex_semantic.request,'urlopen',fetch)
    rows=OpenAlexSemanticSource().fetch(config(research_goal='neural operator for physical equations'),
        now=NOW,logger=lambda *_:None)
    assert [r.paper_id for r in rows]==['openalex:W12']
    assert calls[0]['filter']==['publication_year:2026']
    assert 'from_publication_date' not in str(calls)


def test_semantic_year_crossing_new_year(monkeypatch):
    from paper_radar.sources import openalex_semantic
    urls=[]
    def fetch(req,timeout):
        urls.append(req.full_url)
        return BytesIO(b'{"results":[]}')
    monkeypatch.setattr(openalex_semantic.request,'urlopen',fetch)
    OpenAlexSemanticSource().fetch(config(research_goal='neural operator for finite elements',lookback_days=20),
        now=datetime(2026,1,8,tzinfo=timezone.utc),logger=lambda *_:None)
    assert parse_qs(urlsplit(urls[0]).query)['filter']==['publication_year:2025|2026']


def test_openreview_broad_fallback_validates_original_phrase(monkeypatch):
    from paper_radar.sources import openreview
    calls=[]
    class Fake(BytesIO):
        def __enter__(self):return self
        def __exit__(self,*a):return False
    def response(req,timeout):
        term=parse_qs(urlsplit(req.full_url).query)['term'][0]
        calls.append(term)
        if len(calls)==1:return Fake(b'{"notes":[]}')
        assert term=='operator'
        return Fake(json.dumps({'notes':[
            {'id':'freshtitle12','content':{'title':{'value':'Neural operator benchmark'}},
             'cdate':1794009600000,'mdate':1794009600000},
            {'id':'unrelated12','content':{'title':{'value':'Stochastic operator methods'}},
             'cdate':1794009600000,'mdate':1794009600000}
        ]}).encode())
    monkeypatch.setattr(openreview.request,'urlopen',response)
    rows=OpenReviewSource().fetch(config(),now=NOW,logger=lambda *_:None)
    assert len(rows)==1 and rows.stats['remote_hits']==2
    assert calls==['neural operator','operator']


def test_openreview_remote_hits_explain_zero_due_to_date(monkeypatch):
    from paper_radar.sources import openreview
    def stub(req,timeout):
        return BytesIO(json.dumps({'notes':[{'id':'oldpaper123',
          'content':{'title':{'value':'Neural operator'}},
          'cdate':1609459200000,'mdate':1609459200000}]}).encode())
    monkeypatch.setattr(openreview.request,'urlopen',stub)
    rows=OpenReviewSource().fetch(config(),now=NOW,logger=lambda *_:None)
    assert rows==[] and rows.stats=={'remote_hits':1,'date_rejected':1,'invalid_notes':0}


def test_limit_reached_is_success_for_task_but_pending_for_checkpoints(monkeypatch):
    from paper_radar.sources import base
    class Fake:
        name='test_source';label='Test Source'
        def fetch(self, cfg, logger=print, now=None):
            return [paper('id-'+str(i)) for i in range(cfg['max_results'])]
    cfg={'sources':['test_source'],**config(max_results=3)}
    with patch.dict(base._REGISTRY,{'test_source':Fake}):
        assert len(fetch_enabled(cfg,logger=lambda *_:None))==3
    entry=cfg['_source_results'][0]
    assert entry['partial'] and entry['status']=='limit_reached'
    assert entry['error_code']=='LIMIT_REACHED'
    assert retrieval_outcome(cfg['_source_results'],found=3)['outcome']=='success'
    assert retrieval_outcome([entry,{'ok':False}],found=3)['outcome']=='partial'


def test_quality_deduplicates_across_providers_and_stage_counts(tmp_path):
    from paper_radar.agents.stages import quality
    from paper_radar.sources.retrieval_trace import RetrievalTrace
    from types import SimpleNamespace
    class Task:
        def emit(self,*a,**kw):pass
    p1=paper('arxiv:2501.001','10.1234/shared',source='arxiv')
    p2=paper('openalex:W123','https://doi.org/10.1234/shared',source='openalex')
    p2.abstract='A longer Neural operator abstract with useful mesh details'
    ctx=SimpleNamespace(state={'retrieved':[p1,p2],'profile_ranker':None},
       task=Task(),now=NOW,config=config(),logger=lambda *_:None,checkpoint=lambda:None)
    trace=RetrievalTrace(tmp_path)
    token=ACTIVE_TRACE.set(trace)
    try: out=quality.execute(ctx)
    finally:
        ACTIVE_TRACE.reset(token);trace.close('success')
    assert len(out.data['curated'])==1
    merged=out.data['curated'][0]
    assert merged.source=='arxiv,openalex' and 'mesh details' in merged.abstract
    assert ctx.config['_retrieval_counts']['duplicate_removed']==1
    assert ('cross_source_unique',1) in [(r['filter_stage'],r['count']) for r in read_trace(tmp_path,trace.run_id)['stages']]


def test_quota_check_uses_fixed_host_and_never_returns_secret(monkeypatch):
    from paper_radar.sources import diagnostics
    token='private_token_never_return_to_browser'
    monkeypatch.setattr(diagnostics,'get_credential',lambda source:token)
    class Fake(BytesIO):
        headers={'X-RateLimit-Remaining':'0.75','X-RateLimit-Reset':'3600'}
        def __init__(self):super().__init__(b'{}')
        def __enter__(self):return self
        def __exit__(self,*a):return False
    def fake_open(req,timeout):
        assert req.full_url=='https://api.openalex.org/rate-limit'
        assert req.get_header('Authorization')=='Bearer '+token
        return Fake()
    result=diagnostics.probe_openalex_quota(opener=fake_open)
    assert result['configured'] and result['ok']
    assert result['rate_limit_remaining']==0.75
    assert token not in repr(result)
