#!/usr/bin/env python3
"""Paper-level split and leakage-safe comparative QA generation."""
import csv,json
from collections import defaultdict
from itertools import combinations
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]

def write(path,fields,rows):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)

def main():
 meta=json.loads((BASE/'manifest.json').read_text(encoding='utf-8'))
 papers={p['paper_id']:p for p in meta['papers']}
 bycat=defaultdict(list)
 for p in meta['papers']:bycat[p['category']].append(p)
 assignments={}
 for category,members in sorted(bycat.items()):
  members.sort(key=lambda r:r['paper_id']);n=len(members)
  nvalid=2 if n>=6 else 1 if n>=3 else 0
  ntest=2 if n>=6 else 1 if n>=3 else 0
  for i,p in enumerate(members):
   assignments[p['paper_id']]='train' if i<n-nvalid-ntest else 'validation' if i<n-ntest else 'test'
 write(BASE/'tasks/paper_split.csv',['paper_id','category','split'],[
  {'paper_id':p['paper_id'],'category':p['category'],'split':assignments[p['paper_id']]} for p in meta['papers']])
 source=[]
 with (BASE/'annotations/candidate_evidence.csv').open(encoding='utf-8-sig',newline='') as f:
  source=list(csv.DictReader(f))
 first={}
 for row in source:first.setdefault(row['paper_id'],row['candidate_id'])
 # Within-domain pairs in the same fold; cross-domain pairs are deliberately
 # negative *candidates* (not human-judged), also from the same fold.
 pairs=[];keys=set()
 def add(a,b,kind):
  if a==b or (a,b) in keys or (b,a) in keys:return
  assert assignments[a]==assignments[b]
  keys.add((a,b));category=papers[a]['category'] if kind=='within_domain' else papers[a]['category']+' vs '+papers[b]['category']
  pairs.append({'pair_id':f'C-{len(pairs)+1:03d}', 'paper_a':a,'paper_b':b,
   'category':category,'pair_type':kind,'anchor_a':first[a],'anchor_b':first[b],
   'comparison_dimension':'methods_datasets_metrics_protocols',
   'same_dataset':'','same_metric':'','same_setting':'',
   'quantitatively_comparable':'','contradiction_label':'','judgement_reason':'',
   'review_status':'pending_independent_double_review','split':assignments[a]})
 for cat,members in sorted(bycat.items()):
  for split in ('train','validation','test'):
   subset=sorted((p['paper_id'] for p in members if assignments[p['paper_id']]==split))
   for a,b in list(combinations(subset,2))[:6]:add(a,b,'within_domain')
 for split in ('train','validation','test'):
  group=sorted([p for p in meta['papers'] if assignments[p['paper_id']]==split],key=lambda p:p['paper_id'])
  cross=[(a['paper_id'],b['paper_id']) for a,b in combinations(group,2)
         if a['category']!=b['category']]
  for a,b in cross[:(7 if split=='train' else 5)]:add(a,b,'cross_domain_control')
 single=[json.loads(line) for line in (BASE/'tasks/single_and_cross_paper_qa.jsonl').read_text(encoding='utf-8').splitlines()
         if line.strip() and json.loads(line)['task_type']=='single_paper_evidence']
 for q in single:q['split']=assignments[q['paper_a']]
 qa=single.copy()
 for pair in pairs:
  a,b=pair['paper_a'],pair['paper_b']
  ta,tb=papers[a]['title'],papers[b]['title']
  qa.append({'question_id':'M-'+pair['pair_id'], 'task_type':'cross_paper_method_comparison',
   'question':f"Compare experimental protocols and methodology in '{ta}' and '{tb}'. Are the results directly comparable? Cite the necessary experimental conditions.",
   'paper_a':a,'paper_b':b,'anchor_a':pair['anchor_a'],'anchor_b':pair['anchor_b'],
   'answer_candidate':'Comparison candidate only; actual conditions and results must be checked.',
   'answer_gold':'','citation_gold':'','answerable':'','difficulty':'hard',
   'review_status':'pending_independent_double_review','split':pair['split']})
 for pair in [p for p in pairs if p['pair_type']=='within_domain'][:20]:
  a,b=pair['paper_a'],pair['paper_b']
  qa.append({'question_id':'X-'+pair['pair_id'], 'task_type':'potential_conflict_detection',
   'question':f"Are claims in '{papers[a]['title']}' and '{papers[b]['title']}' genuinely contradictory after matching dataset, metric and conditions? Support the judgement with both PDFs, or state insufficient evidence.",
   'paper_a':a,'paper_b':b,'anchor_a':pair['anchor_a'],'anchor_b':pair['anchor_b'],
   'answer_candidate':'No conflict established without a matched protocol and two supported claims.',
   'answer_gold':'','citation_gold':'','answerable':'','difficulty':'hard',
   'review_status':'pending_independent_double_review','split':pair['split']})
 write(BASE/'tasks/method_and_conflict_pairs.csv',list(pairs[0]),pairs)
 (BASE/'tasks/single_and_cross_paper_qa.jsonl').write_text(''.join(json.dumps(q,ensure_ascii=False)+'\n' for q in qa),encoding='utf-8')
 write(BASE/'tasks/research_qa_review.csv',list(qa[0]),qa)
 for reviewer in ('A','B'):
  rows=[{'question_id':q['question_id'],'task_type':q['task_type'], 'paper_a':q['paper_a'],
   'paper_b':q['paper_b'],'split':q['split'],'reviewer':reviewer,'answer_gold':'',
   'citation_gold':'','answerable':'','rubric_score':'', 'judgement_reason':'','review_minutes':''} for q in qa]
  write(BASE/f'tasks/qa_review_{reviewer}.csv',list(rows[0]),rows)
 write(BASE/'tasks/qa_adjudicated.csv',list(rows[0]),[])
 for reviewer in ('A','B'):
  rows=[{'pair_id':p['pair_id'],'paper_a':p['paper_a'],'paper_b':p['paper_b'],
   'split':p['split'],'reviewer':reviewer,'same_dataset':'', 'same_metric':'',
   'same_setting':'','quantitatively_comparable':'','contradiction_label':'',
   'judgement_reason':'','review_minutes':''} for p in pairs]
  write(BASE/f'tasks/pair_review_{reviewer}.csv',list(rows[0]),rows)
 write(BASE/'tasks/pair_adjudicated.csv',list(rows[0]),[])
 meta.update(qa_task_count=len(qa),cross_paper_pair_count=len(pairs),
             paper_level_split={split:sum(v==split for v in assignments.values())
                                for split in ('train','validation','test')})
 (BASE/'manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
 quality_path=BASE/'quality.json'
 if quality_path.is_file():
  quality=json.loads(quality_path.read_text(encoding='utf-8'))
  quality.update(question_candidates=len(qa),cross_paper_candidates=len(pairs),
                 qa_task_types={typ:sum(q['task_type']==typ for q in qa)
                                for typ in set(q['task_type'] for q in qa)},
                 pair_types={typ:sum(row['pair_type']==typ for row in pairs)
                             for typ in set(row['pair_type'] for row in pairs)},
                 leakage_safe_paper_split=meta['paper_level_split'])
  quality_path.write_text(json.dumps(quality,ensure_ascii=False,indent=2),encoding='utf-8')
 print('Paper splits:',{x:sum(v==x for v in assignments.values()) for x in ('train','validation','test')},
       'Questions:',len(qa),'Pairs:',len(pairs),'cross_fold_pairs: 0',
       'task_types:',{typ:sum(q['task_type']==typ for q in qa) for typ in set(q['task_type'] for q in qa)})
if __name__=='__main__':main()
