# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Chromium evidence: completed page reloads versus actual failed API calls."""
from tools.v131_browser_journeys import classify_failed_requests

ORIGIN = 'http://127.0.0.1:42937'
DOCS = [{'url': ORIGIN+'/', 'status': 200, 'at': 102.0}]


def event(path='/api/public/papers', *, t=101.85, method='GET', kind='fetch',
          failure='net::ERR_ABORTED', origin=ORIGIN):
    return {'url': origin+path, 'failure': failure, 'method': method,
            'resource_type': kind, 'at': t}


def classify(events, documents=DOCS, downloads=set()):
    return classify_failed_requests(events, downloads,
        document_navigations=documents, site_origin=ORIGIN)


def test_real_ci_aborted_papers_and_dashboard_reloads_are_classified():
    events=[event('/api/public/papers'),event('/api/public/dashboard',t=102.1)]
    assert classify(events)==([],0)
    # Compatibility: without independently verified document navigation,
    # both failed requests continue to block release.
    assert classify(events,[])==(events,0)


def test_network_abort_does_not_silently_hide_unrelated_failures():
    events=[
        event('/api/public/papers',t=103.5),             # too far from transition
        event('/api/public/papers',method='POST'),       # cannot ignore a mutation
        event('/api/public/papers',kind='document'),     # not an asynchronous API GET
        event('/api/public/search'),                    # unlisted endpoint
        event('/api/public/dashboard',failure='net::ERR_CONNECTION_RESET'),
        event('/api/public/papers',origin='http://example.com'),
    ]
    assert classify(events)==(events,0)
    assert classify([event()],documents=[{'url':ORIGIN+'/', 'status': 404, 'at':102.0}]) == ([event()],0)
    assert classify([event()],documents=[{'url':'http://example.com/', 'status':200,'at':102.0}]) == ([event()],0)


def test_csv_handoff_and_verified_navigation_aborts_are_distinct():
    events=[event('/api/public/papers'),
            {'url':ORIGIN+'/api/public/evidence/export','failure':'net::ERR_ABORTED'},
            event('/api/public/dashboard',t=102.1)]
    assert classify(events,downloads={'evidence_csv'})==([],1)
    assert classify(events,documents=[],downloads={'evidence_csv'})==([events[0],events[2]],1)
