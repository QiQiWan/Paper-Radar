# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Prevent accidentally publishing an incomplete *full* Paper-Radar repository.

The v1.34.0 full repository is the minimum feature inventory. All Python
packages, frontend resources, public launchers, release/deployment tools,
CI tests and benchmark fixtures must remain present in the full distribution.
Changing implementation is allowed; removing an entry requires an explicit
inventory review. Historical docs and obsolete repair BATs are not required.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = Path(__file__).with_name('required_feature_inventory.json')
REQUIRED_SUPPORT = (
    'README.md', 'docs/USER_GUIDE.md', 'docs/MAINTAINERS.md',
    '.github/workflows/ci.yml', '.github/workflows/desktop-release.yml',
    '.github/workflows/reliability.yml',
    'tools/packaging/feature_parity.py',
    'paper_radar/release_public_keys.json',
)
DEPRECATED_ROOT = (
    'REPAIR_WINDOWS_SETTINGS.bat', 'REPAIR_WINDOWS_STORAGE.bat',
)


def required_paths() -> set[str]:
    data = json.loads(INVENTORY.read_text(encoding='utf-8'))
    paths = data.get('required_paths')
    if not isinstance(paths, list) or not paths or len(paths) != len(set(paths)):
        raise ValueError('Feature inventory missing or contains duplicate paths')
    return set(paths) | set(REQUIRED_SUPPORT)


def validate_tree(root: Path = ROOT) -> dict:
    root = Path(root)
    missing = sorted(path for path in required_paths() if not (root / path).is_file())
    stale = sorted(path for path in DEPRECATED_ROOT if (root / path).exists())
    if missing or stale:
        raise ValueError(f'Incomplete Paper-Radar repository: missing={missing}; obsolete={stale}')
    # All illustrated manuals must resolve their local assets.
    from paper_radar.help_pages import GUIDES, GUIDE_ASSETS, render_guide
    manual = (root / 'docs' / 'USER_GUIDE.md').read_text(encoding='utf-8')
    for section in ('quickstart', 'usage', 'following', 'sources', 'model', 'zotero', 'reading', 'desktop', 'portal'):
        if f'<!-- GUIDE:{section} -->' not in manual:
            raise ValueError('Missing manual section: ' + section)
    inventory = json.loads(INVENTORY.read_text(encoding='utf-8'))
    for file, markers in inventory.get('api_markers', {}).items():
        content = (root / file).read_text(encoding='utf-8')
        missing_markers = sorted(marker for marker in markers if marker not in content)
        if missing_markers:
            raise ValueError(f'API endpoints removed from {file}: {missing_markers}')
    for file, identifiers in inventory.get('ui_ids', {}).items():
        content = (root / file).read_text(encoding='utf-8')
        missing_ids = sorted(ident for ident in identifiers if f'id="{ident}"' not in content)
        if missing_ids:
            raise ValueError(f'UI controls removed from {file}: {missing_ids}')
    for asset in GUIDE_ASSETS:
        if not (root / 'docs' / 'assets' / asset).is_file():
            raise ValueError('Missing manual illustration: ' + asset)
    for name in GUIDES:
        if render_guide(name) is None:
            raise ValueError('Broken contextual help route: ' + name)
    return {'checked': len(required_paths()), 'missing': 0, 'obsolete': 0,
            'manual_sections': 9, 'manual_assets': len(GUIDE_ASSETS),
            'api_markers':sum(map(len,inventory.get('api_markers',{}).values())),
            'ui_ids':sum(map(len,inventory.get('ui_ids',{}).values()))}


def validate_zip(path: Path, prefix: str) -> dict:
    if prefix not in ('', 'Paper-Radar/'):
        raise ValueError('Unexpected source archive layout')
    with ZipFile(path) as z:
        names = z.namelist()
        members = set(names)
        if len(names) != len(members) or z.testzip() is not None:
            raise ValueError('Invalid or damaged source archive')
        missing = sorted(prefix + p for p in required_paths() if prefix + p not in members)
        stale = sorted(prefix + p for p in DEPRECATED_ROOT if prefix + p in members)
        if missing or stale:
            raise ValueError(f'Incomplete release archive: missing={missing}; obsolete={stale}')
    return {'checked': len(required_paths()), 'missing': 0, 'obsolete': 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, default=ROOT)
    ap.add_argument('--archive', type=Path)
    ap.add_argument('--layout', choices=('git-root', 'source'), default='git-root')
    args = ap.parse_args()
    result = validate_tree(args.root)
    if args.archive:
        result['archive'] = validate_zip(args.archive,
                                         prefix='' if args.layout == 'git-root' else 'Paper-Radar/')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
