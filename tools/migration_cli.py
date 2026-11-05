#!/usr/bin/env python3
"""Inspect/migrate/backup an offline SQLite database before a version change."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
import sqlite3
from pathlib import Path
from paper_radar.migrations import migrate,status,REGISTRIES


def main(argv=None):
    parser=argparse.ArgumentParser(description='Paper-Radar SQLite migration inspection')
    parser.add_argument('action',choices=('status','apply','backup'))
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--kind',choices=REGISTRIES,required=True)
    parser.add_argument('--backup-dir',type=Path)
    args=parser.parse_args(argv)
    db=args.database.resolve()
    if not db.is_file() and args.action!='apply':raise SystemExit('Database file not found')
    if args.action=='status':
        con=sqlite3.connect(f'file:{db}?mode=ro',uri=True)
        try:print(json.dumps(status(con,args.kind),ensure_ascii=False))
        finally:con.close()
        return 0
    if db.is_file():
        folder=(args.backup_dir or db.parent/'migration_backups').resolve()
        folder.mkdir(parents=True,exist_ok=True)
        target=folder/(db.stem+'_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'.sqlite3')
        with sqlite3.connect(db) as source, sqlite3.connect(target) as dst:
            source.backup(dst)
        print('Backup:',target)
    if args.action=='backup':return 0
    if args.kind=='research':
        # Existing research tables must be normalized before running their
        # versioned *new* migrations. Backup above happens beforehand.
        from paper_radar.core import connect
        con=connect(db)
    else:
        con=sqlite3.connect(db)
        migrate(con,args.kind)
    try:print(json.dumps(status(con,args.kind),ensure_ascii=False))
    finally:con.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
