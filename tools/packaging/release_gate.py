# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Fail-closed publisher gate for signed *stable* source auto-updates."""
from __future__ import annotations
import argparse
from pathlib import Path
from paper_radar.release_signatures import verify_manifest,SignatureError
from tools.packaging.release_assets import verify,sha256

ROOT=Path(__file__).resolve().parents[2]


def verify_stable(folder:Path,version:str,trust:Path=ROOT/'paper_radar/release_public_keys.json'):
    folder=Path(folder)
    artifacts=verify(folder,version)
    source=folder/f'Paper-Radar_v{version}_Source.zip'
    manifest=folder/f'Paper-Radar_v{version}_release.json'
    signature=folder/f'Paper-Radar_v{version}_release.sig'
    if not manifest.is_file() or not signature.is_file():
        raise ValueError('Signed stable Release requires detached manifest and signature')
    signed=verify_manifest(manifest.read_bytes(),signature.read_bytes(),Path(trust),
        version=version,sha256=sha256(source),archive_size=source.stat().st_size,name=source.name)
    return {'version':version,'signed_key_id':signed['key_id'],
            'verified_assets':len(artifacts)//2,'source_signature_verified':True}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir',type=Path,required=True)
    parser.add_argument('--version',required=True)
    parser.add_argument('--trust',type=Path,default=ROOT/'paper_radar/release_public_keys.json')
    args=parser.parse_args(argv)
    print(verify_stable(args.release_dir,args.version,args.trust))

if __name__=='__main__':main()
