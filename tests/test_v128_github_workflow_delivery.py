# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Ensure GitHub sees workflows at repository root after source delivery."""
from pathlib import Path
from zipfile import ZipFile
import importlib.util

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    path = ROOT / 'tools' / 'packaging' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_both_workflows_exist_at_repository_root():
    module = load('check_github_workflows')
    module.validate_repo(ROOT)
    assert (ROOT / '.github/workflows/ci.yml').is_file()
    assert (ROOT / '.github/workflows/desktop-release.yml').is_file()


def test_github_repo_zip_has_hidden_workflows_at_root(tmp_path):
    packager = load('package_github_repo')
    validator = load('check_github_workflows')
    zpath = tmp_path / 'github-root.zip'
    count = packager.build(zpath)
    assert count >= 20
    validator.validate_archive(zpath, prefix='', root=ROOT)
    with ZipFile(zpath) as z:
        names = set(z.namelist())
        assert '.github/workflows/ci.yml' in names
        assert '.github/workflows/desktop-release.yml' in names
        assert 'Paper-Radar/.github/workflows/desktop-release.yml' not in names
        assert '.env' not in names
        assert 'data/settings.json' not in names
        assert z.testzip() is None


def test_source_zip_contains_updater_layout_and_valid_workflows(tmp_path):
    import tools.package_release as packager
    validator = load('check_github_workflows')
    from paper_radar import __version__
    zpath = tmp_path / f'Paper-Radar_v{__version__}_Source.zip'
    assert packager.build(zpath) >= 20
    validator.validate_archive(zpath, prefix='Paper-Radar/', root=ROOT)
    with ZipFile(zpath) as z:
        assert 'Paper-Radar/.github/workflows/desktop-release.yml' in z.namelist()
        assert '.github/workflows/desktop-release.yml' not in z.namelist()


def test_missing_repo_workflow_fails_fast(tmp_path):
    validator = load('check_github_workflows')
    (tmp_path / '.github/workflows').mkdir(parents=True)
    (tmp_path / '.github/workflows/ci.yml').write_text('name: CI\n')
    with pytest.raises(ValueError, match='desktop-release.yml'):
        validator.validate_repo(tmp_path)


def test_wrong_archive_layout_rejected(tmp_path):
    validator = load('check_github_workflows')
    archive = tmp_path / 'nested.zip'
    with ZipFile(archive, 'w') as z:
        for rel in validator.WORKFLOWS:
            z.writestr('Paper-Radar/' + rel, (ROOT / rel).read_bytes())
    with pytest.raises(ValueError, match='incorrectly nested'):
        validator.validate_archive(archive, prefix='', root=ROOT)


def test_release_workflow_yaml_parses_when_pyyaml_available():
    yaml = pytest.importorskip('yaml')
    path = ROOT / '.github/workflows/desktop-release.yml'
    with path.open(encoding='utf-8') as f:
        obj = yaml.load(f, Loader=yaml.BaseLoader)
    assert 'on' in obj
    assert 'push' in obj['on']
    assert 'workflow_dispatch' in obj['on']
    assert {'prepare','test','build','source','publish'}.issubset(obj['jobs'])
