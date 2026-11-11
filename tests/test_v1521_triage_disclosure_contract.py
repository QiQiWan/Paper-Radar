# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Guard v1.52 disclosure in the browser journey and PR/Release gate parity."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_hidden_bulk_controls_are_opened_before_each_real_user_action():
    from bs4 import BeautifulSoup
    soup=BeautifulSoup((ROOT/'paper_radar/static/portal.html').read_text('utf-8'),'html.parser')
    details=soup.select_one('#viewLibrary details.library-extra-tools')
    assert details is not None and 'open' not in details.attrs
    for name in ('triageStar','triageSkip','exportActionReading','exportStarredReading'):
        assert details.select_one('#'+name) is not None, name
    journey=(ROOT/'tools/v132_browser_journeys.py').read_text('utf-8')
    assert 'def open_library_extra_tools():' in journey
    for action in ('batch','export','skip'):
        assert f'def {action}():\n                    open_library_extra_tools()' in journey or (
            action=='export' and "def export():\n                    # A hard reload" in journey)


def test_ci_and_release_run_the_same_seven_release_acceptance_commands():
    workflow=(ROOT/'.github/workflows/desktop-release.yml').read_text('utf-8')
    ci=(ROOT/'.github/workflows/ci.yml').read_text('utf-8')
    modules=('v131_workspace_benchmark','v131_mutation_benchmark',
             'v131_browser_journeys','v132_triage_benchmark',
             'v132_browser_journeys','v136_following_benchmark',
             'v136_browser_journeys')
    for module in modules:
        command='python -m tools.'+module+' --papers 1000'
        assert command in workflow, module
        assert command in ci, module
