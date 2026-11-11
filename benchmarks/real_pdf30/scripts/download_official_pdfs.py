#!/usr/bin/env python3
"""Download original official arXiv PDFs into a researcher's private directory.

Respects a 3.2 second interval and validates each file against the pinned hash.
Does not upload or republish the PDFs.
"""
import argparse,hashlib,json,time
from pathlib import Path
from urllib.request import Request,urlopen

def sha_file(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--dest',type=Path,default=Path('pdfs'))
    a=p.parse_args();data=json.loads(a.manifest.read_text(encoding='utf-8'));a.dest.mkdir(parents=True,exist_ok=True)
    failures=[]
    for item in data['papers']:
        target=a.dest/(item['paper_id']+item['version']+'.pdf')
        expected=item['sha256']
        if target.exists() and sha_file(target)==expected:
            print('CACHED',target.name);continue
        ok=False
        for trial in range(3):
            try:
                req=Request(item['pdf_url'],headers={'User-Agent':'PaperRadarEval/1.0 (single-connection private research)','Accept':'application/pdf'})
                with urlopen(req,timeout=45) as response:
                    blob=response.read(40*1024*1024)
                if not blob.startswith(b'%PDF') or hashlib.sha256(blob).hexdigest()!=expected:
                    raise ValueError('PDF version or checksum mismatch')
                part=target.with_suffix('.pdf.partial');part.write_bytes(blob);part.replace(target)
                print('OK',target.name,len(blob));ok=True;break
            except Exception as exc:
                print('RETRY',target.name,type(exc).__name__,str(exc)[:90]);time.sleep(4*(trial+1))
        if not ok:failures.append(target.name)
        time.sleep(3.2)
    if failures:
        print('FAILED',failures);raise SystemExit(1)
    print('Validated',len(data['papers']),'PDFs')
if __name__=='__main__':main()
