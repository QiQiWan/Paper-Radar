"""Independent release verification and versioned database migrations."""
from __future__ import annotations
import base64
import hashlib
from io import BytesIO
import json
import sqlite3
import threading
from urllib.error import URLError
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat

from paper_radar.release_signatures import verify_manifest, SignatureError
from paper_radar.migrations import migrate, status, MigrationError, REGISTRIES
from paper_radar.startup_update import StartupUpdater, UpdateError, release_asset


def signed_payload(tmp_path, *, version='1.16.1'):
    private=Ed25519PrivateKey.generate()
    public=private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    kid='test-'+hashlib.sha256(public).hexdigest()[:10]
    trust=tmp_path/'release_public_keys.json'
    trust.write_text(json.dumps({'keys':[{'id':kid,'algorithm':'Ed25519',
        'public_key_base64':base64.b64encode(public).decode()}]}))
    zip_name=f'Paper-Radar_v{version}_Source.zip'
    zip_blob=b'dummy stable release archive bytes'
    manifest={
        'format':1,'repo':'QiQiWan/Paper-Radar','version':version,'tag':'v'+version,
        'asset':zip_name,'sha256':hashlib.sha256(zip_blob).hexdigest(),
        'size':len(zip_blob),'startup_protocol':1,'algorithm':'Ed25519','key_id':kid}
    raw=(json.dumps(manifest,sort_keys=True,separators=(',',':'))+'\n').encode()
    sig=base64.b64encode(private.sign(raw))+b'\n'
    return trust,raw,sig,zip_blob


def test_independent_signature_accepts_exact_release(tmp_path):
    trust,raw,sig,blob=signed_payload(tmp_path)
    assert verify_manifest(raw,sig,trust,version='1.16.1',
        sha256=hashlib.sha256(blob).hexdigest(),archive_size=len(blob),
        name='Paper-Radar_v1.16.1_Source.zip')['version']=='1.16.1'


@pytest.mark.parametrize('failure', ['tampered_manifest','tampered_signature','wrong_hash','untrusted_key','version_replay'])
def test_independent_signature_rejects_mismatches(tmp_path,failure):
    trust,raw,sig,blob=signed_payload(tmp_path)
    version='1.16.1'
    sha=hashlib.sha256(blob).hexdigest()
    if failure=='tampered_manifest':raw=raw.replace(b'"startup_protocol":1',b'"startup_protocol":2')
    if failure=='tampered_signature':sig=base64.b64encode(b'0'*64)
    if failure=='wrong_hash':sha='0'*64
    if failure=='untrusted_key':trust.write_text(json.dumps({'keys':[]}))
    if failure=='version_replay':version='1.16.2'
    with pytest.raises(SignatureError):
        verify_manifest(raw,sig,trust,version=version,sha256=sha,archive_size=len(blob),
            name=f'Paper-Radar_v{version}_Source.zip')


def test_updater_requires_signature_on_116(tmp_path):
    root=tmp_path/'Paper-Radar'
    (root/'paper_radar').mkdir(parents=True)
    (root/'paper_radar/__init__.py').write_text('__version__ = "1.16.0"\n')
    trust,raw,sig,blob=signed_payload(tmp_path)
    (root/'paper_radar/release_public_keys.json').write_bytes(trust.read_bytes())
    ver='1.16.1'
    base=f'https://github.com/QiQiWan/Paper-Radar/releases/download/v{ver}/Paper-Radar_v{ver}_'
    links={'manifest_url':base+'release.json','signature_url':base+'release.sig'}
    class Resp(BytesIO):
        def __init__(self,value,url):super().__init__(value);self.url=url
    def fake_open(req,timeout=None):
        return Resp(raw if req.full_url.endswith('.json') else sig,req.full_url)
    updater=StartupUpdater(root,opener=fake_open)
    asset={'version':ver,'sha256':hashlib.sha256(blob).hexdigest(),
           'size':len(blob),**links}
    updater._check_signature(asset)
    with pytest.raises(UpdateError,match='缺少独立签名'):
        updater._check_signature({k:v for k,v in asset.items() if k not in links})
    (root/'paper_radar/release_public_keys.json').unlink()
    with pytest.raises(UpdateError,match='公钥'):
        updater._check_signature(asset)


def test_release_api_selects_signature_assets():
    version='1.16.2'
    base=f'https://github.com/QiQiWan/Paper-Radar/releases/download/v{version}/'
    names=[f'Paper-Radar_v{version}_Source.zip',f'Paper-Radar_v{version}_release.json',f'Paper-Radar_v{version}_release.sig']
    info={'tag_name':'v'+version,'html_url':f'https://github.com/QiQiWan/Paper-Radar/releases/tag/v{version}',
        'draft':False,'prerelease':False,'assets':[{'name':name,'state':'uploaded',
            'browser_download_url':base+name,'digest':'sha256:'+'a'*64,'size':10000} for name in names]}
    asset=release_asset(info,'1.16.0')
    assert asset['manifest_url'].endswith('_release.json')
    assert asset['signature_url'].endswith('_release.sig')


def test_migration_registry_and_reapply(tmp_path):
    dbfile=tmp_path/'public.sqlite3'
    with sqlite3.connect(dbfile) as conn:
        assert migrate(conn,'portal_auth')>=3
        assert migrate(conn,'portal_auth')>=3
        assert status(conn,'portal_auth')['current']
        n=conn.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0]
        assert n==len(__import__('paper_radar.migrations',fromlist=['REGISTRIES']).REGISTRIES['portal_auth'])
        conn.execute("UPDATE schema_migrations SET checksum='tampered' WHERE kind='portal_auth'")
        conn.commit()
        with pytest.raises(MigrationError,match='mismatch'):migrate(conn,'portal_auth')


def test_migration_rejects_future_schema(tmp_path):
    with sqlite3.connect(tmp_path/'test.sqlite3') as conn:
        migrate(conn,'portal_workspace')
        conn.execute("INSERT INTO schema_migrations VALUES ('portal_workspace',999,'future','abc','now')")
        conn.commit()
        with pytest.raises(MigrationError,match='newer'):migrate(conn,'portal_workspace')


def test_research_database_refuses_future_schema_before_legacy_writes(tmp_path):
    from paper_radar.core import connect
    from paper_radar.migrations import assert_compatible
    path=tmp_path/'papers.sqlite3'
    db=connect(path)
    db.execute("INSERT INTO schema_migrations VALUES ('research',999,'future','sha','now')")
    db.commit();db.close()
    with pytest.raises(MigrationError,match='newer'):connect(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=999").fetchone()[0]==1


def test_public_running_marker_blocks_desktop_hot_update(tmp_path):
    root=tmp_path/'Paper-Radar'
    (root/'paper_radar').mkdir(parents=True)
    (root/'paper_radar/__init__.py').write_text('__version__ = "1.16.0"\n')
    marker=root/'.paper_radar_updates'/'public_service.active'
    marker.parent.mkdir(parents=True)
    marker.write_text('{"port":8899}')
    updater=StartupUpdater(root,opener=lambda *_args,**_kwargs: (_ for _ in ()).throw(AssertionError('Unexpected network call')))
    result=updater.run()
    assert result['state']=='running'
