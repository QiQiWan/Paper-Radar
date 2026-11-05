# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Verify detached Ed25519 release manifests against locally pinned keys.

SHA-256 from a GitHub response is only a transport-integrity checksum. The
signed manifest gives independent developer-controlled artifact provenance.
"""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path


class SignatureError(ValueError):
    pass


def verify_manifest(manifest: bytes, signature: bytes, trusted_key_file: Path, *,
                    version: str, sha256: str, archive_size: int, name: str) -> dict:
    if len(manifest)>12000 or len(signature)>1000:
        raise SignatureError('Signed release metadata too large')
    try:
        content=json.loads(manifest.decode('utf-8'))
        trust=json.loads(Path(trusted_key_file).read_text(encoding='utf-8'))
    except (ValueError,UnicodeError,OSError) as exc:
        raise SignatureError('Invalid signed metadata or local public key trust store') from exc
    if not isinstance(content,dict) or not isinstance(trust,dict):
        raise SignatureError('Invalid signed metadata structure')
    required={'format':1,'repo':'QiQiWan/Paper-Radar','version':version,
        'tag':'v'+version,'asset':name,'sha256':sha256,'size':archive_size,
        'startup_protocol':1,'algorithm':'Ed25519'}
    for key,expected in required.items():
        if type(content.get(key)) is not type(expected) or content[key]!=expected:
            raise SignatureError('Signed manifest does not match release: '+key)
    kid=str(content.get('key_id') or '')
    matches=[key for key in trust.get('keys',[]) if isinstance(key,dict) and key.get('id')==kid]
    if len(matches)!=1 or matches[0].get('algorithm')!='Ed25519':
        raise SignatureError('Untrusted release signing key')
    try:
        public=base64.b64decode(matches[0]['public_key_base64'],validate=True)
        signature_raw=base64.b64decode(signature.strip(),validate=True)
        if len(public)!=32 or len(signature_raw)!=64:
            raise ValueError('Invalid public key or signature bytes')
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(public).verify(signature_raw,manifest)
    except ImportError as exc:
        raise SignatureError('Missing cryptography dependency; manual upgrade required') from exc
    except Exception as exc:
        raise SignatureError('Release signature verification failed') from exc
    return content
