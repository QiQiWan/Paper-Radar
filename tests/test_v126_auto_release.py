# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Offline checks for GitHub desktop release metadata and asset validation."""
from __future__ import annotations
from pathlib import Path
import importlib.util
from zipfile import ZipFile
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    path = ROOT / 'tools' / 'packaging' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_release_version_matches_project():
    from paper_radar import __version__
    assert load('release_version').read_version(ROOT) == __version__


def test_release_version_rejects_version_mismatch(tmp_path):
    import pytest
    (tmp_path / 'paper_radar').mkdir()
    (tmp_path / 'paper_radar' / '__init__.py').write_text('__version__ = "1.30.0"')
    (tmp_path / 'pyproject.toml').write_text('[project]\nversion="1.29.0"\n')
    with pytest.raises(ValueError, match='Version mismatch'):
        load('release_version').read_version(tmp_path)


def make_assets(folder: Path, version: str):
    release_assets = load('release_assets')
    for name in release_assets.asset_names(version):
        p = folder / name
        if name.endswith('.deb'):
            p.write_bytes(b'!<arch>\n' + b'not-a-real-deb-for-test')
        else:
            with ZipFile(p, 'w') as archive:
                archive.writestr('Paper-Radar/README.txt', 'sanity')
        release_assets.checksum_file(p)
    return release_assets


def test_release_asset_set_and_checksums(tmp_path):
    m = make_assets(tmp_path, '1.26.0')
    assert len(m.verify(tmp_path, '1.26.0')) == 12
    assert len({p.name for p in tmp_path.iterdir()}) == 12


def test_release_asset_tamper_rejected(tmp_path):
    import pytest
    m = make_assets(tmp_path, '1.26.0')
    with (tmp_path / m.asset_names('1.26.0')[0]).open('ab') as f:
        f.write(b'tampered')
    with pytest.raises(ValueError, match='Checksum mismatch'):
        m.verify(tmp_path, '1.26.0')


def test_release_asset_renaming_not_silently_accepted(tmp_path):
    import pytest
    m = make_assets(tmp_path, '1.26.0')
    bad = tmp_path / m.asset_names('1.26.0')[1]
    bad.rename(tmp_path / 'renamed.zip')
    with pytest.raises(ValueError, match='Missing/empty'):
        m.verify(tmp_path, '1.26.0')


def test_source_zip_path_traversal_rejected(tmp_path):
    import pytest
    p = tmp_path / 'malicious.zip'
    with ZipFile(p, 'w') as archive:
        archive.writestr('../evil', 'no')
    with pytest.raises(ValueError, match='Unsafe ZIP'):
        load('release_assets').verify_zip(p)


def test_workflow_has_release_gate_and_required_runners():
    text = (ROOT / '.github' / 'workflows' / 'desktop-release.yml').read_text()
    for snippet in ('branches: [main]', 'macos-15-intel', 'macos-15', 'ubuntu-22.04',
                    'windows-latest', 'contents: write', 'release=false',
                    '--draft=false', 'release_assets.py verify', 'needs: [prepare, test, build, source]'):
        assert snippet in text
    assert 'Paper-Radar_macOS_${APP_ARCH}.zip' in text


def test_bump_version_updates_all_markers(tmp_path):
    for rel in ["paper_radar/__init__.py", "pyproject.toml", "paper_radar/agents/catalog.py",
                "paper_radar/static/index.html", "paper_radar/static/portal.html"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, tmp_path / rel)
    sys.path.insert(0, str(ROOT / "tools" / "packaging"))
    try:
        from bump_version import bump
        from paper_radar import __version__
        major, minor, patch = map(int, __version__.split('.'))
        next_version = f"{major}.{minor}.{patch + 1}"
        bump(next_version, tmp_path)
        assert load("release_version").read_version(tmp_path) == next_version
    finally:
        sys.path.remove(str(ROOT / "tools" / "packaging"))
