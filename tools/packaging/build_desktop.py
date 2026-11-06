# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""OS-native PyInstaller desktop bundle builder.

Run this script on EACH target OS. It cannot cross-compile. Do not bundle
users' source trees, environment files, credentials, data, or private PDFs.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys
import zipfile
import shutil

ROOT=Path(__file__).resolve().parent.parent.parent


def build(*, output:Path|None=None):
    output=output or ROOT/'dist'
    output.mkdir(parents=True,exist_ok=True)
    sep=';' if sys.platform=='win32' else ':'
    cmd=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed',
         '--onedir','--name','Paper-Radar',
         '--distpath',str(output),
         '--workpath',str(ROOT/'build'/'desktop'),
         '--specpath',str(ROOT/'build'),
         '--collect-submodules','paper_radar',
         '--hidden-import','tkinter',
         '--add-data',str(ROOT/'paper_radar'/'static')+sep+'paper_radar/static',
         '--add-data',str(ROOT/'docs')+sep+'docs',
         '--add-data',str(ROOT/'paper_radar'/'release_public_keys.json')+sep+'paper_radar',
         str(ROOT/'paper_radar'/'desktop_app.py')]
    subprocess.run(cmd,check=True,cwd=ROOT)
    if sys.platform=='win32':
        # Separate console-mode worker avoids the stdio=None behavior of
        # Windows PyInstaller --windowed processes for source/PDF pipes.
        service_cmd=cmd.copy()
        service_cmd[service_cmd.index('--windowed')]='--console'
        service_cmd[service_cmd.index('Paper-Radar')]='Paper-Radar-Service'
        subprocess.run(service_cmd,check=True,cwd=ROOT)
        service=output/'Paper-Radar-Service'/'Paper-Radar-Service.exe'
        if not service.is_file():raise RuntimeError('Windows service sidecar not built')
        shutil.copy2(service,output/'Paper-Radar'/'Paper-Radar-Service.exe')
    if sys.platform=='darwin':
        target=output/'Paper-Radar.app'
        if not target.is_dir():raise RuntimeError('macOS .app was not built')
    elif sys.platform=='win32':
        target=output/'Paper-Radar'/'Paper-Radar.exe'
        if not target.is_file():raise RuntimeError('Windows .exe was not built')
    else:
        target=output/'Paper-Radar'/'Paper-Radar'
        if not target.is_file():raise RuntimeError('Linux executable was not built')
    # Never run tests against source and package without independent smoke.
    print('Created',target)
    return target


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'dist')
    args=parser.parse_args();build(output=args.output)
