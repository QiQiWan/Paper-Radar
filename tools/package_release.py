# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Build a source ZIP without personal research files. Run from any directory."""
from __future__ import annotations
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import argparse

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_ROOT = {".git", ".venv", "venv", "env", "data", "downloads", "reports", "logs", "dist", "build"}
EXCLUDED_ANYWHERE = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode"}
EXCLUDED_NAMES = {"settings.json", ".env", ".DS_Store", "Thumbs.db", ".coverage"}
EXCLUDED_SUFFIX = {".pdf", ".sqlite", ".sqlite3", ".db", ".log", ".pyc", ".pyo", ".bak"}


def include(rel: Path) -> bool:
    if not rel.parts or rel.parts[0] in EXCLUDED_ROOT:
        return False
    if any(part in EXCLUDED_ANYWHERE for part in rel.parts):
        return False
    if rel.name in EXCLUDED_NAMES or rel.name.startswith(".env.") and rel.name != ".env.example":
        return False
    if rel.suffix.lower() in EXCLUDED_SUFFIX:
        return False
    if rel.name.endswith(('-wal', '-shm')) or rel.name.endswith('.egg-info'):
        return False
    if any(part.endswith('.egg-info') for part in rel.parts):
        return False
    return True


def build(output: Path, root: Path = ROOT) -> int:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(f for f in root.rglob('*') if f.is_file() and include(f.relative_to(root)) and f.resolve() != output)
    with ZipFile(output, 'w', compression=ZIP_DEFLATED, compresslevel=8) as archive:
        for f in files:
            archive.write(f, 'Paper-Radar/' + f.relative_to(root).as_posix())
        assert archive.testzip() is None
    print(f'Created {output} with {len(files)} files')
    return len(files)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT.parent/'Paper-Radar_v1.12.0_GitHub_Source.zip')
    opts=parser.parse_args()
    build(opts.out)
