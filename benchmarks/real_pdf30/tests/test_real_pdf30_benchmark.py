"""Offline referential checks for the real-PDF-30 benchmark metadata.

PDF assets are intentionally not committed to the public repository.
"""
import csv,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def _csv(path):
 with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def test_manifest_canonical_fixed_versions():
 m=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
 assert m['paper_count']==30 and len(m['papers'])==30
 ids=[p['paper_id'] for p in m['papers']]
 assert len(ids)==len(set(ids))
 assert all(p['pdf_url'].startswith('https://arxiv.org/pdf/') and
            p['version'].startswith('v') and len(p['sha256'])==64 and
            p['pages']>=4 for p in m['papers'])

def test_evidence_and_questions_have_verified_reference_ids():
 m=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
 papers={p['paper_id']:p for p in m['papers']}
 evidence=_csv(ROOT/'annotations/candidate_evidence.csv')
 ids={r['candidate_id'] for r in evidence}
 assert len(ids)==len(evidence)==117
 for e in evidence:
  assert e['paper_id'] in papers
  assert e['pdf_sha256']==papers[e['paper_id']]['sha256']
  assert 1<=int(e['page'])<=papers[e['paper_id']]['pages']
 questions=[json.loads(x) for x in (ROOT/'tasks/single_and_cross_paper_qa.jsonl').read_text(encoding='utf-8').splitlines()]
 assert len(questions)==139
 assert all(q['anchor_a'] in ids and (not q['anchor_b'] or q['anchor_b'] in ids) for q in questions)

def test_paper_holdout_splits_and_cross_paper_no_leakage():
 splits={r['paper_id']:r['split'] for r in _csv(ROOT/'tasks/paper_split.csv')}
 assert sorted(splits.values()).count('train')==16
 assert sorted(splits.values()).count('validation')==7
 assert sorted(splits.values()).count('test')==7
 pairs=_csv(ROOT/'tasks/method_and_conflict_pairs.csv')
 assert len(pairs)==34
 assert all(splits[p['paper_a']]==splits[p['paper_b']]==p['split'] for p in pairs)

def test_no_unreviewed_evidence_claimed_as_gold():
 quality=json.loads((ROOT/'quality.json').read_text(encoding='utf-8'))
 assert quality['human_gold_items']==0 and quality['measured_f1'] is None
 for name in ('review_A.csv','review_B.csv'):
  assert all(not r['decision'] for r in _csv(ROOT/'annotations'/name))
 for name in ('qa_review_A.csv','qa_review_B.csv'):
  assert all(not r['answer_gold'] for r in _csv(ROOT/'tasks'/name))
