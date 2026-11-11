# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Create a GitHub repository-root ZIP, including hidden .github workflows.

This archive is for syncing source files into an existing Git checkout. It is
NOT a substitute for the updater-compatible Paper-Radar_vX_Source.zip.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.package_release import include  # noqa: E402
from tools.packaging.check_github_workflows import validate_repo, validate_archive  # noqa: E402


def build(output: Path, root: Path = ROOT) -> int:
    root, output = Path(root).resolve(), Path(output).resolve()
    validate_repo(root)
    from tools.packaging.feature_parity import validate_tree
    validate_tree(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(
        f for f in root.rglob('*') if f.is_file() and not f.is_symlink()
        and include(f.relative_to(root)) and f.resolve() != output
    )
    with ZipFile(output, 'w', compression=ZIP_DEFLATED, compresslevel=8) as z:
        for f in files:
            rel = f.relative_to(root).as_posix()  # NO Paper-Radar/ directory
            info = ZipInfo(rel, date_time=(2026, 11, 6, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = ((0o100755 if f.name.endswith('.sh') else 0o100644) << 16)
            z.writestr(info, f.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=8)
        assert z.testzip() is None
    validate_archive(output, prefix='', root=root)
    from tools.packaging.feature_parity import validate_zip
    validate_zip(output, '')
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + '.sha256').write_text(f'{digest}  {output.name}\n', encoding='ascii')
    print(f'Git-ready ZIP: {output} ({len(files)} files; SHA-256 {digest})')
    return len(files)


def main():
    from paper_radar import __version__
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=ROOT.parent / f'Paper-Radar_v{__version__}_GitHub_Repository_Root.zip')
    args = parser.parse_args()
    build(args.out)


if __name__ == '__main__':
    main()
