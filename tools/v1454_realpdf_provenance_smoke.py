"""Opt-in end-to-end audit of a real long PDF without networking or model calls.

Usage:
  python -m tools.v1454_realpdf_provenance_smoke \\
      --pdf /path/to/frozen-version.pdf --expected-sha256 <sha> --page 167 \\
      --output audit.json

The PDF is copied to a temporary managed download directory; no arbitrary file
path enters Paper-Radar's HTTP interfaces. All temporary files are removed.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from time import monotonic


def run(pdf_path: Path, expected_sha256: str, *, page: int = 167) -> dict:
    from paper_radar.core import connect, save_papers
    from paper_radar.downloads.manager import download_root
    from paper_radar.evidence_chain import local_chain, MAX_EVIDENCE_PAGES
    from paper_radar.fulltext import index_paper
    from paper_radar.models import Paper

    if not 1 <= page <= MAX_EVIDENCE_PAGES:
        raise ValueError('Target page is outside supported evidence range')
    if not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError('Supply a real, independently recorded PDF SHA256')
    pdf_path = Path(pdf_path).resolve(strict=True)
    if not pdf_path.is_file():
        raise ValueError('Source must be a regular PDF')
    digest = sha256()
    with pdf_path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    source_sha = digest.hexdigest()
    if source_sha != expected_sha256:
        raise ValueError('Frozen PDF SHA256 mismatch')
    started = monotonic()
    with TemporaryDirectory(prefix='paper-radar-1454-') as temp:
        db_path = Path(temp) / 'papers.sqlite3'
        downloads = download_root(db_path)
        downloads.mkdir(parents=True, exist_ok=True)
        destination = downloads / 'frozen-original.pdf'
        shutil.copyfile(pdf_path, destination)
        pid = 'arxiv:research-audit'
        paper = Paper(paper_id=pid, title='Frozen PDF evidence integrity test',
                      abstract='Regression test only; does not assert scientific quality.',
                      authors='Audit', published='2026-11-10', updated='2026-11-10',
                      category='cs.LG',url='https://arxiv.org/', pdf_url='')
        with connect(db_path) as conn:
            save_papers(conn, [paper])
            conn.execute('UPDATE papers SET local_file=? WHERE paper_id=?',
                         (destination.name, pid))
            result=index_paper(conn, pid, db_path=db_path)
            source_rows=conn.execute('''SELECT id,content FROM fulltext_chunks
                WHERE paper_id=? AND page=? ORDER BY id''',(pid,page)).fetchall()
        if not source_rows:
            raise RuntimeError('Expected page is not present in the full-text index')
        anchor = None
        for row in source_rows[:10]:
            content=row['content']
            # Use text on the specified physical page, not a synthetic quote.
            for line in content.splitlines():
                quoted=' '.join(line.split())[:130]
                if len(quoted)<40 or not re.search('[a-zA-Z]{4}',quoted):
                    continue
                try:
                    anchor=local_chain('default','add_anchor',pid,page,quoted,db_path=db_path)
                    break
                except ValueError:
                    continue
            if anchor:break
        if not anchor:
            raise RuntimeError('Physical page exists but no unique verbatim anchor could be saved')
        summary={'result':'passed','source_sha256':source_sha,'page':page,
                 'indexed_pages':result.get('pages'), 'index_status':result.get('status'),
                 'anchor_page':anchor['page'], 'anchor_pdf_sha256':anchor['pdf_sha256'],
                 'quote':anchor['quote'], 'quote_start':anchor['quote_start'],
                 'quote_end':anchor['quote_end'],
                 'elapsed_seconds':round(monotonic()-started,2),
                 'model_calls':0, 'scientific_accuracy_assessed':False}
        return summary


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf',type=Path,required=True)
    parser.add_argument('--expected-sha256',required=True)
    parser.add_argument('--page',type=int,default=167)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    result=run(args.pdf,args.expected_sha256,page=args.page)
    output=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(output+'\n',encoding='utf-8')
    print(output)


if __name__=='__main__':
    main()
