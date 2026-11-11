#!/usr/bin/env python3
"""Reproducible real-PDF evidence preannotation; no fabricated ground truth.

Output candidate evidence, four scientific evaluation tasks, independent review
sheets, and a machine-checkable manifest. Every quote is an exact substring of a
specific SHA-256-pinned, rendered PDF page. 'gold' remains pending double review.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
import fitz

METRIC = re.compile(r"\b(?:relative\s+(?:L[12]|error)|L[12]\s+error|RMSE|MSE|MAE|MAPE|accuracy|F[\s-]?1(?:\s+score)?|precision|recall|error\s+rate|speedup|latency|runtime|CRPS|ROC-AUC|AUC|PSNR|bias|RMSE|coefficient\s+of\s+determination)\b", re.I)
NUMBER = re.compile(r"(?<![A-Za-z0-9])(?:[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\s*%?|\d+\.\d+\s*[×x])")
EXPERIMENT = re.compile(r"\b(?:experiment(?:s|al)?|results?|evaluation|benchmark(?:s|ing)?|empirical|simulation(?:s)?|numerical\s+(?:results?|experiments?|stud(?:y|ies))|ablation|performance|case\s+stud(?:y|ies))\b",re.I)
TABLE = re.compile(r"\b(?:table|tab\.?|figure|fig\.?)\s*[A-Z]?\d+\b",re.I)
NEGATION = re.compile(r"\b(?:fails?|outperform(?:s|ed)?|underperform(?:s|ed)?|inferior|worse|better|however|in contrast|inconsistent|degrad(?:es|ed|ation))\b",re.I)
DATASET = re.compile(r"\b(?:Darcy|Navier[- ]Stokes|Burgers|ERA5|PDEBench|Kolmogorov|WaveBench|WeatherBench|CFD|Reynolds|shallow.water|turbulence|FourCastNet|GraphCast)\b",re.I)
HEADER = ['candidate_id','paper_id','version','pdf_sha256','page','source_quote','metric_candidate','value_candidate','dataset_candidate','method_candidate','baseline_candidate','setting_candidate','source_type','status']
LABELS = ['candidate_id','paper_id','pdf_sha256','page','metric_correct','value_correct','relation_correct','method_correct','dataset_correct','verified_quote','verified_page','decision','correct_method','correct_dataset','correct_metric','correct_value','correct_baseline','correct_setting','correct_quote','reviewer','review_minutes','notes']
QUESTIONS = ['question_id','task_type','question','paper_a','paper_b','anchor_a','anchor_b','answer_candidate','answer_gold','citation_gold','answerable','difficulty','review_status','reviewer_a','reviewer_b','adjudicated_by']
PAIR = ['pair_id','paper_a','paper_b','category','anchor_a','anchor_b','comparison_dimension','same_dataset','same_metric','same_setting','quantitatively_comparable','contradiction_label','judgement_reason','review_status']


def norm(s): return ' '.join(str(s or '').split())
def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(2**20),b''):h.update(chunk)
    return h.hexdigest()
def write_csv(path,fields,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
def write_jsonl(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8') as f:
        for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')


def quote_candidates(pdf,metadata,limit=4):
    pid=metadata['paper_id'];version=metadata['version'];sha=metadata['sha256']
    pages=[];raw_text={}
    for ix,page in enumerate(pdf):
        text=page.get_text('text',sort=True)
        raw_text[ix+1]=text
        if not text.strip():continue
        # Avoid abstract-only candidates and reference bibliographies.
        if ix==0 and pdf.page_count>4:continue
        ref_start=re.search(r'(?im)^\s*(?:references|bibliography)\s*$',text)
        lead=text[:260].casefold()
        ref_penalty=25 if 'references\n' in lead or 'bibliography\n' in lead else 0
        score=(len(EXPERIMENT.findall(text))*3+len(METRIC.findall(text))*2+
               len(TABLE.findall(text))*3-min(25,ref_penalty))
        if re.search(r'(?im)^\s*(?:\d+(?:\.\d+)*\s*)?(?:results?|experiments?|evaluation|performance|benchmark(?:s|ing)?)\s*$',text):score+=12
        if score>2:pages.append((score,ix+1,text))
    pages.sort(key=lambda x:(-x[0],x[1]))
    out=[];used_pages=set()
    for score,page,text in pages:
        if len(out)>=limit:break
        # Ensure page diversity when possible.
        if page in used_pages and len(used_pages)<limit:continue
        matches=list(METRIC.finditer(text))
        if not matches:
            matches=list(EXPERIMENT.finditer(text))
        candidate=None
        for m in matches:
            start=max(0,m.start()-45)
            end=min(len(text),m.end()+115)
            window=text[start:end]
            number=NUMBER.search(text[m.end():min(len(text),m.end()+100)])
            if m.re is METRIC and not number:continue
            metric=m.group(0) if m.re is METRIC else ''
            value=number.group(0).strip() if number else ''
            dataset=DATASET.search(window)
            quote=window.strip()[:160]
            if len(quote)<25:continue
            candidate={'candidate_id':f'P{pid.replace(".","")}-{len(out)+1}',
                       'paper_id':pid,'version':version,'pdf_sha256':sha,'page':page,
                       'source_quote':quote,'metric_candidate':metric,'value_candidate':value,
                       'dataset_candidate':dataset.group(0) if dataset else '',
                       'method_candidate':'','baseline_candidate':'','setting_candidate':'',
                       'source_type':'pdf_text_candidate','status':'machine_suggested_unreviewed',
                       'page_score':score}
            break
        if candidate:
            out.append(candidate);used_pages.add(page)
    # For papers without metric+value nearby, retain a section anchor without an invented metric.
    if not out and pages:
        sc,p,t=pages[0]
        q=norm(t[:155])
        out.append({'candidate_id':f'P{pid.replace(".","")}-1','paper_id':pid,'version':version,
                    'pdf_sha256':sha,'page':p,'source_quote':q,'metric_candidate':'',
                    'value_candidate':'','dataset_candidate':'','method_candidate':'',
                    'baseline_candidate':'','setting_candidate':'','source_type':'section_text_only',
                    'status':'machine_suggested_unreviewed','page_score':sc})
    return out,raw_text,len(pages)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--download-manifest',required=True,type=Path)
    ap.add_argument('--pdf-dir',required=True,type=Path)
    ap.add_argument('--out',required=True,type=Path)
    ap.add_argument('--count',type=int,default=30)
    args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    source=json.loads(args.download_manifest.read_text(encoding='utf-8'))
    records=[];quality=[];catalog=[];qrows=[];pdf_page_map={}
    eligible=[r for r in source['items'] if r.get('status')=='downloaded' and r.get('experiment_signal')]
    # Use selection as declared in acquisition manifest, then remaining eligible.
    picked=sorted(eligible,key=lambda r:(not r.get('selected'),r['category'] not in ('operator','pinn','weather','benchmark'),-r.get('experiment_mentions',0)))[:args.count]
    if len(picked)<args.count:
        raise RuntimeError(f'Only {len(picked)} eligible real PDFs were obtained; require {args.count}')
    for i,r in enumerate(picked,1):
        pid=r['paper_id'];version=r['version']
        pdf_path=args.pdf_dir/(pid+version+'.pdf')
        if not pdf_path.is_file():raise FileNotFoundError(pdf_path)
        sha=digest(pdf_path)
        if sha!=r['sha256']:raise ValueError(f'PDF SHA mismatch: {pid}')
        with fitz.open(pdf_path) as doc:
            if doc.page_count<4:raise ValueError(f'Too few pages: {pid}')
            candidates,page_map,section_pages=quote_candidates(doc,r)
            if not candidates:raise ValueError(f'No evidence candidate pages: {pid}')
            for c in candidates:
                assert c['source_quote'] in page_map[c['page']]
            pdf_page_map[pid]=page_map
            records.extend(candidates)
            catalog.append({'paper_id':pid,'version':version,'title':r['title'],'category':r['category'],
                            'arxiv_url':f'https://arxiv.org/abs/{pid}',
                            'pdf_url':r['pdf_url'],'sha256':sha,'pdf_file':f'pdfs/{pdf_path.name}',
                            'pdf_bytes':pdf_path.stat().st_size,'pages':doc.page_count,
                            'machine_readable_chars':sum(len(x) for x in page_map.values()),
                            'candidate_experiment_pages':section_pages,'candidate_evidence':len(candidates),
                            'human_review_state':'not_reviewed',
                            'licensing':'check_source_version_before_redistribution'})
            quality.append({'paper_id':pid,'pdf_sha256':sha,'pages':doc.page_count,
                            'evidence_candidates':len(candidates),'candidate_pages':section_pages,
                            'pdf_valid':True,'needs_table_layout_review':True})
    idx={p['paper_id']:p for p in catalog}
    citations={}
    for c in records:citations.setdefault(c['paper_id'],[]).append(c)
    # Single-paper QA: two or more bounded anchor questions per paper.
    for p in catalog:
        subset=citations[p['paper_id']]
        for k,c in enumerate(subset[:3],1):
            if c['metric_candidate'] and c['value_candidate']:
                question=(f"What metric and reported numeric expression appear near page {c['page']} "
                          f"of '{p['title']}'? What extra conditions are required to interpret it?")
                candidate=f"Metric: {c['metric_candidate']}; nearby number: {c['value_candidate']}; relationship unverified."
            else:
                question=f"What experimental design or result is discussed near page {c['page']} of '{p['title']}'?"
                candidate='Check the cited PDF passage and surrounding table or section.'
            qrows.append({'question_id':f'S-{p["paper_id"].replace(".","")}-{k}',
                'task_type':'single_paper_evidence','question':question,'paper_a':p['paper_id'],
                'paper_b':'','anchor_a':c['candidate_id'],'anchor_b':'',
                'answer_candidate':candidate,'answer_gold':'','citation_gold':'','answerable':'',
                'difficulty':'medium','review_status':'pending_independent_double_review'})
    # Matched-category method comparisons. Cross-topic comparisons are excluded.
    pairs=[]
    groups=defaultdict(list)
    for p in catalog:groups[p['category']].append(p['paper_id'])
    for category,members in sorted(groups.items()):
        for a,b in list(combinations(members,2))[:10]:
            ca=citations[a][0];cb=citations[b][0]
            pairs.append({'pair_id':f'C-{len(pairs)+1:03d}', 'paper_a':a,'paper_b':b,'category':category,
                          'anchor_a':ca['candidate_id'],'anchor_b':cb['candidate_id'],
                          'comparison_dimension':'method_and_experimental_design',
                          'same_dataset':'','same_metric':'','same_setting':'',
                          'quantitatively_comparable':'','contradiction_label':'',
                          'judgement_reason':'','review_status':'pending_independent_double_review'})
    # QA for method comparisons and possible contradictions; gold stays blank.
    for pair in pairs:
        a=idx[pair['paper_a']];b=idx[pair['paper_b']]
        qrows.append({'question_id':'M-'+pair['pair_id'],
            'task_type':'cross_paper_method_comparison',
            'question':f"Compare the experimental protocol and methodological contribution of '{a['title']}' and '{b['title']}'. State whether results can be compared numerically, with evidence for any shared setting.",
            'paper_a':a['paper_id'],'paper_b':b['paper_id'],
            'anchor_a':pair['anchor_a'],'anchor_b':pair['anchor_b'],
            'answer_candidate':'Candidate pair only; experimental equivalence is unverified.',
            'answer_gold':'','citation_gold':'','answerable':'','difficulty':'hard',
            'review_status':'pending_independent_double_review'})
    for pair in pairs[:min(20,len(pairs))]:
        a=idx[pair['paper_a']];b=idx[pair['paper_b']]
        qrows.append({'question_id':'X-'+pair['pair_id'],
            'task_type':'potential_conflict_detection',
            'question':f"Do '{a['title']}' and '{b['title']}' make genuinely conflicting claims after accounting for datasets, evaluation conditions and metrics? Give exact evidence or mark 'no demonstrated conflict'.",
            'paper_a':a['paper_id'],'paper_b':b['paper_id'],
            'anchor_a':pair['anchor_a'],'anchor_b':pair['anchor_b'],
            'answer_candidate':'No conflict is established by this candidate pair.',
            'answer_gold':'','citation_gold':'','answerable':'','difficulty':'hard',
            'review_status':'pending_independent_double_review'})
    # Independent review sheets contain only blank expert labels and PDF pointers.
    reviewer_rows=[]
    for c in records:
        row={key:'' for key in LABELS}
        for k in ('candidate_id','paper_id','pdf_sha256','page'):
            row[k]=c[k]
        reviewer_rows.append(row)
    for reviewer in ('A','B'):
        rows=[{**r,'reviewer':reviewer} for r in reviewer_rows]
        write_csv(args.out/f'annotations/review_{reviewer}.csv',LABELS,rows)
    write_csv(args.out/'annotations/adjudicated_gold.csv',LABELS,[])
    write_csv(args.out/'annotations/candidate_evidence.csv',HEADER,records)
    write_jsonl(args.out/'tasks/single_and_cross_paper_qa.jsonl',qrows)
    write_csv(args.out/'tasks/method_and_conflict_pairs.csv',PAIR,pairs)
    write_csv(args.out/'tasks/research_qa_review.csv',QUESTIONS,qrows)
    (args.out/'manifest.json').write_text(json.dumps({'benchmark_id':'paper_radar_real_pdf_30_v1',
        'created_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'source':'arxiv original preprints','papers':catalog,'paper_count':len(catalog),
        'evidence_candidate_count':len(records),'qa_task_count':len(qrows),
        'cross_paper_pair_count':len(pairs),
        'gold_annotation_status':'unmeasured_pending_two_independent_reviewers',
        'license_note':'Original PDFs supplied for private research; check arXiv per-version license before redistribution.'},ensure_ascii=False,indent=2),encoding='utf-8')
    (args.out/'quality.json').write_text(json.dumps({'papers':len(catalog),'technical_pdf_preflight_passed':len(catalog),
        'evidence_candidates':len(records),'question_candidates':len(qrows),
        'cross_paper_candidates':len(pairs),'human_gold_items':0,'measured_f1':None,
        'licensing_checked_for_redistribution':False,'per_paper':quality},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'papers':len(catalog),'evidence_candidates':len(records),
                      'qa_candidates':len(qrows),'cross_paper_pairs':len(pairs)},ensure_ascii=False))

if __name__=='__main__':main()
