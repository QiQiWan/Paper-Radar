# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Reproducible, evidence-first evaluation on real published arXiv papers.

The benchmark ships *metadata and short manually checked evidence*, not PDFs.
Fetch is explicit and requires internet. No LLM is silently called.
Absent PDFs or prediction files are reported as NOT MEASURED, never 0% accuracy.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
BENCH=ROOT/'benchmarks'/'real_papers_v1'
MAX_DOWNLOAD=45*1024*1024

class SafeArxivRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        url=urlsplit(newurl)
        if url.scheme!='https' or url.hostname not in {'arxiv.org','export.arxiv.org','static.arxiv.org'} or url.username or url.password:
            raise ValueError('Unsafe PDF redirect')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def load():
    manifest=json.loads((BENCH/'manifest.json').read_text(encoding='utf-8'))
    gold=json.loads((BENCH/'gold_evidence.json').read_text(encoding='utf-8'))
    ids={p['paper_id'] for p in manifest['papers']}
    assert all(f['paper_id'] in ids for f in gold['fields'])
    assert len({f['id'] for f in gold['fields']})==len(gold['fields'])
    return manifest,gold


def normalized(text):
    return re.sub(r'\s+','',str(text).casefold().replace('\u00ad','').replace('\ufb01','fi'))


def fetch(pdf_dir:Path):
    manifest,_=load()
    pdf_dir.mkdir(parents=True,exist_ok=True)
    reports=[]
    opener=urllib.request.build_opener(SafeArxivRedirect())
    for i,paper in enumerate(manifest['papers']):
        name=paper['paper_id']+paper['version']+'.pdf'
        target=pdf_dir/name
        if target.exists() and target.read_bytes()[:5]==b'%PDF-':
            reports.append({'paper_id':paper['paper_id'],'status':'already_downloaded','bytes':target.stat().st_size})
            continue
        url=paper['pdf_url']
        parts=urlsplit(url)
        if parts.scheme!='https' or parts.hostname!='arxiv.org' or not parts.path.startswith('/pdf/'):
            raise ValueError('Noncanonical PDF URL')
        if i:time.sleep(4)
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'Paper-Radar scholarly evaluation tool (manual use)', 'Accept':'application/pdf'})
            with opener.open(req,timeout=25) as r:
                raw=r.read(MAX_DOWNLOAD+1)
            if len(raw)>MAX_DOWNLOAD or not raw.startswith(b'%PDF-'):
                raise ValueError('PDF invalid or above 45 MiB')
            tmp=target.with_suffix('.partial')
            tmp.write_bytes(raw);tmp.replace(target)
            reports.append({'paper_id':paper['paper_id'],'status':'downloaded',
                'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        except Exception as exc:
            reports.append({'paper_id':paper['paper_id'],'status':'fetch_failed',
                'error':type(exc).__name__+': '+str(exc)[:160]})
    return reports


def parse_pdf(pdf:Path,timeout=45)->dict:
    if not pdf.is_file():return {'status':'pdf_missing'}
    if pdf.stat().st_size>MAX_DOWNLOAD:return {'status':'pdf_too_large'}
    try:
        res=subprocess.run([sys.executable,'-m','paper_radar.portal_pdf_worker',str(pdf)],
             cwd=ROOT,capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired:return {'status':'parse_timeout'}
    if res.returncode:return {'status':'parse_failed','detail':res.stdout[:250]}
    try:
        payload=json.loads(res.stdout)
        if not isinstance(payload.get('pages'),list):raise ValueError('Invalid PDF payload')
    except (ValueError,TypeError):return {'status':'parse_failed'}
    return {'status':'parsed','pages':payload['pages'],'total_pages':payload['total_pages'],
            'sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()}


def records_from_gold(manifest:dict,gold:dict)->list[dict]:
    lookup={x['paper_id']:x for x in manifest['papers']}
    result=[]
    for f in gold['fields']:
        p=lookup[f['paper_id']]
        result.append({'paper_id':f['paper_id'],'title':p['title'],
            'doi':'','url':p['landing_url'],'published':str(p['year']),
            'kind':f['kind'],'dataset':f['dataset'],'metric':f['metric'],
            'value':f['value'],'baseline':f['baseline'],'setting':f['setting'],
            'quote':f['quote'],'page':f['page_index']+1,
            'provenance':'manual_public_pdf_page_reviewed'})
    return result


def evaluate_synthesis(manifest,gold):
    from paper_radar.synthesis import compare_records,to_markdown
    result=compare_records(records_from_gold(manifest,gold))
    compare=set()
    # A group is a 'candidate' only if it spans two genuinely distinct papers.
    for group in result['comparisons']:
        ids={r['paper_id'] for r in group['records']}
        for a in group['records']:
            for b in group['records']:
                if a['paper_id']!=b['paper_id']:
                    compare.add(tuple(sorted((a['paper_id'],b['paper_id']))))
    lookup={x['id']:x for x in gold['fields']}
    labels=gold['pairwise_comparability_gold']
    matched=sum((tuple(sorted((lookup[x['a']]['paper_id'],lookup[x['b']]['paper_id']))) in compare)==x['comparable'] for x in labels)
    positive=sum(x['comparable'] for x in labels)
    return result,{'gold_records':len(gold['fields']),
                   'pairwise_cases':len(labels),
                   'pairwise_correct':matched,
                   'pairwise_accuracy':round(matched/len(labels),4) if labels else None,
                   'positive_pairs':positive,
                   'negative_pairs':len(labels)-positive,
                   'unsafe_candidates':sum(1 for x in labels if not x['comparable'] and
                       tuple(sorted((lookup[x['a']]['paper_id'],lookup[x['b']]['paper_id']))) in compare),
                   'comparison_groups':len(result['comparisons']),
                   'warning':'Only negative pairwise labels in v1: no comparable-pair recall claim.'}


def evaluate_pdf_pages(manifest,gold,pdf_dir:Path):
    fields_by_id={p['paper_id']:[f for f in gold['fields'] if f['paper_id']==p['paper_id']] for p in manifest['papers']}
    found=0; total=0;available=0;details=[]
    for paper in manifest['papers']:
        pdf=pdf_dir/(paper['paper_id']+paper['version']+'.pdf')
        parsed=parse_pdf(pdf)
        record={'paper_id':paper['paper_id'],'pdf_status':parsed['status'],
                'paper_url':paper['landing_url'],'annotation_count':len(fields_by_id[paper['paper_id']])}
        if parsed['status']=='parsed':
            available+=1
            record.update({'page_count':parsed['total_pages'],'sha256':parsed['sha256'],
                          'exceeds_portal_12mib_limit':pdf.stat().st_size>12*1024*1024})
            index={x['page']:x['text'] for x in parsed['pages']}
            checks=[]
            for ann in fields_by_id[paper['paper_id']]:
                page_text=index.get(ann['page_index']+1,'')
                anchor_hit=normalized(ann['anchor']) in normalized(page_text)
                context_hit=normalized(ann['context_anchor']) in normalized(page_text)
                checks.append({'id':ann['id'],'expected_page':ann['page_index']+1,
                               'value_present':anchor_hit,'method_present':context_hit,
                               'verified':anchor_hit and context_hit})
                total+=1;found+=anchor_hit and context_hit
            record['checks']=checks
        details.append(record)
    return {'real_pdfs_parsed':available,'real_pdfs_in_manifest':len(manifest['papers']),
            'gold_field_matches':int(found),'gold_field_checks':total,
            'page_anchor_recall':round(found/total,4) if total else None,
            'measured_on_local_real_pdf':bool(total),'papers':details}


def evaluate_predictions(path:Path|None,gold):
    if not path:return {'measured':False,'reason':'No model predictions supplied'}
    payload=json.loads(path.read_text(encoding='utf-8'))
    predictions=payload['findings'] if isinstance(payload,dict) else payload
    if not isinstance(predictions,list):raise ValueError('Predictions must be a JSON array')
    def key(f):
        return (str(f.get('paper_id','')).strip(),int(f.get('page_index',int(f.get('page',0))-1)),
                normalized(f.get('metric','')),normalized(f.get('value','')))
    target={key(f) for f in gold['fields']}
    found={key(f) for f in predictions}
    hits=len(target & found)
    prec=hits/len(found) if found else 0
    rec=hits/len(target) if target else 0
    return {'measured':True,'gold_count':len(target),'prediction_count':len(found),
            'true_positive':hits,'precision':round(prec,4),'recall':round(rec,4),
            'f1':round(2*prec*rec/(prec+rec),4) if prec+rec else 0}


def run(pdf_dir:Path,predictions:Path|None,output:Path):
    manifest,gold=load()
    synth,quality=evaluate_synthesis(manifest,gold)
    from paper_radar.synthesis import to_markdown
    pdf=evaluate_pdf_pages(manifest,gold,pdf_dir)
    extracted=evaluate_predictions(predictions,gold)
    summary={'benchmark_id':manifest['benchmark_id'],'gold_status':gold['annotation_status'],
             'pdf_extraction':pdf,'synthesis_gold':quality,'model_extraction':extracted,
             'limitations':['Real PDFs are not included in this repository.',
               'Manual PDF table entries are initial seed labels, not a large statistically representative sample.',
               'Model precision/recall is not measured unless predictions are provided.',
               'Negative comparison labels have no positive-case sensitivity measurement.']}
    output.mkdir(parents=True,exist_ok=True)
    # The synthesis report uses REAL manually checked paper records, distinctly
    # labeled as imported/unverified by the local PDF parser.
    academic_report=to_markdown(synth,'真实论文人工校验种子集',imported=True)
    (output/'real_paper_synthesis.md').write_text(academic_report,encoding='utf-8')
    retained={str(r['paper_id']) for r in synth['records']}
    report_coverage={'gold_rows_retained':len(synth['records']),
                     'gold_rows_with_source_page_and_quote':sum(
                        bool(r.get('url','').startswith('https://') and r.get('page',0)>0 and r.get('quote'))
                        for r in synth['records']),
                     'distinct_real_papers_in_report':len(retained),
                     'manual_source_only':True,
                     'safe_no_cross_paper_percent_improvement':True}
    summary['synthesis_report_structure']=report_coverage
    (output/'real_paper_eval.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    txt=['# Paper-Radar 真实论文评估初始报告','',
         f'已复核论文来源：{len(manifest["papers"])} 篇；人工证据记录：{len(gold["fields"])} 条。',
         f'可成功解析的本地真实 PDF：{pdf["real_pdfs_parsed"]} 篇 / {pdf["real_pdfs_in_manifest"]} 篇。',
         f'正确定位的实验条目：{pdf["gold_field_matches"]} / {pdf["gold_field_checks"]}；未获取原始 PDF 时不计算全文解析召回率。',
         f'跨论文不当对比候选：{quality["unsafe_candidates"]}；共 {quality["pairwise_cases"]} 项人工标注的不可比对照。',
         f'模型抽取质量：{("已评分" if extracted["measured"] else "未执行；尚无真实模型预测输入")}。',
         '', '## 学术质量限制','',
         '这是一套可复现的真实论文种子基准，仅能初步验证证据定位与错误对比拦截。需要人工复核扩展至多种数据集与可比的正例后，才能评价 SynthesisAgent 的跨论文综合判断质量。',
         '原论文 PDF 不随软件分发。网络不通时只运行人工标签对照，不能宣称真实 PDF 解析已完成。','',
         '## 数据源','']
    for p in manifest['papers']:txt.append(f'- {p["title"]}：{p["landing_url"]}（{p["version"]}）')
    (output/'real_paper_eval.md').write_text('\n'.join(txt)+'\n',encoding='utf-8')
    return summary


def cli(argv=None):
    p=argparse.ArgumentParser(description='Real-paper evidence and synthesis benchmark')
    p.add_argument('command',choices=('fetch','score'))
    p.add_argument('--pdf-dir',type=Path,default=BENCH/'pdfs')
    p.add_argument('--predictions',type=Path)
    p.add_argument('--output',type=Path,default=BENCH/'results')
    args=p.parse_args(argv)
    if args.command=='fetch':result=fetch(args.pdf_dir)
    else:result=run(args.pdf_dir,args.predictions,args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(cli())
