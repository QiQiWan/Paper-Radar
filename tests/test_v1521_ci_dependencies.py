# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Prevent CI and desktop release regressions from undeclared test packages."""
from pathlib import Path
import sys
import re

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_dev_extra_declares_html_parser_used_by_ai_guidance_tests():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    declared = [re.split(r'[<>=!~]', s, maxsplit=1)[0].lower()
                for s in project['project']['optional-dependencies']['dev']]
    assert {'pytest', 'pyyaml', 'beautifulsoup4'} <= set(declared)
    # The import is indispensable to collecting the AI guidance regression file.
    assert 'from bs4 import BeautifulSoup' in (
        ROOT / 'tests/test_v148_ai_guidance.py').read_text(encoding='utf-8')


def test_all_ci_test_jobs_install_the_same_declared_dev_extra():
    for path in ('ci.yml', 'desktop-release.yml', 'reliability.yml'):
        workflow = (ROOT / '.github/workflows' / path).read_text(encoding='utf-8')
        assert 'python -m pip install -e ".[dev]"' in workflow, path
    ci = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    assert 'fail-fast: false' in ci
