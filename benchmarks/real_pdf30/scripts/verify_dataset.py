#!/usr/bin/env python3
"""Integrity and referential checks; optionally validate 30 on-disk PDFs."""
import argparse,csv,hashlib,json
from collections import Counter
from pathlib import Path
import fitz

def main():
 p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,default=Path('.'));p.add_argument('--with-pdfs',action='store_true')
 a=p.parse_args();root=a.dataset
 manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'));papers=manifest['papers']
 assert len(papers)==30 and len(set(p['paper_id'] for p in papers))==30
 hashes={p['paper_id']:p['sha256'] for p in papers}
 with (root/'annotations/candidate_evidence.csv').open(encoding='utf-8-sig',newline='') as f:
  evidence=list(csv.DictReader(f))
 byid={r['candidate_id']:r for r in evidence}
 assert len(evidence)>=60 and len(byid)==len(evidence)
 for c in evidence:
  assert c['paper_id'] in hashes and c['pdf_sha256']==hashes[c['paper_id']]
  assert 1<=int(c['page'])<=next(p['pages'] for p in papers if p['paper_id']==c['paper_id'])
 qa=[json.loads(line) for line in (root/'tasks/single_and_cross_paper_qa.jsonl').read_text(encoding='utf-8').splitlines() if line]
 assert len(qa)>=100 and len({q['question_id'] for q in qa})==len(qa)
 with (root/'tasks/paper_split.csv').open(encoding='utf-8-sig',newline='') as f:
  splits={r['paper_id']:r['split'] for r in csv.DictReader(f)}
 assert set(splits)==set(hashes)
 for q in qa:
  assert q['anchor_a'] in byid
  assert q['paper_a'] in hashes
  if q['paper_b']:
   assert q['anchor_b'] in byid and splits[q['paper_a']]==splits[q['paper_b']]==q['split']
  else:assert splits[q['paper_a']]==q['split']
 pdf_count=0
 if a.with_pdfs:
  for paper in papers:
   path=root/'pdfs'/(paper['paper_id']+paper['version']+'.pdf')
   assert path.exists(),path
   sha=hashlib.sha256(path.read_bytes()).hexdigest()
   assert sha==paper['sha256']
   with fitz.open(path) as pdf:
    assert pdf.page_count==paper['pages']
    # Anchors remain exact text spans after version pinning.
    for row in evidence:
     if row['paper_id']==paper['paper_id']:
      assert row['source_quote'] in pdf[int(row['page'])-1].get_text('text',sort=True)
   pdf_count+=1
 print(json.dumps({'state':'passed','papers':len(papers),'pdfs_verified':pdf_count,
       'evidence_candidates':len(evidence),'question_tasks':len(qa),'split':dict(Counter(splits.values()))},ensure_ascii=False))
if __name__=='__main__':main()
