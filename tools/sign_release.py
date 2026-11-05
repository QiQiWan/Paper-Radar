#!/usr/bin/env python3
"""Offline Ed25519 release signing. Keep private keys outside Git/source ZIPs."""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, PrivateFormat, NoEncryption, PublicFormat


def manifest_bytes(data: dict) -> bytes:
    return (json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8')


def sign(source: Path, version: str, private_key: Path, dest: Path):
    key=load_pem_private_key(private_key.read_bytes(),password=None)
    if not isinstance(key,Ed25519PrivateKey):
        raise ValueError('Requires an Ed25519 signing key')
    pub=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    kid='paper-radar-2026-'+hashlib.sha256(pub).hexdigest()[:12]
    source=source.resolve()
    if source.name != f'Paper-Radar_v{version}_Source.zip':
        raise ValueError('Unexpected source archive name')
    blob=source.read_bytes()
    data={'format':1,'repo':'QiQiWan/Paper-Radar','version':version,'tag':'v'+version,
          'asset':source.name,'sha256':hashlib.sha256(blob).hexdigest(),
          'size':len(blob),'startup_protocol':1,'algorithm':'Ed25519','key_id':kid}
    output=manifest_bytes(data)
    dest.mkdir(parents=True,exist_ok=True)
    manifest=dest/f'Paper-Radar_v{version}_release.json'
    signature=dest/f'Paper-Radar_v{version}_release.sig'
    manifest.write_bytes(output)
    signature.write_text(base64.b64encode(key.sign(output)).decode('ascii')+'\n',encoding='ascii')
    print('Signed',source.name,'with',kid)
    return manifest,signature


def main():
    parser=argparse.ArgumentParser(description='Sign a Paper-Radar release offline')
    sub=parser.add_subparsers(dest='cmd',required=True)
    new=sub.add_parser('keygen'); new.add_argument('--output',type=Path,required=True)
    make=sub.add_parser('sign'); make.add_argument('--source',type=Path,required=True)
    make.add_argument('--version',required=True); make.add_argument('--key',type=Path,required=True)
    make.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.cmd=='keygen':
        import os
        key=Ed25519PrivateKey.generate()
        path=args.output
        if path.exists():raise SystemExit('Refusing to overwrite a key')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(key.private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()))
        os.chmod(path,0o600)
        pub=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        print(json.dumps({'id':'paper-radar-2026-'+hashlib.sha256(pub).hexdigest()[:12],
            'algorithm':'Ed25519','public_key_base64':base64.b64encode(pub).decode('ascii')},indent=2))
    else:sign(args.source,args.version,args.key,args.output)

if __name__=='__main__':main()
