# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Explicit, hash-guarded cleanup of obsolete *root* scripts after upgrading.

Only the exact shipped v1.45.4 script bytes may be deleted. A user-edited
script is never removed; no scanning outside the known repository root.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
KNOWN_SHA256={'CHECK_RETRIEVAL_WINDOWS.bat': 'ba92b8e4a009c7c921f36e8b4ad4a6dab62fb9bc788b24fe1c5503202e008d1c', 'RUN_REALPDF30_EVAL_MAC_LINUX.sh': 'ae9b84af88f6e3b59b103609fcafaf3783f16a6f9ca467f8cbb15e0960e3202a', 'RUN_REALPDF30_EVAL_WINDOWS.bat': '298c7099c71f044cc875b057ad5729b9d2802e5b46927676529d80c50c570967', 'RUN_REALPDF30_V1452_MAC_LINUX.sh': 'ca5c8b8d8f0e5184e00cad55acd7a2ec330b9975c8648a8bed2612eff47b5f9b', 'RUN_REALPDF30_V1452_WINDOWS.bat': '6bf6ae9434463c72af01924c3b3f0157df4f591fbb6014c8618ea40d10a55e8d', 'RUN_REALPDF30_V1453_MAC_LINUX.sh': 'd1d1fcd67c58d02772b2921ee27aa2c33c7f11f09ffdf9f0c8aea73522371cbe', 'RUN_REALPDF30_V1453_WINDOWS.bat': 'c78e4797832f313e875fca1041837e07cabc9a50ce45e1b39cc74fe6683f0b1e', 'RUN_SCIENCE_AUDIT_V1454_MAC_LINUX.sh': 'eae990c76bdeb9ba523468bd1dfcf24363cec0a6c9afa94c4d50599694159979', 'RUN_SCIENCE_AUDIT_V1454_WINDOWS.bat': 'ee7bb0d081629e6794957c2786767584cbdc57123fdc7fac99f29eb487859c82', 'start_public_macos_linux.sh': 'a20a29bd2fd0e18cd5116ca504ad313e2dd5ade29d1ae8193377d43875f5d236', 'start_public_windows.bat': '74396abb14c1124245c08480ec7fc6d90380237ded4473952a0df0f4cafca77b', 'start_public_worker_macos_linux.sh': 'd86caf2dd2dbc7eaaedb545bbf2a8018d53a1b6436f2d8941a1568060ec89eb1', 'start_public_worker_windows.bat': '51cf301276dd77b720a48cd2f460c6b9bf474e9b81f2948693f41c8420e89429'}


def cleanup(root:Path=ROOT,*,apply=False):
    root=Path(root).resolve()
    results={'deleted':[],'would_delete':[],'modified_preserved':[],'absent':[]}
    for name,expected in KNOWN_SHA256.items():
        path=root/name
        if not path.exists():
            results['absent'].append(name)
            continue
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            results['modified_preserved'].append(name)
            continue
        if apply:
            path.unlink()
            results['deleted'].append(name)
        else:
            results['would_delete'].append(name)
    return results


def main(argv=None):
    parser=argparse.ArgumentParser(description='Remove exact known obsolete root launchers; preserve edited files')
    parser.add_argument('--apply',action='store_true',help='Perform deletion; default dry run')
    args=parser.parse_args(argv)
    import json
    print(json.dumps(cleanup(apply=args.apply),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
