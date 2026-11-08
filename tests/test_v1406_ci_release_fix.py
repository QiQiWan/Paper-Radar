"""Regression checks for GitHub 3.11 CI and official Release channel."""
from pathlib import Path
import yaml
from paper_radar.help_pages import render_guide

ROOT=Path(__file__).resolve().parents[1]


def test_guide_nav_is_html_safe_and_marks_selected_page():
    rendered=render_guide('quickstart')
    assert rendered is not None
    page=rendered.decode('utf-8')
    assert '<a aria-current="page" href="/guide/quickstart">' in page
    assert '<a href="/guide/reading">' in page


def test_github_workflows_run_with_utf8_on_windows():
    for name in ('ci.yml','reliability.yml','desktop-release.yml'):
        text=(ROOT/'.github/workflows'/name).read_text(encoding='utf-8')
        workflow=yaml.load(text,Loader=yaml.BaseLoader)
        assert workflow['env']['PYTHONUTF8']=='1'
        assert workflow['env']['PYTHONIOENCODING']=='utf-8'
        assert 'compileall -q paper_radar tools' in text


def test_verified_release_is_official_but_unsigned_update_remains_blocked():
    text=(ROOT/'.github/workflows/desktop-release.yml').read_text(encoding='utf-8')
    assert 'needs: [prepare, test, build, source]' in text
    assert 'python tools/packaging/release_assets.py verify' in text
    assert 'gh release create "$TAG" release/*' in text
    assert 'gh release edit "$TAG" --repo "$GITHUB_REPOSITORY" --draft=false --prerelease=false' in text
    assert 'if [ "$NATIVE_RELEASE_APPROVED" != "v${VERSION}" ]; then' in text
    assert 'python -m tools.packaging.release_gate' in text
    assert 'auto_update=false' in text


def test_unsigned_official_release_keeps_manual_download_but_disables_auto_install(tmp_path):
    import json
    from io import BytesIO
    from unittest.mock import patch
    from paper_radar.core import DEFAULTS
    from paper_radar.updates import fetch_latest, UpdateChecker
    from paper_radar.startup_update import release_asset
    ver='1.40.7'
    root=f'https://github.com/QiQiWan/Paper-Radar/releases'
    filename=f'Paper-Radar_v{ver}_Source.zip'
    asset={'name':filename,'state':'uploaded','digest':'sha256:'+'a'*64,
           'size':12000,'browser_download_url':f'{root}/download/v{ver}/{filename}'}
    info={'tag_name':f'v{ver}','html_url':f'{root}/tag/v{ver}',
          'draft':False,'prerelease':False,'assets':[asset]}
    with patch('paper_radar.updates.request.urlopen',return_value=BytesIO(json.dumps(info).encode('utf-8'))):
        with patch('paper_radar.updates.__version__','1.40.6'):
            found=fetch_latest()
    assert found['state']=='available' and found['source_auto_installable'] is False
    checker=UpdateChecker(tmp_path/'status.json',fetcher=lambda:found)
    checker._snapshot=found
    assert checker.status(DEFAULTS)['can_install'] is False
    assert release_asset(info,'1.40.6') is None
    # Older 1.15 bootstrap contract remains unchanged for compatibility.
    assert release_asset(info,'1.15.0')['version']==ver


def test_signed_release_has_eligible_metadata_but_signature_still_verified(tmp_path):
    import json
    from io import BytesIO
    from unittest.mock import patch
    from paper_radar.updates import fetch_latest, UpdateChecker
    from paper_radar.core import DEFAULTS
    ver='1.40.7'
    root=f'https://github.com/QiQiWan/Paper-Radar/releases'
    base=f'{root}/download/v{ver}/'
    names=[f'Paper-Radar_v{ver}_{suffix}' for suffix in
           ('Source.zip','release.json','release.sig')]
    info={'tag_name':f'v{ver}','html_url':f'{root}/tag/v{ver}',
          'draft':False,'prerelease':False,
          'assets':[{'name':name,'state':'uploaded','browser_download_url':base+name,
                     'size':12000,'digest':'sha256:'+'a'*64} for name in names]}
    with patch('paper_radar.updates.request.urlopen',return_value=BytesIO(json.dumps(info).encode('utf-8'))):
        with patch('paper_radar.updates.__version__','1.40.6'):
            found=fetch_latest()
    assert found['source_auto_installable'] is True
    checker=UpdateChecker(tmp_path/'status.json',fetcher=lambda:found)
    checker._snapshot=found
    assert checker.status(DEFAULTS)['can_install'] is True
