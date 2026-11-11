# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Verified, self-contained backups of local research metadata and files.

SQLite files are captured through sqlite3.Connection.backup to include all
committed WAL pages. Source data is never modified. Restores target an empty,
separate directory and verify every payload before publishing it atomically.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid

DATA_DIRS=('data','downloads','reports')
CONFIG_NAMES=('settings.json','settings.json.bak')
CHUNK=1024*1024


class BackupError(RuntimeError):
    pass


def _hash(path: Path) -> str:
    hasher=sha256()
    with path.open('rb') as file:
        while block:=file.read(CHUNK):
            hasher.update(block)
    return hasher.hexdigest()


def _is_sqlite(path: Path) -> bool:
    return path.suffix.lower() in ('.sqlite3','.sqlite','.db')


def _regular_files(root: Path, *, include_pdfs=True):
    for name in CONFIG_NAMES:
        candidate=root/name
        if candidate.is_symlink():raise BackupError('配置文件不能是符号链接')
        if candidate.is_file():yield candidate
    for name in DATA_DIRS:
        folder=root/name
        if folder.is_symlink():raise BackupError('研究数据目录不能是符号链接')
        if not folder.is_dir():continue
        for base, dirs, files in os.walk(folder,followlinks=False):
            parent=Path(base)
            for subdir in dirs:
                if (parent/subdir).is_symlink():
                    raise BackupError('研究文件包含符号链接目录，已中止备份')
            for filename in sorted(files):
                file=parent/filename
                if file.is_symlink():raise BackupError('研究文件包含符号链接，已中止备份')
                if filename.endswith(('-wal','-shm','.part','.tmp')):
                    continue
                if not include_pdfs and file.suffix.lower()=='.pdf':continue
                if file.is_file():yield file


def _sqlite_backup(source: Path, dest: Path) -> None:
    # Always open read-only: never create an empty replacement at the source.
    if not source.is_file():raise BackupError('数据库文件不存在')
    uri=source.resolve().as_uri()+'?mode=ro'
    # sqlite3.Connection.__exit__ commits but does not close handles. Windows
    # refuses to rename the verified staging directory while SQLite remains
    # open, so close both connections before publishing the snapshot.
    with closing(sqlite3.connect(uri,uri=True,timeout=15)) as original:
        with closing(sqlite3.connect(dest,timeout=15)) as replica:
            original.backup(replica,pages=100,sleep=.05)
            result=replica.execute('PRAGMA integrity_check').fetchone()[0]
            if result!='ok':raise BackupError('备份数据库完整性检查未通过')


def _check_manifest(snapshot: Path) -> dict:
    snapshot=Path(snapshot)
    if snapshot.is_symlink():raise BackupError('备份目录不能是符号链接')
    manifest=snapshot/'manifest.json'
    if manifest.is_symlink() or not manifest.is_file():raise BackupError('备份缺少 manifest.json')
    try:
        content=json.loads(manifest.read_text(encoding='utf-8'))
    except (ValueError,UnicodeError,OSError) as exc:
        raise BackupError('备份清单不可读取') from exc
    if content.get('format')!='paper-radar-backup-v1' or not isinstance(content.get('files'),list):
        raise BackupError('备份清单格式无效')
    if not content['files'] or len(content['files'])>500_000:
        raise BackupError('备份文件数量无效')
    seen=set()
    for row in content['files']:
        if not isinstance(row,dict) or not {'path','bytes','sha256','sqlite'}<=set(row):
            raise BackupError('备份清单条目无效')
        name=row['path']
        if not isinstance(name,str) or '\\' in name or '\x00' in name or name.startswith('/'):
            raise BackupError('备份包含不安全路径')
        relative=Path(name)
        if relative.is_absolute() or not relative.parts or '..' in relative.parts:
            raise BackupError('备份包含目录穿越路径')
        if relative.parts[0] not in (*DATA_DIRS,*CONFIG_NAMES):
            raise BackupError('备份文件范围不受支持')
        if name in seen:raise BackupError('备份存在重复文件路径')
        seen.add(name)
        file=snapshot/relative
        # A listed file can live beneath a malicious symbolic-link directory.
        # Reject every intermediate link before resolving or opening it.
        if any((snapshot/Path(*relative.parts[:i])).is_symlink()
               for i in range(1,len(relative.parts))):
            raise BackupError('备份包含符号链接目录，已中止恢复')
        if not file.resolve().is_relative_to(snapshot.resolve()):
            raise BackupError('备份文件越过快照目录')
        if file.is_symlink() or not file.is_file() or file.stat().st_size!=row['bytes']:
            raise BackupError('备份文件缺失或长度改变：'+name)
        if _hash(file)!=row['sha256']:
            raise BackupError('备份文件校验失败：'+name)
        if row['sqlite']:
            try:
                with closing(sqlite3.connect(file.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                        raise BackupError('备份数据库损坏：'+name)
            except sqlite3.DatabaseError as exc:
                raise BackupError('备份数据库不可读取：'+name) from exc
    return content


def create_backup(root: Path, *, destination: Path | None=None,
                  include_pdfs=True, clock=None, prefix='paper-radar-') -> dict:
    root=Path(root).expanduser()
    if root.is_symlink() or not root.is_dir():raise BackupError('研究数据目录不存在或不安全')
    root=root.resolve()
    destination=Path(destination) if destination is not None else root/'backups'
    if destination.is_symlink():raise BackupError('备份目标不能是符号链接')
    destination.mkdir(parents=True,exist_ok=True)
    destination=destination.resolve()
    for base in DATA_DIRS:
        if destination.is_relative_to(root/base):
            raise BackupError('备份目录不能放在正在备份的数据目录内')
    if prefix not in ('paper-radar-','paper-radar-auto-'):
        raise BackupError('备份类型无效')
    timestamp=(clock or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target=destination/f'{prefix}{timestamp}-{uuid.uuid4().hex[:8]}'
    temp=destination/('.backup-'+uuid.uuid4().hex+'.partial')
    temp.mkdir(mode=0o700)
    files=[]
    try:
        for source in _regular_files(root,include_pdfs=include_pdfs):
            relative=source.relative_to(root)
            output=temp/relative
            output.parent.mkdir(parents=True,exist_ok=True)
            if _is_sqlite(source):
                _sqlite_backup(source,output)
            else:
                # Catch active file writes; copying a changing report/PDF must
                # not create a silently inconsistent archive.
                stat_before=source.stat()
                with source.open('rb') as inp, output.open('xb') as out:
                    shutil.copyfileobj(inp,out,CHUNK)
                stat_after=source.stat()
                if (stat_before.st_size,stat_before.st_mtime_ns)!=(stat_after.st_size,stat_after.st_mtime_ns):
                    raise BackupError('备份期间文件发生修改，请重试：'+str(relative))
            files.append({'path':relative.as_posix(),'bytes':output.stat().st_size,
                          'sha256':_hash(output),'sqlite':_is_sqlite(source)})
        if not files:raise BackupError('没有需要备份的研究数据')
        manifest={'format':'paper-radar-backup-v1','created_at':datetime.now(timezone.utc).isoformat(),
                  'include_pdfs':bool(include_pdfs),'files':files,
                  'file_count':len(files),'total_bytes':sum(x['bytes'] for x in files)}
        (temp/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        _check_manifest(temp)
        temp.rename(target)  # Never overwrite a previously successful backup.
        return {'path':str(target),'files':len(files),'bytes':manifest['total_bytes'],
                'include_pdfs':bool(include_pdfs),'verified':True}
    except BaseException:
        shutil.rmtree(temp,ignore_errors=True)
        raise


def verify_backup(snapshot: Path) -> dict:
    manifest=_check_manifest(Path(snapshot))
    return {'ok':True,'files':manifest['file_count'],
            'bytes':manifest['total_bytes'],'include_pdfs':manifest['include_pdfs']}


def restore_backup(snapshot: Path, destination: Path) -> dict:
    snapshot=Path(snapshot).expanduser()
    if snapshot.is_symlink():raise BackupError('备份目录不能是符号链接')
    snapshot=snapshot.resolve()
    manifest=_check_manifest(snapshot)
    dest=Path(destination).expanduser()
    if dest.is_symlink() or (dest.exists() and (not dest.is_dir() or any(dest.iterdir()))):
        raise BackupError('恢复目标必须是全新或空目录，不能覆盖已有研究数据')
    dest_parent=dest.parent.resolve()
    if not dest_parent.exists():dest_parent.mkdir(parents=True,exist_ok=True)
    if dest_parent==snapshot or dest_parent.is_relative_to(snapshot):
        raise BackupError('恢复目标不能位于备份目录内部')
    stage=dest_parent/('.paper-radar-restore-'+uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    try:
        for row in manifest['files']:
            relative=Path(row['path'])
            target=stage/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(snapshot/relative,target)
        _check_manifest_for_files(stage,manifest)
        if dest.exists():dest.rmdir()  # Empty directory only; no user data removed.
        stage.rename(dest)
        return {'restored':True,'path':str(dest),'files':len(manifest['files'])}
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True)
        raise


def _check_manifest_for_files(stage: Path,manifest: dict) -> None:
    # Validate copies, too; storage media may fail during restore.
    for row in manifest['files']:
        file=stage/row['path']
        if not file.is_file() or file.stat().st_size!=row['bytes'] or _hash(file)!=row['sha256']:
            raise BackupError('恢复副本校验失败：'+row['path'])
        if row['sqlite']:
            with closing(sqlite3.connect(file.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise BackupError('恢复数据库完整性检查失败：'+row['path'])


def backup_if_due(root: Path, *, interval_hours: float=24, keep: int=7,
                  now=None) -> dict:
    """Best-effort rolling *metadata* backups. Runs after a successful cycle.

    Backup timestamps are not inferred from database mtimes. A verified backup
    must be created before an old snapshot is pruned. PDFs require manual full
    backups; an automated metadata snapshot never promises PDF preservation.
    """
    root=Path(root)
    folder=root/'backups'
    if folder.is_symlink():raise BackupError('备份目录不能是符号链接')
    folder.mkdir(parents=True,exist_ok=True)
    items=sorted((p for p in folder.glob('paper-radar-auto-*') if p.is_dir() and not p.is_symlink()),
                 key=lambda p:p.stat().st_mtime,reverse=True)
    current=now or datetime.now(timezone.utc)
    if items and current.timestamp()-items[0].stat().st_mtime < interval_hours*3600:
        return {'state':'up_to_date','path':str(items[0])}
    # A failed backup never triggers pruning. Existing snapshots stay intact.
    report=create_backup(root,destination=folder,include_pdfs=False,clock=current,
                         prefix='paper-radar-auto-')
    retained=[]
    for item in sorted((p for p in folder.glob('paper-radar-auto-*') if p.is_dir() and not p.is_symlink()),
                       key=lambda p:p.stat().st_mtime,reverse=True):
        try:
            if verify_backup(item)['ok']:retained.append(item)
        except (BackupError,OSError,sqlite3.DatabaseError):
            continue
    for old in retained[max(2,min(30,int(keep))):]:
        shutil.rmtree(old)
    return {'state':'created',**report}
