# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Build an auditable source ZIP ready for GitHub Release auto-installation.

Output MUST be attached to a public, non-prerelease GitHub Release using the
exact archive name. GitHub calculates the Release asset SHA-256 digest used by
Paper-Radar's bootstrap updater. A local .sha256 file is also generated for
publisher-side comparison (not an alternative trust source).
"""
from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
import argparse
import hashlib
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EXCLUDED_ROOT = {'.git', '.venv', 'venv', 'env', 'data', 'downloads', 'reports', 'storage',
                 'logs', 'dist', 'build', '.paper_radar_updates', '.paper_radar_recovery'}
EXCLUDED_ANYWHERE = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.idea', '.vscode'}
EXCLUDED_NAMES = {'settings.json', '.env', '.DS_Store', 'Thumbs.db', '.coverage', '.paper_radar_home'}
EXCLUDED_SUFFIX = {'.pdf', '.sqlite', '.sqlite3', '.db', '.log', '.pyc', '.pyo', '.bak', '.zip', '.pem', '.key'}
ALLOWED_ROOT = {'paper_radar', 'tests', 'tools', 'docs', '.github', 'deploy', 'benchmarks',
                'start_public_windows.bat', 'start_public_macos_linux.sh',
                'start_public_worker_windows.bat', 'start_public_worker_macos_linux.sh',
                '.editorconfig', '.env.example', '.gitattributes', '.gitignore',
                'CHANGELOG.md', 'CONTRIBUTING.md', 'LICENSE', 'NOTICE.md',
                'README.md', 'SECURITY.md', 'main.py', 'startup.py',
                'start_windows.bat', 'start_macos_linux.sh', 'CHECK_RETRIEVAL_WINDOWS.bat', 'pyproject.toml',
                'requirements.txt'}


def include(rel: Path) -> bool:
    if not rel.parts or rel.parts[0] in EXCLUDED_ROOT or rel.parts[0] not in ALLOWED_ROOT:
        return False
    if any(part in EXCLUDED_ANYWHERE for part in rel.parts):
        return False
    if rel.name in EXCLUDED_NAMES or (rel.name.startswith('.env.') and rel.name != '.env.example'):
        return False
    if rel.suffix.lower() in EXCLUDED_SUFFIX:
        return False
    if rel.name.endswith(('-wal', '-shm')) or any(p.endswith('.egg-info') for p in rel.parts):
        return False
    return True


def build(output: Path, root: Path = ROOT) -> int:
    from paper_radar import __version__
    from paper_radar.startup_update import unpack_verified
    from tools.packaging.check_github_workflows import validate_repo, validate_archive
    output = output.resolve()
    root = Path(root).resolve()
    validate_repo(root)
    from tools.packaging.feature_parity import validate_tree
    validate_tree(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    expected_name = f'Paper-Radar_v{__version__}_Source.zip'
    if output.name != expected_name:
        raise ValueError(f'自动升级要求附件精确命名为：{expected_name}')
    files = sorted(f for f in root.rglob('*') if f.is_file() and not f.is_symlink()
                   and include(f.relative_to(root)) and f.resolve()!=output)
    with ZipFile(output,'w',compression=ZIP_DEFLATED,compresslevel=8) as archive:
        for item in files:
            rel = 'Paper-Radar/' + item.relative_to(root).as_posix()
            info = ZipInfo(rel, date_time=(2026, 11, 4, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = ((0o100755 if item.name.endswith('.sh') else 0o100644) << 16)
            archive.writestr(info,item.read_bytes(),compress_type=ZIP_DEFLATED,compresslevel=8)
        assert archive.testzip() is None
    validate_archive(output, prefix='Paper-Radar/', root=root)
    from tools.packaging.feature_parity import validate_zip
    validate_zip(output, 'Paper-Radar/')
    import tempfile
    with tempfile.TemporaryDirectory(prefix='paper-radar-release-check-') as td:
        unpack_verified(output,Path(td),__version__)
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(f'{digest}  {output.name}\n',encoding='ascii')
    print(f'Created {output} ({len(files)} files, SHA-256 {digest})')
    return len(files)


if __name__ == '__main__':
    from paper_radar import __version__
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT.parent / f'Paper-Radar_v{__version__}_Source.zip')
    args=parser.parse_args()
    build(args.out)
