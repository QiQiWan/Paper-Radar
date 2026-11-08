"""Operator CLI for portable verified local data snapshots."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from paper_radar.core import APP_ROOT
from paper_radar.backups import create_backup,restore_backup,verify_backup


def main(argv=None):
    p=argparse.ArgumentParser(description='Paper-Radar 数据备份与完整性验收')
    commands=p.add_subparsers(dest='action',required=True)
    c=commands.add_parser('create');c.add_argument('--root',type=Path,default=APP_ROOT)
    c.add_argument('--output',type=Path);c.add_argument('--metadata-only',action='store_true')
    v=commands.add_parser('verify');v.add_argument('snapshot',type=Path)
    r=commands.add_parser('restore');r.add_argument('snapshot',type=Path);r.add_argument('--to',required=True,type=Path)
    a=p.parse_args(argv)
    if a.action=='create':res=create_backup(a.root,destination=a.output,include_pdfs=not a.metadata_only)
    elif a.action=='verify':res=verify_backup(a.snapshot)
    else:res=restore_backup(a.snapshot,a.to)
    print(json.dumps(res,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
