# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Safely bump the five current-version markers needed for a new Release."""
from __future__ import annotations
import argparse
from pathlib import Path
import re
from release_version import ROOT, SEMVER, read_version


def replace_once(path: Path, regex: str, replace: str) -> None:
    old = path.read_text(encoding="utf-8")
    new, count = re.subn(regex, lambda _match: replace, old)
    if count != 1:
        raise ValueError(f"Expected exactly one version marker in {path}; found {count}")
    path.write_text(new, encoding="utf-8")


def bump(version: str, root: Path = ROOT) -> None:
    if not SEMVER.fullmatch(version):
        raise ValueError("Expected numeric MAJOR.MINOR.PATCH")
    old = read_version(root)
    if tuple(map(int, version.split('.'))) <= tuple(map(int, old.split('.'))):
        raise ValueError(f"New version must be greater than {old}")
    changes = [
        ('paper_radar/__init__.py', r'(?m)^__version__\s*=\s*[\'\"]\d+\.\d+\.\d+[\'\"]', f'__version__ = "{version}"'),
        ('pyproject.toml', r'(?m)^version\s*=\s*[\'\"]\d+\.\d+\.\d+[\'\"]', f'version = "{version}"'),
        ('paper_radar/agents/catalog.py', r'(?m)^VERSION\s*=\s*[\'\"]\d+\.\d+\.\d+[\'\"]', f"VERSION = '{version}'"),
        ('paper_radar/static/index.html', r'Paper-Radar v\d+\.\d+\.\d+', f'Paper-Radar v{version}'),
        ('paper_radar/static/portal.html', r'<span id="portalVersion">v\d+\.\d+\.\d+</span>', f'<span id="portalVersion">v{version}</span>'),
    ]
    # Preflight all markers before modifying any file.
    for rel, regex, _ in changes:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        if len(re.findall(regex, text)) != 1:
            raise ValueError(f"Version marker missing or ambiguous: {rel}")
    for rel, regex, replacement in changes:
        replace_once(root / rel, regex, replacement)
    if read_version(root) != version:
        raise RuntimeError('Version bump postcondition failed')
    print(f"Updated {len(changes)} version markers: {old} -> {version}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('version')
    args = parser.parse_args()
    bump(args.version)


if __name__ == '__main__':
    main()
