# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Validate that installable GitHub Actions workflows really ship in archives.

GitHub reads workflows ONLY from the repository root .github/workflows/.
The updater source ZIP intentionally has a Paper-Radar/ top-level directory;
the Git-ready ZIP must not. Validate both layouts explicitly.
"""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    '.github/workflows/ci.yml',
    '.github/workflows/desktop-release.yml',
    '.github/workflows/reliability.yml',
)
REQUIRED_RELEASE_MARKERS = (
    'workflow_dispatch:', 'push:', 'build:', 'publish:',
    'windows-latest', 'ubuntu-22.04', 'macos-15', 'macos-15-intel',
    'contents: write', 'release_assets.py verify', 'gh release create',
)


def validate_repo(root: Path = ROOT) -> None:
    root = Path(root)
    for rel in WORKFLOWS:
        file = root / rel
        if not file.is_file() or not file.read_text(encoding='utf-8').strip():
            raise ValueError(f'Missing repository-root GitHub Actions workflow: {rel}')
    text = (root / WORKFLOWS[1]).read_text(encoding='utf-8')
    for marker in REQUIRED_RELEASE_MARKERS:
        if marker not in text:
            raise ValueError(f'Desktop release workflow missing required step/runner: {marker}')
    if not (root / 'tools/packaging/build_desktop.py').is_file():
        raise ValueError('Missing packaging entrypoint referenced by workflow')
    if not (root / 'tools/packaging/release_assets.py').is_file():
        raise ValueError('Missing release verification referenced by workflow')
    if not (root / 'tools/packaging/feature_parity.py').is_file():
        raise ValueError('Missing feature parity verification referenced by workflow')


def validate_archive(archive: Path, *, prefix: str, root: Path = ROOT) -> None:
    """Compare archive entries with on-disk workflows and reject nesting errors.

    prefix='Paper-Radar/' for the updater ZIP, prefix='' for a Git-ready ZIP.
    """
    if prefix not in ('', 'Paper-Radar/'):
        raise ValueError('Unsupported archive layout')
    validate_repo(root)
    with ZipFile(archive) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate ZIP paths')
        for rel in WORKFLOWS:
            member = prefix + rel
            if member not in names:
                raise ValueError(f'GitHub workflow is missing or incorrectly nested: {member}')
            if z.read(member) != (Path(root) / rel).read_bytes():
                raise ValueError(f'Workflow content differs from checked source: {member}')
            # Avoid a second nested copy that the GitHub repository will ignore.
            other = ('Paper-Radar/' if not prefix else '') + rel
            if other in names:
                raise ValueError(f'Unexpected alternate workflow path: {other}')
        for name in names:
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name:
                raise ValueError(f'Unsafe ZIP path: {name}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--layout', choices=('source', 'git-root'), default='source')
    args = parser.parse_args()
    validate_repo(args.root)
    if args.archive:
        validate_archive(args.archive, prefix='Paper-Radar/' if args.layout == 'source' else '', root=args.root)
    print('GitHub Actions workflows: valid and correctly located')


if __name__ == '__main__':
    main()
