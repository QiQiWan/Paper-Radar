# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Post-package native smoke, using real frozen executables on each OS runner.

This verifies a portable install/relocation, cold starts, restarts, and user
state persistence. It does not claim code signing, notarization or an upgrade
from a different historical executable.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen

from paper_radar import __version__
from tools.packaging.release_assets import verify_zip, sha256


def _port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def _get(url,*,timeout=2):
    with urlopen(url,timeout=timeout) as response:
        raw=response.read(1000000)
        return response.status,raw


def _start_and_probe(binary,env,log,*,max_wait=25):
    port=_port()
    started=time.perf_counter()
    with log.open('a',encoding='utf-8') as stream:
        process=subprocess.Popen([str(binary),'--serve','--port',str(port)],
            stdout=stream,stderr=subprocess.STDOUT,env=env,stdin=subprocess.DEVNULL)
    try:
        health=None
        deadline=time.monotonic()+max_wait
        while time.monotonic()<deadline:
            if process.poll() is not None:break
            try:
                status,body=_get(f'http://127.0.0.1:{port}/api/health',timeout=.4)
                health=json.loads(body)
                if status==200 and health.get('version')==__version__:break
            except (OSError,ValueError,TimeoutError):pass
            time.sleep(.15)
        if not health or health.get('version')!=__version__:
            raise RuntimeError('Frozen local HTTP server did not become healthy')
        startup_ms=(time.perf_counter()-started)*1000
        status,html=_get(f'http://127.0.0.1:{port}/app/library')
        if status!=200 or b'Paper-Radar' not in html:
            raise RuntimeError('Frozen HTML workspace route failed')
        status,data=_get(f'http://127.0.0.1:{port}/api/papers?limit=2')
        if status!=200 or not isinstance(json.loads(data).get('papers'),list):
            raise RuntimeError('Frozen papers API failed')
        return round(startup_ms,2)
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)


def run(*,bundle:Path, executable_relative:str, archive:Path|None=None, deb:Path|None=None,
        out:Path|None=None):
    bundle=Path(bundle).resolve()
    relative=Path(executable_relative)
    binary=bundle/relative
    if relative.is_absolute() or '..' in relative.parts or not binary.is_file():
        raise ValueError('Binary must be within an existing bundle')
    begin=time.perf_counter()
    checks={'archive_checksum':None,'debian_metadata':None}
    if archive:
        archive=Path(archive)
        verify_zip(archive)
        sha_file=archive.with_name(archive.name+'.sha256')
        expected=f'{sha256(archive)}  {archive.name}\n'
        if not sha_file.is_file() or sha_file.read_text(encoding='ascii')!=expected:
            raise RuntimeError('Release archive SHA-256 does not match published sidecar')
        checks['archive_checksum']='pass'
    if deb:
        deb=Path(deb)
        if not deb.is_file() or deb.open('rb').read(8)!=b'!<arch>\n':
            raise RuntimeError('Invalid DEB format')
        listing=subprocess.run(['dpkg-deb','--info',str(deb)],capture_output=True,text=True,check=True)
        if f'Version: {__version__}' not in listing.stdout:
            raise RuntimeError('DEB version does not match current code')
        checks['debian_metadata']='pass'
    with tempfile.TemporaryDirectory(prefix='paper-radar-v131-native-') as td:
        base=Path(td)
        data=base/'user-data'
        data.mkdir()
        # Always run from a relocated, clean installation path rather than build output.
        relocated=base/'installed'/bundle.name
        relocated.parent.mkdir()
        shutil.copytree(bundle,relocated,symlinks=True)
        installed_binary=relocated/relative
        env=dict(os.environ,PAPER_RADAR_USER_HOME=str(data))
        doctor_path=base/'doctor.json'
        start=time.perf_counter()
        result=subprocess.run([str(installed_binary),'--doctor-output',str(doctor_path)],
            capture_output=True,text=True,timeout=35,env=env)
        if result.returncode!=0 or not doctor_path.is_file():
            raise RuntimeError('Frozen executable doctor failed; return '+str(result.returncode))
        payload=json.loads(doctor_path.read_text(encoding='utf-8'))
        if payload.get('version')!=__version__ or Path(payload.get('data_dir','')).resolve()!=data.resolve():
            raise RuntimeError('Frozen version or per-user data path mismatch')
        doctor_ms=(time.perf_counter()-start)*1000
        marker=data/'v131_persistence_marker.txt'
        marker.write_text('synthetic acceptance marker; no user data',encoding='utf-8')
        samples=[]
        log=base/'service.log'
        for _ in range(2):
            samples.append(_start_and_probe(installed_binary,env,log))
        if marker.read_text(encoding='utf-8')!='synthetic acceptance marker; no user data':
            raise RuntimeError('Frozen restart erased per-user files')
        # Startup failures retain only a short log excerpt for debugging.
        log_tail=log.read_text(encoding='utf-8',errors='replace')[-900:]
    report={'version':__version__,'kind':'native_frozen_relocated_install_smoke',
        'os':platform.platform(),'architecture':platform.machine(),
        'bundle':bundle.name,'executable':executable_relative,
        'checks':{**checks,'doctor':'pass','user_data_isolation':'pass',
                  'cold_start':'pass','restart':'pass','route':'pass','api':'pass',
                  'persistence':'pass'},
        'doctor_ms':round(doctor_ms,2),'service_start_ms':samples,
        'completion_rate':1.0,'error_rate':0.0,
        'limitations':['Same-version relocation only; prior-version native upgrade untested',
                       'OS publisher signature, notarization and end-user security prompts not verified'],
        'total_seconds':round(time.perf_counter()-begin,2),
        'at':datetime.now(timezone.utc).isoformat(timespec='seconds')}
    if out:
        out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'checks':report['checks'],'starts_ms':samples,'total_seconds':report['total_seconds']},ensure_ascii=False))
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--executable-relative',required=True)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--deb',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args(argv)
    run(bundle=args.bundle,executable_relative=args.executable_relative,archive=args.archive,
        deb=args.deb,out=args.out)

if __name__=='__main__':main()
