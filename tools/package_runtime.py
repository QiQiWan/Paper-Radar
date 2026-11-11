# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Make a minimal, source-based LOCAL runtime ZIP (no study data or dev tests).

The user still needs Python and dependencies. This is intentionally distinct
from the signed GitHub updater source archive and native frozen installers.
"""
from __future__ import annotations
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import argparse
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from paper_radar import __version__
from paper_radar.help_pages import GUIDES, GUIDE_ASSETS

ROOT_FILES={
 'main.py','startup.py','start_windows.bat','start_macos_linux.sh',
 'requirements.txt','README.md','LICENSE','NOTICE.md',
 'pyproject.toml','SECURITY.md',
}
DOCTORS=set()  # Maintenance tools remain in the full GitHub repository.
DOCS=({'docs/USER_GUIDE.md'} | {'docs/assets/'+name for name in GUIDE_ASSETS}
      | {path.relative_to(ROOT).as_posix() for path in (ROOT/'docs').glob('RETRIEVAL_*_V*.md')}
      | {'docs/RESEARCH_SYNTHESIS_V1.44.0.md','docs/RESEARCH_CONTINUITY_V1.45.0.md',
         'docs/RESEARCH_BRIEFS_V1.45.5.md','docs/ANNOTATION_WORKBENCH_V1.46.0.md'})
DEPLOY={'deploy/nginx-paper-radar.conf', 'deploy/paperradar-public.service',
        'deploy/paperradar-worker@.service'}
EXCLUDED_PARTS={'__pycache__','.pytest_cache','.mypy_cache','dist','build'}
PROHIBITED_SUFFIX={'.pyc','.pyo','.zip','.db','.sqlite','.sqlite3','.log','.pem','.key','.bak'}


def entries(root:Path=ROOT)->list[Path]:
    chosen=[]
    for path in root.rglob('*'):
        if not path.is_file() or path.is_symlink():continue
        rel=path.relative_to(root)
        as_posix=rel.as_posix()
        if any(piece in EXCLUDED_PARTS for piece in rel.parts):continue
        if rel.suffix.lower() in PROHIBITED_SUFFIX:continue
        if as_posix in ROOT_FILES|DOCTORS|DOCS|DEPLOY or (
          rel.parts[0]=='paper_radar' and
          (rel.suffix.lower() in {'.py','.html','.css','.js'} or
           as_posix=='paper_radar/release_public_keys.json') and
          not any(part.startswith('.') for part in rel.parts)) or (
          rel.parts[0]=='benchmarks' and
          rel.suffix.lower() in {'.py','.json','.csv','.jsonl','.md','.txt','.bat','.sh'} and
          not any(part.startswith('.') for part in rel.parts)):
            chosen.append(rel)
    missing=[name for name in ROOT_FILES|DOCTORS|DOCS|DEPLOY if not (root/name).is_file()]
    if missing:raise RuntimeError('运行包缺少文件：'+', '.join(sorted(missing)))
    return sorted(chosen)


def build_runtime(output:Path, root:Path=ROOT)->dict:
    root=Path(root).resolve()
    output=Path(output).resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    files=entries(root)
    with ZipFile(output,'w',compression=ZIP_DEFLATED,compresslevel=8) as zipfile:
        for rel in files:
            zipfile.write(root/rel,arcname='Paper-Radar/'+rel.as_posix())
    # An explicit readback catches incomplete/truncated archives.
    with ZipFile(output) as zipfile:
        assert zipfile.testzip() is None
        names=set(zipfile.namelist())
    for private in ('settings.json','data/papers.sqlite3','.paper_radar_home','downloads','reports'):
        assert not any(x.endswith('/'+private) for x in names),private
    return {'archive':str(output),'version':__version__,'files':len(files),
            'size_bytes':output.stat().st_size,'type':'local-python-source-runtime'}


def main(argv=None):
    import json
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT.parent/f'Paper-Radar_v{__version__}_Windows_Local_Runtime.zip')
    args=parser.parse_args(argv)
    print(json.dumps(build_runtime(args.out),ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
