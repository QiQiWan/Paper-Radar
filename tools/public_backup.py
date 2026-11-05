"""Consistent SQLite backup plus tenant PDF files; execute with a restricted OS user."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
from hashlib import sha256
import json
from pathlib import Path
import os
import shutil
import sqlite3


def backup(data_root:Path, output_root:Path):
    data_root=data_root.resolve();output_root=output_root.resolve()
    if not (data_root/'accounts.sqlite3').is_file():raise ValueError('Account DB does not exist')
    if data_root==output_root or data_root in output_root.parents or output_root in data_root.parents:
        raise ValueError('Backup must be stored separately from live data')
    out=output_root/('backup_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f'))
    out.mkdir(parents=True,mode=0o700)
    if os.name!='nt':os.chmod(out,0o700)
    manifest=[]
    try:
        files=[data_root/'accounts.sqlite3']
        users=data_root/'users'
        if users.exists():
            for path in users.rglob('*'):
                if path.is_symlink():raise ValueError('Symlinks are forbidden in tenant data')
                if path.is_file() and path.suffix in ('.sqlite3','.pdf'):files.append(path)
        for src in files:
            if not src.resolve().is_relative_to(data_root):raise ValueError('Path outside tenant root')
            dest=out/src.relative_to(data_root)
            dest.parent.mkdir(parents=True,exist_ok=True)
            if src.suffix=='.sqlite3':
                with sqlite3.connect(f'file:{src}?mode=ro',uri=True) as source:
                    with sqlite3.connect(dest) as target:source.backup(target)
                with sqlite3.connect(dest) as test:
                    if test.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Corrupt SQLite backup')
            else:shutil.copy2(src,dest)
            if os.name!='nt':os.chmod(dest,0o600)
            manifest.append({'file':str(dest.relative_to(out)),'size':dest.stat().st_size,
                'sha256':sha256(dest.read_bytes()).hexdigest()})
        (out/'manifest.json').write_text(json.dumps({'items':manifest},ensure_ascii=False,indent=2),encoding='utf-8')
        return {'directory':str(out),'files':len(manifest)}
    except Exception:
        shutil.rmtree(out,ignore_errors=True)
        raise


def verify(directory:Path):
    directory=directory.resolve()
    items=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))['items']
    for item in items:
        candidate=directory/item['file']
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(directory):
            raise ValueError('Unsafe backup path')
        if candidate.stat().st_size!=item['size'] or sha256(candidate.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('Checksum mismatch')
    return {'ok':True,'verified':len(items)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data-root',type=Path)
    p.add_argument('--output-root',type=Path);p.add_argument('--verify',type=Path);args=p.parse_args()
    if args.verify:result=verify(args.verify)
    elif args.data_root and args.output_root:result=backup(args.data_root,args.output_root)
    else:p.error('Provide --verify DIR or --data-root DIR --output-root DIR')
    print(json.dumps(result,ensure_ascii=False,indent=2))
