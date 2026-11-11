# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Conservative corpus review/accuracy/accounting, with explicit denominators.

Only adjudicated real-PDF evidence linked to two completed independent
annotations may contribute to a headline extraction F1. Unlabeled = unmeasured.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re

ID = re.compile(r'^\d{4}\.\d{4,5}$')
SHA = re.compile(r'^[0-9a-f]{64}$')
FIELDS = ('paper_id','page_index','method','dataset','metric','value')


def rows(path:Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open('r',encoding='utf-8-sig',newline='') as handle:
        return list(csv.DictReader(handle))


def norm(value):
    return ' '.join(str(value or '').casefold().split())


def key(row):
    return tuple(norm(row.get(f,'')) for f in FIELDS)


def load_manifest(folder:Path):
    result=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    papers=result['papers']
    if len(papers)<30 or len({p['paper_id'] for p in papers})!=len(papers):
        raise ValueError('真实论文清单必须包括至少 30 篇不同论文。')
    for p in papers:
        pid=p['paper_id']
        if not ID.fullmatch(pid) or p['landing_url']!=f'https://arxiv.org/abs/{pid}' or \
            p['pdf_url']!=f'https://arxiv.org/pdf/{pid}{p["version"]}' or not re.fullmatch(r'v[1-9]\d*',p['version']):
            raise ValueError('论文 ID 或 PDF 固定版本链接不一致。')
        if not p.get('title') or not p.get('category'):
            raise ValueError('论文元数据缺少必需字段。')
    return result


def completed_review_sets(folder:Path,paper_ids:set[str]):
    reviews={}
    for reviewer in ('A','B'):
        by_paper=defaultdict(list)
        for row in rows(folder/f'annotations_{reviewer}.csv'):
            pid=row.get('paper_id','')
            if pid not in paper_ids or row.get('reviewer')!=reviewer:
                raise ValueError('标注表存在未知论文或审核人。')
            by_paper[pid].append(row)
        reviewed={}
        for pid,values in by_paper.items():
            # A stub row remains pending until the reviewer explicitly signs off,
            # including genuine no-experiment papers (empty findings).
            if not all(norm(v.get('independent_review_complete'))=='yes' for v in values):
                continue
            if not all(SHA.fullmatch(v.get('pdf_sha256','')) for v in values):
                raise ValueError(f'{reviewer} 的 {pid} 已标记完成但缺少 PDF SHA-256。')
            if len({v.get('pdf_sha256') for v in values})!=1:
                raise ValueError(f'{reviewer} 的 {pid} 使用了不同 PDF 文件版本。')
            completed=[]
            for v in values:
                if any(v.get(x,'').strip() for x in ('method','dataset','metric','value')):
                    if not all(v.get(x,'').strip() for x in FIELDS):
                        raise ValueError(f'{reviewer} 的 {pid} 存在不完整的实验字段。')
                    if not SHA.fullmatch(v.get('pdf_sha256','')):
                        raise ValueError(f'{reviewer} 的 {pid} 尚未记录真实 PDF SHA-256。')
                    if not v.get('quote','').strip():
                        raise ValueError('实验标签缺少原文引文。')
                    completed.append(v)
            reviewed[pid]=completed
        reviews[reviewer]=reviewed
    # Independent reviewers must use an identical immutable PDF version.
    for pid in set(reviews['A']) & set(reviews['B']):
        sha_a=reviews['A'][pid][0]['pdf_sha256'] if reviews['A'][pid] else next(
            x['pdf_sha256'] for x in rows(folder/'annotations_A.csv') if x['paper_id']==pid)
        sha_b=reviews['B'][pid][0]['pdf_sha256'] if reviews['B'][pid] else next(
            x['pdf_sha256'] for x in rows(folder/'annotations_B.csv') if x['paper_id']==pid)
        if sha_a != sha_b:
            raise ValueError(f'{pid} 的两位审核人使用了不同 PDF 版本。')
    return reviews


def checked_pdf_hash(folder:Path,paper:dict) -> str|None:
    path=folder/'pdfs'/(paper['paper_id']+paper['version']+'.pdf')
    if (folder/'pdfs').is_symlink() or not path.is_file() or path.is_symlink() or path.stat().st_size>45*1024*1024:
        return None
    with path.open('rb') as f:
        if f.read(5)!=b'%PDF-':return None
        f.seek(0)
        digest=hashlib.sha256()
        for block in iter(lambda:f.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()



def gold_quotes_in_pdf(folder:Path, by_id:dict, gold:list[dict]) -> tuple[bool,list[str]]:
    """Strict PDF-text check for gold evidence; OCR-only pages require another audit.

    No implicit network traffic; only fixed-version local PDFs are examined.
    """
    if not gold:return False,[]
    try:
        from pypdf import PdfReader
    except ImportError:
        return False,['pypdf_missing']
    errors=[];readers={}
    for r in gold:
        pid=r['paper_id'];p=by_id[pid]
        try:
            page=int(r['page_index'])
            if not 0<=page<400:raise ValueError('Invalid page')
            if pid not in readers:
                path=folder/'pdfs'/(pid+p['version']+'.pdf')
                readers[pid]=PdfReader(str(path),strict=False)
            reader=readers[pid]
            if page>=len(reader.pages):raise ValueError('Page outside PDF')
            text=reader.pages[page].extract_text() or ''
            quote=norm(r['quote'])
            if not quote or quote not in norm(text):
                errors.append(f'{pid}:page={page+1}:quote_not_found_in_pdf_text')
        except Exception:
            errors.append(f'{pid}:page={r.get("page_index")}:pdf_parse_or_page_error')
    return not errors,errors[:30]

def review_coverage(folder:Path) -> dict:
    manifest=load_manifest(folder)
    ids={p['paper_id'] for p in manifest['papers']}
    reviews=completed_review_sets(folder,ids)
    double=sorted(set(reviews['A']) & set(reviews['B']))
    disagreements=[]
    for pid in double:
        sa={key(x) for x in reviews['A'][pid]}
        sb={key(x) for x in reviews['B'][pid]}
        if sa!=sb:disagreements.append(pid)
    by_category=Counter(p['category'] for p in manifest['papers'])
    pdf_count=sum(checked_pdf_hash(folder,p) is not None for p in manifest['papers'])
    return {'papers_total':len(ids),'topic_distribution':dict(sorted(by_category.items())),
            'review_A_completed':len(reviews['A']), 'review_B_completed':len(reviews['B']),
            'double_reviewed':len(double),'double_review_agreement':
                round((len(double)-len(disagreements))/len(double),4) if double else None,
            'double_review_disagreement_papers':disagreements,
            'meta_verified_count':sum(p.get('metadata_verification','').startswith('arxiv_landing_checked_')
                                      for p in manifest['papers']),
            'real_pdfs_available':pdf_count,
            'real_pdfs_included':pdf_count>0,
            'review_state':'pending' if len(double)<len(ids) else 'completed',
            'notice':'论文 URL 经公开 arXiv 页面确认；双人标注、PDF 文件检查和模型抽取仍是独立验收阶段。'}


def adjudicated_quality(folder:Path,predictions:Path|None) -> dict:
    manifest=load_manifest(folder)
    ids={p['paper_id'] for p in manifest['papers']}
    reviews=completed_review_sets(folder,ids)
    completed=set(reviews['A'])&set(reviews['B'])
    adjud=rows(folder/'adjudicated_gold.csv')
    good=[];seen=set()
    by_id={p['paper_id']:p for p in manifest['papers']}
    for r in adjud:
        pid=r.get('paper_id','')
        if pid not in completed:
            raise ValueError(f'论文 {pid} 尚未完成双人独立标注，不能加入正式金标准。')
        if not SHA.fullmatch(r.get('pdf_sha256','')) or not all(r.get(f,'').strip() for f in FIELDS) or not r.get('quote','').strip():
            raise ValueError('金标准必须具备 PDF 哈希、完整字段和可核对引文。')
        # The signed PDF hash must match the exact bytes for the fixed-version PDF.
        a_rows=reviews['A'][pid]; b_rows=reviews['B'][pid]
        expected=(a_rows or b_rows)[0]['pdf_sha256'] if (a_rows or b_rows) else None
        if not expected:continue
        if r['pdf_sha256']!=expected:
            raise ValueError('仲裁记录的 PDF 哈希与双人标注不一致。')
        k=key(r)
        if k in seen:raise ValueError('金标准存在重复实验标签。')
        seen.add(k);good.append(r)
    measured_papers={r['paper_id'] for r in good}
    verified_pdf_ids={pid for pid in completed if (sha:=checked_pdf_hash(folder,by_id[pid]))
                      and any(x['pdf_sha256']==sha for x in reviews['A'][pid]+reviews['B'][pid])}
    quote_verified,quote_errors = gold_quotes_in_pdf(folder,by_id,good) if measured_papers.issubset(verified_pdf_ids) else (False,[])
    # Empty fully reviewed papers are eligible for false-positive precision,
    # while recall needs at least one gold positive.
    scored_papers=(completed & verified_pdf_ids) if adjud else set()
    if predictions is None or not predictions.is_file() or not scored_papers or not good or not measured_papers.issubset(verified_pdf_ids) or not quote_verified:
        return {'measured':False,'reason':'缺少独立双人标注、对应版本的真实 PDF、仲裁金标准或模型预测。',
                'adjudicated_records':len(good),'double_reviewed_papers':len(completed),
                'pdf_hash_verified_papers':len(verified_pdf_ids), 'pdf_quote_errors':quote_errors,
                'model_f1':None,'model_precision':None,'model_recall':None}
    payload=json.loads(predictions.read_text(encoding='utf-8'))
    findings=payload.get('findings') if isinstance(payload,dict) else payload
    if not isinstance(findings,list):raise ValueError('模型预测需为 findings 数组。')
    predicted=[]
    for row in findings:
        if not isinstance(row,dict) or row.get('paper_id') not in ids:
            raise ValueError('预测记录论文 ID 无效。')
        if row['paper_id'] in scored_papers:
            predicted.append(key(row))
    gold_keys={key(r) for r in good}
    true=len(set(predicted)&gold_keys)
    false=len(predicted)-true
    missing=len(gold_keys)-true
    precision=true/len(predicted) if predicted else 0.0
    recall=true/len(gold_keys)
    f1=2*precision*recall/(precision+recall) if precision+recall else 0.0
    return {'measured':True,'benchmark_papers':len(ids),'scored_papers':len(scored_papers),
            'scored_paper_ids':sorted(scored_papers), 'adjudicated_records':len(good),
            'predictions_in_scored_subset':len(predicted),'true_positive':true,
            'false_positive':false,'false_negative':missing,
            'duplicate_predictions':len(predicted)-len(set(predicted)),
            'pdf_hash_verified_papers':len(verified_pdf_ids), 'pdf_quote_errors':[],
            'model_precision':round(precision,4),'model_recall':round(recall,4),'model_f1':round(f1,4),
            'warning':'仅在已双人审核且仲裁的论文子集上测量，不能代表整个 33 篇清单。'}


def review_burden(folder:Path) -> dict:
    manifest=load_manifest(folder)
    valid={p['paper_id'] for p in manifest['papers']}
    activity=rows(folder/'review_activity.csv')
    actions={'accept','edit','delete','add'}
    counts=Counter();fields_changed=0;seconds=0
    for row in activity:
        if row.get('paper_id') not in valid or row.get('reviewer') not in ('A','B') or row.get('action') not in actions:
            raise ValueError('人工修正记录包含无效论文、审核人或操作类型。')
        try:
            sec=float(row.get('seconds') or 0);changes=int(row.get('field_edits') or 0)
        except (ValueError,TypeError):raise ValueError('人工耗时和字段修改数格式错误。') from None
        if not 0<=sec<=86400 or not 0<=changes<=100:
            raise ValueError('人工修正耗时或数量超出合理范围。')
        counts[row['action']]+=1;seconds+=sec;fields_changed+=changes
    total=sum(counts.values())
    if not total:return {'measured':False,'total_review_actions':0,'correction_rate':None,'review_minutes':None}
    corrected=counts['edit']+counts['delete']+counts['add']
    return {'measured':True,'total_review_actions':total,'corrected_actions':corrected,
            'correction_rate':round(corrected/total,4),
            'fields_changed':fields_changed,'review_minutes':round(seconds/60,2),
            'reviewed_papers':len({x['paper_id'] for x in activity}),
            'definition':'edit+delete+add / all action events; minutes are self-reported, not independent timing.'}


def summary(folder:Path, predictions:Path|None=None) -> dict:
    return {'benchmark':'real_papers_v2','coverage':review_coverage(folder),
            'extraction_quality':adjudicated_quality(folder,predictions),
            'human_correction':review_burden(folder)}
