# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Real-library acceptance for v1.52.1; zero values are never fabricated.

Usage: python -m paper_radar.research_acceptance --db /path/to/papers.sqlite3
       --topic default --expected-count 72 --output pilot_v1521.zip
The tool does not download PDFs or call a paid model by default.
"""
from __future__ import annotations
import argparse
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
from time import monotonic
import zipfile

from .core import open_database
from .abstract_enrichment import quality_summary
from .research_intelligence import preview_local,run_local,_paper_rows
from .pilot_audit import overview as pilot_overview
from .research_scope import MODES

DEFAULT_QUESTION='神经算子在不同网格和几何条件下的泛化能力，现有实验有哪些一致结果和局限？'


def _csv(rows,fields):
    dest=io.StringIO(newline='');writer=csv.DictWriter(dest,fieldnames=fields,extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        row={k:(''+str(row.get(k,''))) for k in fields}
        writer.writerow({k:("'"+v if v.lstrip().startswith(('=','+','-','@','\t','\r')) else v)
            for k,v in row.items()})
    return ('\ufeff'+dest.getvalue()).encode('utf-8')


def evaluate(db_path:Path,topic='default',question=DEFAULT_QUESTION,*,expected_count=72,
             mode='all',manual_ids=None,run_analysis=True,allow_model=False,
             confirm_transfer=False,model_config=None,gold_csv:Path|None=None):
    db_path=Path(db_path).expanduser().resolve()
    if not db_path.is_file():raise FileNotFoundError('未找到论文数据库：'+str(db_path))
    if mode not in MODES:raise ValueError('分析范围无效')
    if allow_model and not confirm_transfer:raise ValueError('模型分析需要明确授权发送题名和摘要')
    start=monotonic()
    with open_database(db_path) as db:
        rows,truncated=_paper_rows(db,topic,'local')
        q=quality_summary(db,topic)
        pilot=pilot_overview(db,topic,db_path)['summary']
    preview=preview_local(db_path,topic,question)
    total=len(rows)
    report=None
    if run_analysis and total:
        report=run_local(db_path,topic,question,selection_mode=mode,
            paper_ids=manual_ids if mode=='manual' else None,use_model=allow_model,
            confirm_transfer=confirm_transfer,model_config=model_config or {})
    brief=(report or {}).get('brief') or {}
    selection=brief.get('selection') or {}
    by_id={str(x['paper_id']):str(x.get('abstract') or '') for x in rows}
    insights=brief.get('abstract_insights') or []
    verified_source_spans=0
    for line in insights:
        abstract=by_id.get(line.get('paper_id'),'')
        if ' '.join(str(line.get('quote','')).split()) in ' '.join(abstract.split()):
            verified_source_spans+=1
    gold_stats=None
    if gold_csv is not None:
        with Path(gold_csv).open(encoding='utf-8-sig',newline='') as f:
            gold=list(csv.DictReader(f))
        positives={r['paper_id'] for r in gold if str(r.get('relevant','')).strip()=='1' and r.get('paper_id') in by_id}
        negatives={r['paper_id'] for r in gold if str(r.get('relevant','')).strip()=='0' and r.get('paper_id') in by_id}
        predicted=set(preview['recommended_ids'])
        labeled=positives|negatives
        gold_stats={'gold_labeled_papers':len(labeled),'positive_papers':len(positives),
            'recommendation_precision':round(len(predicted&positives)/len(predicted&labeled),4) if predicted&labeled else None,
            'important_paper_recall':round(len(predicted&positives)/len(positives),4) if positives else None,
            'source':'independent_manual_annotation'}
    summary={'schema_version':'1.52.1','evaluation_type':'connected_local_library',
        'database_present':True,'has_real_papers_in_database':total>0,
        'expected_count':expected_count,'actual_count':total,
        'matches_expected_count':total==expected_count,
        'topic_papers_truncated':truncated,'topic':topic,
        'question_sha256':sha256(question.encode('utf-8')).hexdigest(),
        'evaluation_elapsed_seconds':round(monotonic()-start,3),
        'abstract_quality':q['by_quality'],'actionable_abstracts':q['needs_refresh'],
        'checked_abstracts_no_improvement':q['checked_without_improvement'],
        'recommended_count':len(preview['recommended_ids']),
        'selection_count':selection.get('selected_count') if report else None,
        'selected_abstracts':selection.get('abstract_available_count') if report else None,
        'analysis_batches':len(brief.get('analysis_batches',[])) if report else None,
        'model_status':(brief.get('model') or {}).get('status') if report else 'not_run',
        'model_batches_attempted':(brief.get('model') or {}).get('batch_attempted') if report else None,
        'model_actual_usage_and_cost':None,
        'model_actual_usage_reason':'模型提供商未返回可核对的逐次 usage 和账单；仅记录请求及预算估计',
        'abstract_source_quotes':len(insights) if report else None,
        'source_quotes_exactly_in_stored_abstracts':verified_source_spans if report else None,
        'source_quote_integrity_fraction':round(verified_source_spans/len(insights),4) if insights else None,
        'pdf_acquisition_rate':pilot.get('pdf_acquisition_rate'),
        'pdf_acquisition_denominator':pilot.get('pilot_selected_unique'),
        'field_accuracy_on_reviewed_fields':pilot.get('field_accuracy_on_reviewed_fields'),
        'field_accuracy_denominator':pilot.get('field_accuracy_denominator'),
        'manual_review_seconds':pilot.get('manual_review_seconds'),
        'method_confirmed_comparison_groups':pilot.get('method_confirmed_comparison_groups'),
        'gold_relevance_metrics':gold_stats,
        'limitations':['只有连接到真实本地文献数据库，才能得到当前用户的实际覆盖数字。',
            '没有人工字段标注不能计算提取准确率；没有有效审核计时不能计算人力成本。',
            '跨论文组数只计入已经逐项确认指标、任务、数据划分和预算的有效对照。',
            '抽取摘要来源句子属于文本完整性验证，不等同于科研结论的准确性评价。']}
    rows_out=[{'paper_id':str(x['paper_id']),'title':str(x.get('title') or ''),
        'abstract_available':bool(str(x.get('abstract') or '').strip()),
        'recommended':str(x['paper_id']) in set(preview['recommended_ids'])} for x in rows]
    return {'summary':summary,'papers':rows_out,'report_id':(report or {}).get('id','')}


def package(result:dict,output:Path):
    dest=Path(output);dest.parent.mkdir(parents=True,exist_ok=True)
    summary=result['summary']
    with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('summary.json',json.dumps(summary,ensure_ascii=False,indent=2))
        z.writestr('paper_coverage.csv',_csv(result['papers'],['paper_id','title','abstract_available','recommended']))
        lines=['# Paper-Radar v1.52.1 实机试点检查','',
            '本报告读取当前工作站连接的本地 SQLite 文献库。',
            f"- 实际论文数：{summary['actual_count']}；预期 {summary['expected_count']}；一致：{summary['matches_expected_count']}",
            f"- 摘要可补数量：{summary['actionable_abstracts']}；已检查暂未改善：{summary['checked_abstracts_no_improvement']}",
            f"- 本轮选文：{summary['selection_count']}；分批：{summary['analysis_batches']}",
            f"- PDF 获取率：{summary['pdf_acquisition_rate']}（未开展批量下载时为 null）",
            f"- 字段正确率：{summary['field_accuracy_on_reviewed_fields']}（未人工标注时为 null）",
            f"- 人工核对耗时：{summary['manual_review_seconds']} 秒（未计时时为 null）",
            f"- 已确认可比组：{summary['method_confirmed_comparison_groups']} 组",'',
            '## 说明','']+['- '+x for x in summary['limitations']]
        z.writestr('README_FIRST.md','\n'.join(lines)+'\n')
        z.writestr('report_id.txt',result['report_id'])
    return dest


def main(argv=None):
    parser=argparse.ArgumentParser(description='Paper-Radar real local library acceptance')
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--topic',default='default')
    parser.add_argument('--question',default=DEFAULT_QUESTION)
    parser.add_argument('--expected-count',type=int,default=72)
    parser.add_argument('--mode',choices=MODES,default='all')
    parser.add_argument('--manual-id',action='append',default=[])
    parser.add_argument('--gold-relevance-csv',type=Path)
    parser.add_argument('--no-analysis',action='store_true')
    parser.add_argument('--output',type=Path,default=Path('Paper-Radar-Pilot-v1521.zip'))
    args=parser.parse_args(argv)
    result=evaluate(args.db,args.topic,args.question,expected_count=args.expected_count,
        mode=args.mode,manual_ids=args.manual_id,run_analysis=not args.no_analysis,
        gold_csv=args.gold_relevance_csv)
    path=package(result,args.output)
    print(json.dumps({'output':str(path),'summary':result['summary']},ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
