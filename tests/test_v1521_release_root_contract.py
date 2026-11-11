# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Protect the startup updater and both source/repository ZIP layout contracts."""
from pathlib import Path
import pytest

from paper_radar.startup_update import MANAGED_ITEMS, UpdateError, _safe_member
from tools.package_release import ALLOWED_ROOT, include
from tools.packaging.check_github_workflows import validate_root_launchers

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = {'start_windows.bat', 'start_macos_linux.sh'}


def test_release_root_uses_only_supported_launchers():
    validate_root_launchers(ROOT)
    actual = {p.name for p in ROOT.iterdir()
              if p.suffix.lower() in ('.bat', '.sh')}
    assert actual == LAUNCHERS


def test_stray_root_script_fails_during_preflight(tmp_path):
    for name in LAUNCHERS:
        (tmp_path / name).write_text('launcher', encoding='ascii')
    validate_root_launchers(tmp_path)
    (tmp_path / 'CHECK_RETRIEVAL_WINDOWS.bat').write_text('@echo off', encoding='ascii')
    with pytest.raises(ValueError, match='CHECK_RETRIEVAL_WINDOWS'):
        validate_root_launchers(tmp_path)


def test_packager_allowed_roots_derive_from_updater_contract():
    assert ALLOWED_ROOT == frozenset(MANAGED_ITEMS) | LAUNCHERS
    assert include(Path('start_windows.bat'))
    assert not include(Path('CHECK_RETRIEVAL_WINDOWS.bat'))
    with pytest.raises(UpdateError, match='白名单'):
        _safe_member('Paper-Radar/CHECK_RETRIEVAL_WINDOWS.bat', 'Paper-Radar')
