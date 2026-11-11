# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Make the synthetic following journey reflect actual collapsed v1.52 navigation."""
from pathlib import Path
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def test_following_navigation_is_revealed_by_an_explicit_user_click():
    soup=BeautifulSoup((ROOT/'paper_radar/static/portal.html').read_text('utf-8'),'html.parser')
    menu=soup.select_one('#primaryNav details.portal-side-more')
    assert menu is not None and not menu.has_attr('open')
    assert menu.select_one('button[data-view="following"]') is not None
    script=(ROOT/'tools/v136_browser_journeys.py').read_text('utf-8')
    assert 'def navigate_to_following():' in script
    assert "menu.locator('summary').click()" in script
    assert script.count('navigate_to_following()') >= 3  # definition + 2 callsites


def test_following_actions_remain_release_blocking():
    script=(ROOT/'tools/v136_browser_journeys.py').read_text('utf-8')
    assert "step('following_inbox',follow)" in script
    assert "step('preference_persists',preference)" in script
    assert "step('seen_and_persisted',seen)" in script
    assert "return 0 if result['summary']['completion_rate']==1" in script
