"""Full repository parity and clean distribution checks, after v1.34.1 cleanup."""
from pathlib import Path
from zipfile import ZipFile
import json

from paper_radar import __version__
from paper_radar.help_pages import GUIDES, render_guide
from tools.packaging.feature_parity import required_paths, validate_tree, validate_zip
from tools.packaging.check_github_workflows import validate_repo
from tools.package_runtime import build_runtime

ROOT = Path(__file__).resolve().parents[1]


def test_baseline_feature_inventory_and_routes_remain_intact():
    result = validate_tree(ROOT)
    assert result['checked'] >= 240
    assert result['api_markers'] >= 110
    assert result['ui_ids'] >= 350
    assert result['missing'] == 0


def test_public_and_local_service_entrypoints_are_available():
    for name in ('start_windows.bat','start_macos_linux.sh','paper_radar/cli_launcher.py',
                 'deploy/nginx-paper-radar.conf','deploy/paperradar-public.service',
                 'deploy/paperradar-worker@.service'):
        assert (ROOT/name).is_file(), name
    import paper_radar.public_portal
    import paper_radar.portal_worker
    import paper_radar.webapp
    import paper_radar.agents.runtime


def test_github_workflow_release_dependencies_are_present():
    validate_repo(ROOT)
    import yaml
    with (ROOT/'.github/workflows/ci.yml').open(encoding='utf-8') as f:
        ci=yaml.load(f,Loader=yaml.BaseLoader)
    with (ROOT/'.github/workflows/desktop-release.yml').open(encoding='utf-8') as f:
        release=yaml.load(f,Loader=yaml.BaseLoader)
    assert {'test','windows-smoke'} <= set(ci['jobs'])
    assert {'prepare','test','build','source','publish','promote'} <= set(release['jobs'])
    matrix=release['jobs']['build']['strategy']['matrix']['include']
    assert {entry['label'] for entry in matrix}=={
        'windows-x64','ubuntu-amd64','macos-arm64','macos-intel'
    }
    assert 'PAPER_RADAR_RELEASE_SIGNING_KEY_PEM' in (ROOT/'.github/workflows/desktop-release.yml').read_text()
    assert 'PAPER_RADAR_NATIVE_RELEASE_APPROVED' in (ROOT/'.github/workflows/desktop-release.yml').read_text()


def test_clean_user_manual_retains_legacy_help_routes():
    docs={p.name for p in (ROOT/'docs').glob('*.md')}
    assert {'USER_GUIDE.md','MAINTAINERS.md'} <= docs
    # A full Git checkout may retain historical guides and release notes.
    # Require canonical manuals without assuming they are the only Markdown
    # files; the compact ZIP intentionally carries fewer historical docs.
    assert all((ROOT/'docs'/name).is_file() for name in ('USER_GUIDE.md','MAINTAINERS.md'))
    assert len(GUIDES)>=11
    for slug in GUIDES:
        body=render_guide(slug)
        assert body and b'<figure' in body
        assert b'<table' in render_guide('sources')
    for obsolete in ('REPAIR_WINDOWS_SETTINGS.bat','REPAIR_WINDOWS_STORAGE.bat'):
        assert not (ROOT/obsolete).exists()


def test_lightweight_runtime_keeps_all_app_modules_and_public_launchers(tmp_path):
    archive=tmp_path/f'Paper-Radar_v{__version__}_Runtime.zip'
    result=build_runtime(archive,ROOT)
    assert result['files']>=100
    with ZipFile(archive) as z:
        names=set(z.namelist())
        assert 'Paper-Radar/start_macos_linux.sh' in names
        assert 'Paper-Radar/start_windows.bat' in names
        assert 'Paper-Radar/paper_radar/launcher.py' in names
        assert 'Paper-Radar/paper_radar/public_portal.py' in names
        assert 'Paper-Radar/paper_radar/agents/research/synthesis.py' in names
        assert 'Paper-Radar/docs/USER_GUIDE.md' in names
        assert 'Paper-Radar/docs/RETRIEVAL_AND_UI_V1404.md' in names
        assert 'Paper-Radar/docs/RETRIEVAL_REPAIR_V1403.md' in names
        assert 'Paper-Radar/benchmarks/real_pdf30/manifest.json' in names
        assert 'Paper-Radar/benchmarks/real_pdf30/tasks/single_and_cross_paper_qa.jsonl' in names
        assert 'Paper-Radar/paper_radar/review_webapp.py' in names
        assert not any(name.lower().endswith('.pdf') for name in names)
        assert 'Paper-Radar/deploy/nginx-paper-radar.conf' in names
        assert 'Paper-Radar/REPAIR_WINDOWS_STORAGE.bat' not in names
        assert 'Paper-Radar/settings.json' not in names
        assert z.testzip() is None
