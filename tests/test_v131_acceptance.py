# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""v1.31.0: measurable, offline load/UX prerequisites and signed release gates."""
from __future__ import annotations
import base64
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from zipfile import ZipFile

import pytest

from tools.v131_workspace_benchmark import (run,run_local_workspace_benchmark,
    seed_workspace,workspace_server,response_http,verify_result,pct)
from tools.packaging.release_assets import asset_names,checksum_file
from tools.packaging.release_gate import verify_stable
from tools.sign_release import sign
from paper_radar.portal_auth import db as auth_db
from paper_radar.migrations import REGISTRIES,status
from paper_radar.updates import UpdateChecker
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption,PublicFormat

ROOT=Path(__file__).resolve().parents[1]


def test_v131_schema_index_and_append_only():
    steps=REGISTRIES['portal_workspace']
    assert len(steps)>=9 and steps[8].name=='thousand_paper_workspace_indexes'
    assert tuple(s.version for s in steps)==tuple(range(1,len(steps)+1))
    statements=' '.join(steps[8].statements)
    for table in ['papers','topic_paper_flags','verified_evidence','evidence_reviews']:
        assert table in statements


def test_one_thousand_papers_query_consistency_and_isolation():
    with workspace_server(papers=1000) as env:
        paths=['/api/public/dashboard?topic_id=default',
               '/api/public/papers?topic_id=default&limit=30&sort=recent',
               '/api/public/papers?topic_id=default&limit=30&q=Neural%20Operator',
               '/api/public/papers?topic_id=default&limit=30&filter=starred',
               '/api/public/papers?topic_id=default&limit=30&filter=done',
               '/api/public/papers?topic_id=default&limit=30&sort=title',
               '/api/public/papers?topic_id=default&paper_id=synthetic%3Av131%3A000011']
        names=['dashboard','recent','search','starred','done','title_sort','paper_detail']
        for name,path in zip(names,paths):
            res=response_http(env['base'],env['token'],path)
            assert verify_result(name,res,1000)==(True,'')
        unauth=response_http(env['base'],'not-a-valid-cookie',paths[1])
        assert unauth['status']==401
        reject=response_http(env['base'],env['token'],'/api/public/papers/flags',
                method='POST',csrf='WRONG',body={'topic_id':'default','paper_id':'synthetic:v131:000011','is_starred':True})
        assert reject['status']==403
        with env['portal'].workspace(env['user']['id']) as db:
            index_names={row[1] for row in db.execute("PRAGMA index_list('papers')")}
            assert 'idx_portal_papers_recent' in index_names
            assert status(db,'portal_workspace')['current']


def test_isolated_local_sqlite_one_thousand_papers_persistence():
    result=run_local_workspace_benchmark(papers=1000,repetitions=36)
    assert result['paper_count']==1000
    assert result['error_rate']==0
    assert result['persisted_flags_after_reopen']
    assert result['p95_ms'] is not None


def test_load_runner_emits_true_traffic_latencies_and_error_rate():
    data=run(papers=1000,requests=32,concurrency=4,soak_seconds=1,p95_budget_ms=5000,enforce=True)
    assert data['gate']['passed']
    assert data['load']['requests']==32
    assert sum(v['requests'] for v in data['load']['breakdown'].values())==32
    assert data['load']['completion_rate']==1
    assert data['stability']['requests']>=1
    assert data['local_workspace']['persisted_flags_after_reopen']
    assert data['dataset']['kind']=='deterministic_synthetic_metadata'


def _release_fixture(tmp_path):
    version='1.31.0'
    source=tmp_path/f'Paper-Radar_v{version}_Source.zip'
    for name in asset_names(version):
        f=tmp_path/name
        if name.endswith('.deb'):
            f.write_bytes(b'!<arch>\n'+b'fixture-not-a-real-deb')
        else:
            with ZipFile(f,'w') as z:z.writestr('Paper-Radar/README.txt','synthetic fixture')
        checksum_file(f)
    private=Ed25519PrivateKey.generate()
    raw_pub=private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    kid='paper-radar-2026-'+hashlib.sha256(raw_pub).hexdigest()[:12]
    trust=tmp_path/'trusted_keys.json'
    trust.write_text(json.dumps({'format':1,'keys':[{'id':kid,'algorithm':'Ed25519',
           'public_key_base64':base64.b64encode(raw_pub).decode()}]}))
    key=tmp_path/'private.key'
    key.write_bytes(private.private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()))
    sign(source,version,key,tmp_path)
    return version,trust


def test_signed_stable_gate_accepts_exact_digest_and_pinned_key(tmp_path):
    version,trust=_release_fixture(tmp_path)
    result=verify_stable(tmp_path,version,trust)
    assert result['source_signature_verified']
    assert result['verified_assets']==6
    assert result['signed_key_id'].startswith('paper-radar-2026-')


def test_signed_stable_gate_rejects_tamper_missing_signature_and_key_rotation(tmp_path):
    version,trust=_release_fixture(tmp_path)
    path=tmp_path/f'Paper-Radar_v{version}_release.sig'
    valid=path.read_bytes()
    path.write_bytes(b'a'*88)
    with pytest.raises(Exception):verify_stable(tmp_path,version,trust)
    path.write_bytes(valid)
    with pytest.raises(Exception):verify_stable(tmp_path,version,ROOT/'paper_radar/release_public_keys.json')
    path.unlink()
    with pytest.raises(ValueError,match='requires detached'):verify_stable(tmp_path,version,trust)


def test_frozen_build_still_checks_updates_but_cannot_hot_install(tmp_path,monkeypatch):
    # A frozen bundle can notify users about a new stable Release; the source
    # update handoff is prohibited, even when notification metadata is valid.
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    checker=UpdateChecker(tmp_path/'status.json',fetcher=lambda:{
        'state':'available','latest_version':'1.99.0','tag_name':'v1.99.0',
        'release_url':'https://github.com/QiQiWan/Paper-Radar/releases/tag/v1.99.0'})
    assert checker.schedule({'update_check_enabled':True},force=True)
    deadline=time.monotonic()+3
    while checker.status({'update_check_enabled':True})['checking'] and time.monotonic()<deadline:
        time.sleep(.01)
    state=checker.status({'update_check_enabled':True})
    assert state['state']=='available'
    assert state['can_install'] is False
    assert state['update_method']=='native_package_manual'
    assert state['checking'] is False


def test_workflow_has_browser_performance_native_and_signed_release_gates():
    text=(ROOT/'.github/workflows/desktop-release.yml').read_text(encoding='utf-8')
    for needle in ['v131_workspace_benchmark','v131_browser_journeys','--enforce',
                   'v131_native_smoke','release_gate','PAPER_RADAR_RELEASE_SIGNING_KEY_PEM','PAPER_RADAR_NATIVE_RELEASE_APPROVED','promote_tag','gh release download',
                   'stable=false','stable=true','--draft=false']:
        assert needle in text
    assert 'prerelease_flag="--prerelease"' in text
    assert 'No developer signing key' in text
    assert 'can_install=not getattr(sys' in (ROOT/'paper_radar/updates.py').read_text()


def test_browser_journeys_use_real_dom_not_mock_page():
    text=(ROOT/'tools/v131_browser_journeys.py').read_text(encoding='utf-8')
    assert 'sync_playwright' in text and 'page.locator' in text
    assert 'seed_workspace' in text and 'page.reload' in text
    assert 'not_human_study' in text
    assert 'ERR_BLOCKED_BY_ADMINISTRATOR' not in text or 'navigation preflight' in text


def test_human_task_aggregation_uses_observed_samples_only():
    from tools.v131_human_study import aggregate,TASKS
    assert len(TASKS)>=10
    result=aggregate([{'tasks':[{'success':True,'seconds':3.5,'error_count':0},
                                {'success':False,'seconds':5.1,'error_count':2}]}])
    assert result['participants']==1
    assert result['completion_rate']==0.5
    assert result['interaction_errors_per_task']==1
    assert result['p95_task_ms']==5100
    assert aggregate([])['completion_rate'] is None


def test_native_smoke_harness_with_actual_local_service_and_relocated_shim(tmp_path):
    """Exercise smoke-check logic without claiming any PyInstaller binary was built."""
    import os
    if os.name!='posix':pytest.skip('POSIX test shim')
    import subprocess
    from tools.v131_native_smoke import run as smoke
    bundle=tmp_path/'Paper-Radar'
    bundle.mkdir()
    fake=bundle/'Paper-Radar'
    fake.write_text('#!/bin/sh\nexec '+sys.executable+
      ' -c '+"'import sys;sys.frozen=True;from paper_radar.desktop_app import main;raise SystemExit(main())'"+
      ' "$@"\n')
    fake.chmod(0o755)
    archive=tmp_path/'shim.zip'
    with ZipFile(archive,'w') as zipped:zipped.writestr('Paper-Radar/readme.txt','test')
    checksum_file(archive)
    result=smoke(bundle=bundle,executable_relative='Paper-Radar',archive=archive,
                 out=tmp_path/'smoke.json')
    assert result['checks']['doctor']=='pass'
    assert result['checks']['restart']=='pass'
    assert result['checks']['api']=='pass'
    assert result['checks']['archive_checksum']=='pass'


def test_v131_authenticated_concurrent_mutations_and_reopen():
    from tools.v131_mutation_benchmark import run as benchmark
    result=benchmark(papers=1000,writes=24,concurrency=4,enforce=True)
    assert result['completed']==24
    assert result['persisted_after_reopen']==24
    assert result['p95_ms'] is not None
    assert result['gate']['passed']


def test_v8_to_v9_index_migration_preserves_papers_flags_and_reopens(tmp_path,monkeypatch):
    import sqlite3
    from paper_radar.migrations import migrate,MigrationError
    original=REGISTRIES['portal_workspace']
    path=tmp_path/'legacy_v8.sqlite3'
    monkeypatch.setitem(REGISTRIES,'portal_workspace',original[:8])
    with sqlite3.connect(path) as db:
        assert migrate(db,'portal_workspace')==8
        db.execute('INSERT INTO papers VALUES (?,?,?,?,?,?,?)',
                   ('archived','Archived paper','Historical abstract','2025-05-05','', '', '2025-05-05'))
        db.execute('INSERT INTO topic_papers VALUES (?,?)',('default','archived'))
        db.execute("INSERT INTO topic_paper_flags VALUES ('default','archived',1,'reading',1,'2025-05-06')")
        db.commit()
    monkeypatch.setitem(REGISTRIES,'portal_workspace',original)
    with sqlite3.connect(path) as db:
        assert migrate(db,'portal_workspace')==len(original)
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('SELECT title FROM papers WHERE id="archived"').fetchone()[0]=='Archived paper'
        assert db.execute('SELECT is_starred,reading_status,feedback FROM topic_paper_flags').fetchone()==(1,'reading',1)
        assert migrate(db,'portal_workspace')==len(original)
        index={r[1] for r in db.execute("PRAGMA index_list('papers')")}
        assert 'idx_portal_papers_recent' in index
    monkeypatch.setitem(REGISTRIES,'portal_workspace',original[:8])
    with sqlite3.connect(path) as db:
        with pytest.raises(MigrationError,match='newer'):
            migrate(db,'portal_workspace')
