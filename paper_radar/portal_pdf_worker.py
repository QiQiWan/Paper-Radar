"""PDF text parser run in a subprocess with bounded wall-clock and output size."""
from __future__ import annotations
import json,sys
from pathlib import Path

def parse(path):
    if sys.platform.startswith('linux'):
        import resource
        try:
            resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
            resource.setrlimit(resource.RLIMIT_CPU,(18,20))
        except (OSError,ValueError):pass
    from pypdf import PdfReader
    doc=PdfReader(str(path),strict=False)
    if doc.is_encrypted:raise ValueError('Encrypted PDF is not supported')
    if len(doc.pages)>40:raise ValueError('PDF page count exceeds 40')
    pages=[];total=0
    for num,p in enumerate(doc.pages,1):
        text=str(p.extract_text() or '')[:4800].replace('\x00','').strip()
        total+=len(text)
        if total>110000:raise ValueError('PDF extracted text exceeds limit')
        pages.append({'page':num,'text':text})
    return {'total_pages':len(doc.pages),'pages':pages}

if __name__=='__main__':
    try:print(json.dumps(parse(Path(sys.argv[1])),ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error':type(exc).__name__},ensure_ascii=False))
        sys.exit(2)
