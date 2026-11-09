"""Browser acceptance safeguards for a strict CSP and verified downloads."""
from pathlib import Path

from tools.v131_browser_journeys import classify_failed_requests


def test_browser_journeys_use_csp_safe_locator_assertions():
    source=(Path(__file__).resolve().parents[1]/'tools/v131_browser_journeys.py').read_text(encoding='utf-8')
    assert 'wait_for_function(' not in source
    assert "expect(button).to_have_attribute('aria-pressed','true')" in source
    assert "expect(select).to_have_value('done')" in source
    assert 'saved_file.stat().st_size>0' in source


def test_triage_and_following_journeys_are_csp_safe():
    root=Path(__file__).resolve().parents[1]/'tools'
    for script in ('v132_browser_journeys.py','v136_browser_journeys.py'):
        source=(root/script).read_text(encoding='utf-8')
        assert 'wait_for_function(' not in source
        assert 'from playwright.sync_api import expect, sync_playwright' in source


def test_verified_download_handoff_is_not_a_network_failure():
    failures=[{'url':'http://127.0.0.1:8000/api/public/evidence/export','failure':'net::ERR_ABORTED'}]
    assert classify_failed_requests(failures,{'evidence_csv'}) == ([],1)
    assert classify_failed_requests(failures,set()) == (failures,0)


def test_only_one_verified_csv_handoff_is_exempt():
    evidence={'url':'http://127.0.0.1:8000/api/public/evidence/export','failure':'net::ERR_ABORTED'}
    unrelated={'url':'http://127.0.0.1:8000/api/public/papers','failure':'net::ERR_ABORTED'}
    server={'url':'http://127.0.0.1:8000/api/public/evidence/export','failure':'net::ERR_CONNECTION_RESET'}
    unexpected, expected=classify_failed_requests([evidence,unrelated,server,evidence],{'evidence_csv'})
    assert expected==1
    assert unexpected==[unrelated,server,evidence]


def test_query_string_does_not_change_verified_export_detection():
    response={'url':'http://127.0.0.1:8000/api/public/evidence/export?from=abc','failure':'net::ERR_ABORTED'}
    assert classify_failed_requests([response],{'evidence_csv'}) == ([],1)
