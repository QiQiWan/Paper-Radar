# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Exercise a genuine signed 1.30.0 -> 1.31.0 ZIP install + rollback offline.

This uses the production release parser, Ed25519 verifier, file journal and
SQLite settings snapshots. The GitHub transport is simulated; no publication
or native frozen-binary upgrade is claimed by these tests.
"""
from __future__ import annotations

import base64
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from paper_radar.startup_update import StartupUpdater
from tools.sign_release import manifest_bytes


VERSION = '1.31.0'
BASE = 'https://github.com/QiQiWan/Paper-Radar/releases'
TAG = 'v'+VERSION
ASSET = f'Paper-Radar_v{VERSION}_Source.zip'


class OfflineResponse(BytesIO):
    def __init__(self, blob: bytes, url: str):
        super().__init__(blob)
        self.url = url


class OfflineGitHub:
    def __init__(self, mapping: dict[str,bytes]):
        self.mapping = mapping
        self.requests=[]

    def __call__(self, req, timeout=None):
        url=req.full_url
        self.requests.append(url)
        if url not in self.mapping:
            raise RuntimeError('Offline test requested unexpected URL: '+url)
        return OfflineResponse(self.mapping[url],url)


def fixture_release(tmp_path, *, bad_signature=False, missing_signature=False, digest_mismatch=False):
    root=tmp_path/'Paper-Radar'
    (root/'paper_radar').mkdir(parents=True)
    (root/'paper_radar/__init__.py').write_text('__version__ = "1.30.0"\n__startup_protocol__ = 1\n')
    (root/'paper_radar/stale.py').write_text('old = True\n')
    (root/'main.py').write_text('print("original build")\n')
    (root/'README.md').write_text('old README\n')
    (root/'settings.json').write_text('{"model_key":"offline-local-only"}\n')
    (root/'data').mkdir()
    with sqlite3.connect(root/'data'/'papers.sqlite3') as db:
        db.execute('CREATE TABLE papers(title TEXT)')
        db.execute('INSERT INTO papers VALUES (?)',('Private research - must survive',))
    (root/'downloads').mkdir()
    (root/'downloads'/'original.pdf').write_bytes(b'%PDF-1.4\nprivate-placeholder')
    (root/'start_windows.bat').write_text('@echo off\nrem trusted launcher\n')
    private=Ed25519PrivateKey.generate()
    public=private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    kid='paper-radar-2026-'+hashlib.sha256(public).hexdigest()[:12]
    trust={'format':1,'keys':[{'id':kid,'algorithm':'Ed25519','public_key_base64':base64.b64encode(public).decode()}]}
    trust_bytes=(json.dumps(trust)+'\n').encode()
    (root/'paper_radar/release_public_keys.json').write_bytes(trust_bytes)

    # Enough entropy to exceed minimum GitHub asset size, and valid Python.
    program={
       'paper_radar/__init__.py':'__version__ = "1.31.0"\n__startup_protocol__ = 1\n',
       'paper_radar/startup_update.py':'def installed_version():\n    return "1.31.0"\n',
       'paper_radar/release_public_keys.json':trust_bytes,
       'main.py':'print("updated build")\n',
       'startup.py':'print("startup 1.31.0")\n',
       'pyproject.toml':'[project]\nname="paper-radar-local"\nversion="1.31.0"\n',
       'requirements.txt':'keyring>=25\n',
       'README.md':'release-'+os.urandom(1600).hex(),
       'start_windows.bat':'@echo off\nrem untrusted launcher: must be ignored\n',
    }
    output=BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as z:
        for rel,contents in program.items():
            z.writestr('Paper-Radar/'+rel,contents)
    archive=output.getvalue()
    assert len(archive)>=1024
    digest=hashlib.sha256(archive).hexdigest()
    manifest={'format':1,'repo':'QiQiWan/Paper-Radar','version':VERSION,'tag':TAG,
        'asset':ASSET,'sha256':digest,'size':len(archive),'startup_protocol':1,
        'algorithm':'Ed25519','key_id':kid}
    mblob=manifest_bytes(manifest)
    sig=base64.b64encode(private.sign(mblob))+b'\n'
    if bad_signature:
        sig=base64.b64encode(b'0'*64)+b'\n'
    base=f'{BASE}/download/{TAG}/'
    assets=[{'name':ASSET,'state':'uploaded','size':len(archive),
             'digest':'sha256:'+('f'*64 if digest_mismatch else digest),
             'browser_download_url':base+ASSET}]
    if not missing_signature:
        for suffix in ('release.json','release.sig'):
            assets.append({'name':f'Paper-Radar_v{VERSION}_{suffix}',
                  'state':'uploaded','browser_download_url':base+f'Paper-Radar_v{VERSION}_{suffix}'})
    payload={'tag_name':TAG,'html_url':f'{BASE}/tag/{TAG}',
             'draft':False,'prerelease':False,'assets':assets}
    opener=OfflineGitHub({
        'https://api.github.com/repos/QiQiWan/Paper-Radar/releases/latest':json.dumps(payload).encode(),
        base+ASSET:archive,
        base+f'Paper-Radar_v{VERSION}_release.json':mblob,
        base+f'Paper-Radar_v{VERSION}_release.sig':sig,
    })
    return root,opener


def test_verified_signed_updater_commits_and_preserves_user_state(tmp_path):
    root,transport=fixture_release(tmp_path)
    updater=StartupUpdater(root,opener=transport)
    updater.is_web_running=lambda:False
    result=updater.run()
    assert result['state']=='updated',result
    assert result['installed_version']==VERSION
    assert (root/'main.py').read_text()=='print("updated build")\n'
    assert not (root/'paper_radar/stale.py').exists()
    assert (root/'start_windows.bat').read_text()=='@echo off\nrem trusted launcher\n'
    assert 'offline-local-only' in (root/'settings.json').read_text()
    assert (root/'downloads/original.pdf').read_bytes().startswith(b'%PDF-1.4')
    with sqlite3.connect(root/'data/papers.sqlite3') as db:
        assert db.execute('SELECT title FROM papers').fetchone()[0]=='Private research - must survive'
    assert not updater.journal_path.exists()
    assert any('release.sig' in url for url in transport.requests)


def test_post_install_failure_restores_version_code_and_database(tmp_path):
    root,transport=fixture_release(tmp_path)
    updater=StartupUpdater(root,opener=transport)
    updater.is_web_running=lambda:False
    result=updater.run()
    assert result['state']=='updated',result
    (root/'settings.json').write_text('{"model_key":"modified-by-failed-app"}\n')
    with sqlite3.connect(root/'data/papers.sqlite3') as db:
        db.execute('UPDATE papers SET title=?',('changed by failed app',))
    assert updater.mark_failed_and_rollback(failed_version=VERSION,reason='failed health')
    assert '1.30.0' in (root/'paper_radar/__init__.py').read_text()
    assert (root/'main.py').read_text()=='print("original build")\n'
    assert (root/'paper_radar/stale.py').exists()
    assert 'offline-local-only' in (root/'settings.json').read_text()
    with sqlite3.connect(root/'data/papers.sqlite3') as db:
        assert db.execute('SELECT title FROM papers').fetchone()[0]=='Private research - must survive'
    assert updater.status_path.exists()
    assert not updater.journal_path.exists()


@pytest.mark.parametrize('failure',['bad_signature','missing_signature','digest_mismatch'])
def test_invalid_offline_release_rejected_without_side_effects(tmp_path,failure):
    root,transport=fixture_release(tmp_path,**{failure:True})
    updater=StartupUpdater(root,opener=transport)
    updater.is_web_running=lambda:False
    result=updater.run()
    # An unsigned official Release remains manual-download-only.
    # Invalid signed artifacts must fail closed without installation.
    if failure == 'missing_signature':
        assert result['state']=='up_to_date',result
        assert transport.requests == ['https://api.github.com/repos/QiQiWan/Paper-Radar/releases/latest']
    else:
        assert result['state']=='error',result
    assert (root/'main.py').read_text()=='print("original build")\n'
    assert (root/'paper_radar/stale.py').exists()
    assert not updater.journal_path.exists()
    assert 'offline-local-only' in (root/'settings.json').read_text()
