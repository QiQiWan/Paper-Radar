# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Inspect local SQLite storage and optionally copy it to a non-synced home.

No database repair, deletion, or implicit relocation is performed. Migration
uses sqlite3.backup for every SQLite database (including WAL transactions),
verifies destination integrity, then atomically installs a pointer. All old
files remain intact for manual rollback. Stop the Paper-Radar service first.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from paper_radar.storage_layout import POINTER_FILE, resolve_data_root  # noqa: E402

USER_ITEMS = ('settings.json', 'settings.json.bak', 'data', 'downloads', 'reports', 'logs')
SYNC_FOLDERS = ('onedrive', 'dropbox', 'google drive', 'icloud drive')

def _in_sync_folder(path: Path) -> bool:
    return any(name in str(path).casefold() for name in SYNC_FOLDERS)



def database_check(path: Path) -> dict:
    state = {'path':str(path), 'exists': path.exists(), 'size_bytes':0,
             'quick_check':'not_run', 'sqlite_version':None}
    if not state['exists']:
        state['quick_check']='missing'
        return state
    try:
        state['size_bytes']=path.stat().st_size
        # URI read-only prevents the checker from silently creating an empty DB.
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
        try:
            db.execute('PRAGMA query_only=ON')
            outcome=db.execute('PRAGMA quick_check').fetchone()
            state['quick_check']=str(outcome[0]) if outcome else 'no_result'
            state['sqlite_version']=db.execute('PRAGMA user_version').fetchone()[0]
        finally: db.close()
    except (sqlite3.DatabaseError, OSError, PermissionError) as exc:
        state['quick_check']='failed'
        state['error_type']=type(exc).__name__
        state['error_code']=getattr(exc,'sqlite_errorname',None)
    return state


def inspect(install: Path = ROOT) -> dict:
    install=Path(install).resolve()
    home=resolve_data_root(install, create=False)
    folder=home/'data'
    dbs=[]
    if folder.exists():
        dbs=[database_check(f) for f in sorted(folder.rglob('*.sqlite3'))
             if f.is_file() and not f.is_symlink()]
    try:
        disk=shutil.disk_usage(home if home.exists() else home.parent)
        available=disk.free
    except OSError:
        available=None
    return {'install_dir':str(install), 'data_dir':str(home),
            'data_separated':home!=install,
            'cloud_sync_path':_in_sync_folder(home),
            'disk_free_bytes':available,
            'database_files':dbs,
            'sqlite_sidecars':[f.name for f in sorted(folder.glob('*.sqlite3-*'))] if folder.exists() else [],
            'next_step':('建议先停止本地服务，再迁移数据到非 OneDrive 目录。'
                if _in_sync_folder(home) else
                '如果检查失败，请先备份原始数据库；禁止直接删除 WAL、SHM 或执行覆盖式修复。')}


def _copy_db(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True,exist_ok=True)
    # SQLite backup handles active WAL pages and does not move/write source.
    original=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=8)
    try:
        replica=sqlite3.connect(dest,timeout=8)
    except BaseException:
        original.close()
        raise
    try:
        original.backup(replica,pages=300,sleep=.05)
        replica.commit()
    finally:
        replica.close()
        original.close()
    verified=database_check(dest)
    if verified['quick_check']!='ok':
        raise RuntimeError('SQLite 备份校验失败：'+str(dest))


def _offline_copy_database(source: Path, dest: Path) -> None:
    """Last resort. Explicit opt-in; requires all Paper-Radar processes stopped.

    Copy the exact database and existing WAL files before opening the clone.
    Never attempt recovery in place; reject the entire migration if the clone
    cannot pass SQLite quick_check.
    """
    if dest.exists(): dest.unlink()
    shutil.copy2(source,dest)
    for ext in ('-wal','-shm'):
        sidecar=source.with_name(source.name+ext)
        if sidecar.exists():shutil.copy2(sidecar,dest.with_name(dest.name+ext))
    replica=sqlite3.connect(dest,timeout=5)
    try:
        result=replica.execute('PRAGMA quick_check').fetchone()
        if not result or result[0]!='ok':
            raise RuntimeError('离线副本完整性检查失败，保留原文件。')
        replica.execute('PRAGMA wal_checkpoint(FULL)')
    finally:
        replica.close()
    if database_check(dest)['quick_check']!='ok':
        raise RuntimeError('离线副本二次完整性检查失败，禁止启用。')


def _copy_plain(src: Path, dst: Path, *, offline_recovery: bool=False) -> None:
    if src.is_symlink():
        raise RuntimeError('拒绝复制符号链接：'+str(src))
    if src.is_dir():
        dst.mkdir(parents=True,exist_ok=True)
        for item in sorted(src.iterdir()):
            if item.name.endswith(('-wal','-shm','-journal')):
                # SQLite snapshots are self-contained; never raw-copy journals.
                continue
            _copy_plain(item,dst/item.name,offline_recovery=offline_recovery)
    elif src.suffix.lower() in {'.sqlite3','.sqlite','.db'}:
        try:
            _copy_db(src,dst)
        except (sqlite3.DatabaseError, OSError, RuntimeError):
            if not offline_recovery:raise
            _offline_copy_database(src,dst)
    elif src.is_file():
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,dst)
        if src.stat().st_size!=dst.stat().st_size:
            raise RuntimeError('文件复制校验失败：'+str(src))
        if src.stat().st_size<=50*1024*1024:
            def sha(path):
                digest=hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):
                        digest.update(chunk)
                return digest.digest()
            if sha(src)!=sha(dst):
                raise RuntimeError('文件 SHA-256 校验失败：'+str(src))
    else:
        raise RuntimeError('无法识别的用户文件：'+str(src))


def migrate(install:Path,target:Path,*,confirmed:bool=False,
            offline_recovery:bool=False) -> dict:
    install=Path(install).resolve()
    source=resolve_data_root(install,create=False)
    target=Path(target).expanduser()
    if not target.is_absolute():
        raise ValueError('目标必须使用绝对路径。')
    target=target.resolve()
    if not confirmed:
        raise ValueError('迁移需要 --yes；先关闭所有 Paper-Radar 进程并完成备份。')
    if target==source or source in target.parents or target in source.parents:
        raise ValueError('目标路径不可位于原数据目录之内，也不能包含原数据目录。')
    if _in_sync_folder(target):
        raise ValueError('迁移目标不能位于云盘同步目录。')
    if target.is_symlink() or (target.exists() and any(target.iterdir())):
        raise FileExistsError('迁移目标必须不存在或为空目录，禁止覆盖现有资料。')
    pointer=install/POINTER_FILE
    if pointer.is_symlink() or pointer.exists():
        raise FileExistsError('已有数据目录指向文件，请先确认旧迁移状态。')
    if target.exists():target.rmdir()
    target.parent.mkdir(parents=True,exist_ok=True)
    # Fail before copying when a destination cannot accommodate the source.
    # SQLite may require additional temporary pages during backup; use a margin.
    estimate=0
    for name in USER_ITEMS:
        item=source/name
        if item.is_file(): estimate+=item.stat().st_size
        elif item.is_dir():
            for file in item.rglob('*'):
                if file.is_file() and not file.is_symlink(): estimate+=file.stat().st_size
    disk_free=shutil.disk_usage(target.parent).free
    if disk_free < int(estimate * 1.3) + 30*1024*1024:
        raise OSError('目标磁盘空间不足，请至少预留原始数据总量的 1.3 倍及 30 MB。')
    stage=target.parent/('.paper-radar-migrate-'+uuid4().hex[:10])
    stage.mkdir()
    try:
        for name in USER_ITEMS:
            src=source/name
            if src.exists():_copy_plain(src,stage/name,offline_recovery=offline_recovery)
        original=inspect(install)
        for item in original['database_files']:
            if item['quick_check']!='ok' and not offline_recovery:
                raise RuntimeError('原数据库完整性检查未通过，停止切换：'+item['path'])
        for replica in (stage/'data').rglob('*.sqlite3') if (stage/'data').exists() else ():
            if database_check(replica)['quick_check']!='ok':
                raise RuntimeError('新数据库完整性检查未通过，停止切换：'+str(replica))
        manifest={'source':str(source),'target':str(target),
                 'created_at':datetime.now(timezone.utc).isoformat(),
                 'original_files_kept':True,'databases_checked':len(original['database_files']),
                 'offline_copy_requested':offline_recovery}
        (stage/'MIGRATION_RECEIPT.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(stage,target)
        pointer_tmp=install/(POINTER_FILE+'.tmp')
        try:
            pointer_tmp.write_text(str(target)+'\n',encoding='utf-8')
            os.replace(pointer_tmp,pointer)
        finally:
            if pointer_tmp.exists():pointer_tmp.unlink()
        return manifest
    except Exception:
        # Do not delete a potentially valuable partial copy. Pointer untouched.
        raise


def main(argv=None) -> int:
    ap=argparse.ArgumentParser(description='Paper-Radar 论文库存储检查与安全迁移')
    ap.add_argument('--root',type=Path,default=ROOT)
    ap.add_argument('--check',action='store_true',help='只检查，默认模式')
    ap.add_argument('--migrate',action='store_true',help='停止服务后复制到新目录')
    ap.add_argument('--to',type=Path)
    ap.add_argument('--yes',action='store_true',help='确认已停止服务，并接受保留全部原文件')
    ap.add_argument('--offline-recovery',action='store_true',help='备份接口失败时尝试停机后原样复制数据库与 WAL，再校验副本')
    ap.add_argument('--report',type=Path,help='将诊断 JSON 保存到指定文件')
    args=ap.parse_args(argv)
    if args.migrate and args.to is None:ap.error('--migrate 必须带 --to')
    try:
        status=migrate(args.root,args.to,confirmed=args.yes,
            offline_recovery=args.offline_recovery) if args.migrate else inspect(args.root)
        report=json.dumps(status,ensure_ascii=False,indent=2)
        print(report)
        if args.report:
            args.report.parent.mkdir(parents=True,exist_ok=True)
            args.report.write_text(report+'\n',encoding='utf-8')
        return 0 if args.migrate or all(d['quick_check']=='ok' for d in status['database_files']) else 2
    except (OSError, ValueError, RuntimeError, sqlite3.DatabaseError) as exc:
        print('存储检查未通过：'+type(exc).__name__+'：'+str(exc),file=sys.stderr)
        print('旧数据库和个人文件均未删除。',file=sys.stderr)
        return 3

if __name__=='__main__':raise SystemExit(main())
