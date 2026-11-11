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


def test_triage_browser_release_gate_handles_empty_error_lists(monkeypatch, tmp_path):
    from tools import v132_browser_journeys
    monkeypatch.setattr(v132_browser_journeys, 'run', lambda **kwargs: {
        'summary': {'task_error_rate': 0.0, 'js_exceptions': [], 'http_5xx': []}})
    assert v132_browser_journeys.main(['--enforce', '--out-dir', str(tmp_path)]) == 0


def test_triage_browser_release_gate_rejects_real_failures(monkeypatch, tmp_path):
    from tools import v132_browser_journeys
    for key, value in (('task_error_rate', 0.1), ('js_exceptions', ['boom']),
                       ('http_5xx', ['500'])):
        summary={'task_error_rate':0.0, 'js_exceptions':[], 'http_5xx':[]}
        summary[key]=value
        monkeypatch.setattr(v132_browser_journeys,'run', lambda **kwargs: {'summary':summary})
        assert v132_browser_journeys.main(['--enforce', '--out-dir',str(tmp_path)])==1



def test_native_startup_timeout_policy_keeps_intel_under_real_probe():
    from tools.v131_native_smoke import startup_wait_seconds
    assert startup_wait_seconds('Darwin','x86_64')==75
    assert startup_wait_seconds('Darwin','arm64')==40
    assert startup_wait_seconds('Windows','AMD64')==40
    assert startup_wait_seconds('Linux','x86_64')==40
    source=(Path(__file__).resolve().parents[1]/'tools/v131_native_smoke.py').read_text(encoding='utf-8')
    assert 'exit_code={process.poll()}' in source
    assert "debug_path=Path(out).with_suffix('.failure.log')" in source


def _benchmark_trial(note_ms, failures=None):
    failures=failures or []
    return {'gate':{'passed':not failures,'reasons':failures},
            'groups':{kind:{'p95_ms':note_ms if kind=='note' else 30.0}
                      for kind in ('note','batch','read','export')}}


def test_repeated_performance_gate_ignores_one_runner_spike():
    from tools.v132_triage_benchmark import evaluate_repeated_trials
    result=evaluate_repeated_trials([_benchmark_trial(132),_benchmark_trial(1729),
                                     _benchmark_trial(140)],1500)
    assert result['passed'] is True
    assert result['median_group_p95_ms']['note']==140
    assert result['trial_group_p95_ms']['note']==[132.0,1729.0,140.0]


def test_repeated_performance_gate_blocks_consistent_slowness_and_data_errors():
    from tools.v132_triage_benchmark import evaluate_repeated_trials
    result=evaluate_repeated_trials([_benchmark_trial(1700),_benchmark_trial(1600),
                                     _benchmark_trial(130)],1500)
    assert result['passed'] is False and 'p95_budget_exceeded' in result['reasons']
    corruption=evaluate_repeated_trials([_benchmark_trial(130),
        _benchmark_trial(140,['sqlite_integrity']),_benchmark_trial(135)],1500)
    assert corruption['passed'] is False and 'sqlite_integrity' in corruption['reasons']


def test_direct_loopback_probe_ignores_http_proxy(monkeypatch):
    from tools.v131_native_smoke import _get
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from threading import Thread
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'health-ok')
        def log_message(self, fmt, *args):
            pass
    server=HTTPServer(('127.0.0.1',0), Handler)
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        monkeypatch.setenv('HTTP_PROXY','http://127.0.0.1:1')
        monkeypatch.setenv('http_proxy','http://127.0.0.1:1')
        monkeypatch.delenv('NO_PROXY',raising=False)
        assert _get(f'http://127.0.0.1:{server.server_port}/health') == (200,b'health-ok')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
