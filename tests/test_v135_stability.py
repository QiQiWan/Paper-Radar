"""v1.35 stability: WAL, verified restore, corruption and non-overwriting."""
import json
from pathlib import Path
import sqlite3

import pytest
from paper_radar.backups import BackupError,create_backup,restore_backup,verify_backup


def local_workspace(tmp_path):
    root=tmp_path/'research'
    (root/'data').mkdir(parents=True)
    (root/'downloads').mkdir()
    (root/'reports').mkdir()
    (root/'settings.json').write_text('{"keywords":["operator learning"]}',encoding='utf-8')
    db=sqlite3.connect(root/'data'/'papers.sqlite3')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE papers(id INT PRIMARY KEY,title TEXT NOT NULL)')
    db.executemany('INSERT INTO papers VALUES (?,?)',[(i,f'paper{i}') for i in range(37)])
    db.commit()
    (root/'downloads'/'article.pdf').write_bytes(b'%PDF-1.5\nlocal pdf\n%%EOF')
    (root/'reports'/'research.md').write_text('结论\n',encoding='utf-8')
    return root,db


def test_wal_snapshot_can_restore_to_new_location(tmp_path):
    root,connection=local_workspace(tmp_path)
    try:
        report=create_backup(root,destination=tmp_path/'safe-archive')
        assert report['verified']
        assert report['files']>=4
        assert verify_backup(report['path'])['ok']
        target=tmp_path/'fresh-research'
        restored=restore_backup(report['path'],target)
        assert restored['restored']
        with sqlite3.connect(target/'data'/'papers.sqlite3') as db:
            assert db.execute('SELECT COUNT(*) FROM papers').fetchone()[0]==37
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert (target/'downloads'/'article.pdf').read_bytes().startswith(b'%PDF-')
        assert (target/'reports'/'research.md').read_text(encoding='utf-8')=='结论\n'
        assert (root/'data'/'papers.sqlite3').exists()
    finally:connection.close()


def test_backup_tampering_detected(tmp_path):
    root,db=local_workspace(tmp_path)
    try:report=create_backup(root,destination=tmp_path/'archive')
    finally:db.close()
    snapshot=Path(report['path'])
    (snapshot/'settings.json').write_text('modified')
    with pytest.raises(BackupError,match='校验失败|长度改变'):
        verify_backup(snapshot)
    assert not (tmp_path/'restored').exists()
    with pytest.raises(BackupError):restore_backup(snapshot,tmp_path/'restored')


def test_restore_never_overwrites_existing_research(tmp_path):
    root,db=local_workspace(tmp_path)
    try:report=create_backup(root,destination=tmp_path/'archive')
    finally:db.close()
    target=tmp_path/'existing';target.mkdir()
    (target/'settings.json').write_text('DO NOT DELETE')
    with pytest.raises(BackupError):restore_backup(report['path'],target)
    assert (target/'settings.json').read_text()=='DO NOT DELETE'


def test_restore_rejects_path_traversal(tmp_path):
    root,db=local_workspace(tmp_path)
    try:report=create_backup(root,destination=tmp_path/'archive')
    finally:db.close()
    snapshot=Path(report['path'])
    manifest=snapshot/'manifest.json'
    content=json.loads(manifest.read_text())
    content['files'][0]['path']='../../secrets.txt'
    manifest.write_text(json.dumps(content))
    with pytest.raises(BackupError):restore_backup(snapshot,tmp_path/'empty')
    assert not (tmp_path/'secrets.txt').exists()


def test_backup_rejects_symbolic_link_escape(tmp_path):
    root,db=local_workspace(tmp_path)
    try:
        outside=tmp_path/'secret.txt';outside.write_text('DO NOT COPY')
        try:(root/'data'/'outside').symlink_to(outside)
        except OSError:pytest.skip('Cannot create symlinks on this platform')
        with pytest.raises(BackupError,match='符号链接'):
            create_backup(root,destination=tmp_path/'safe-archive')
    finally:db.close()


def test_restore_rejects_parent_symlink_in_snapshot(tmp_path):
    root,db=local_workspace(tmp_path)
    try:report=create_backup(root,destination=tmp_path/'archive')
    finally:db.close()
    snapshot=Path(report['path'])
    outside=tmp_path/'outside-content'
    (outside/'data').mkdir(parents=True)
    # Attackers can replace the whole archived directory with a link while
    # leaving files with the same hashes in the linked directory.
    import shutil
    shutil.copytree(snapshot/'data',outside/'data',dirs_exist_ok=True)
    shutil.rmtree(snapshot/'data')
    try:(snapshot/'data').symlink_to(outside/'data',target_is_directory=True)
    except OSError:pytest.skip('Cannot create directory symlinks on this platform')
    with pytest.raises(BackupError,match='符号链接'):
        restore_backup(snapshot,tmp_path/'fresh')
    assert not (tmp_path/'fresh').exists()


def test_automatic_rotation_never_deletes_manual_full_backup(tmp_path):
    from datetime import datetime,timezone,timedelta
    from paper_radar.backups import backup_if_due
    root,db=local_workspace(tmp_path)
    try:
        folder=root/'backups'
        manual=create_backup(root,destination=folder,include_pdfs=True)
        assert Path(manual['path']).name.startswith('paper-radar-')
        for index in range(4):
            result=backup_if_due(root,interval_hours=0,keep=2,
                now=datetime.now(timezone.utc)+timedelta(days=index+1))
            assert result['state']=='created'
        assert Path(manual['path']).exists()
        assert verify_backup(manual['path'])['include_pdfs'] is True
        auto=list(folder.glob('paper-radar-auto-*'))
        assert len(auto)==2
        assert all(not verify_backup(item)['include_pdfs'] for item in auto)
    finally:db.close()
