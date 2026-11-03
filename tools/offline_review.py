# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Audit a Paper-Radar CSV against time and topic gates, with no network calls.

Example:
  python tools/offline_review.py --input reports/文献索引.csv \
    --keywords 'embankment,slope,numerical' --as-of 2026-11-03 --days 7 \
    --out reports/复核
"""
from __future__ import annotations
import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from paper_radar.core import DEFAULTS, rank
from paper_radar.models import Paper
from paper_radar.quality import valid_recent_publication
from paper_radar.reports.word import write_report
from paper_radar.reports.text import write_markdown, write_csv


def read_index(path: Path) -> list[Paper]:
    with path.open('r', encoding='utf-8-sig', newline='') as file:
        records = list(csv.DictReader(file))
    papers = []
    for idx, row in enumerate(records, 1):
        papers.append(Paper(
            paper_id=row.get('论文ID') or f'csv-{idx}', title=row.get('标题') or '',
            abstract=row.get('原始摘要') or '', authors=row.get('作者') or '',
            published=row.get('日期') or '', updated=row.get('日期') or '',
            category=row.get('分类') or '', url=row.get('论文链接') or '',
            pdf_url=row.get('PDF链接') or '', source=row.get('来源') or 'unknown',
            venue=row.get('期刊/会议') or '',doi=row.get('DOI') or '',
            summary=row.get('中文速览') or '',
        ))
    return papers


def audit(papers: list[Paper], *, keywords: list[str], now: datetime, days: int) -> tuple[list[Paper], list[dict]]:
    result, decisions = [], []
    for p in papers:
        if not valid_recent_publication(p.published, now, days):
            decision='日期超出范围或不完整'
        else:
            p.score = rank(p, keywords)
            decision='入选' if not keywords or p.score > 0 else '主题相关性不足'
        decisions.append({'paper_id':p.paper_id,'title':p.title,'date':p.published,
                          'source':p.source,'score':p.score,'decision':decision})
        if decision=='入选':
            result.append(p)
    result.sort(key=lambda p:(p.score,p.published),reverse=True)
    return result, decisions


def create_review(input_file: Path, out_dir: Path, keywords: list[str], now: datetime, days: int) -> dict:
    papers=read_index(input_file)
    selected,decisions=audit(papers,keywords=keywords,now=now,days=days)
    out_dir.mkdir(parents=True,exist_ok=True)
    counts={'total':len(papers),'selected':len(selected),
            'date_removed':sum(d['decision']=='日期超出范围或不完整' for d in decisions),
            'topic_removed':sum(d['decision']=='主题相关性不足' for d in decisions)}
    with (out_dir/'复核明细.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['paper_id','title','date','source','score','decision']);w.writeheader();w.writerows(decisions)
    config={**DEFAULTS,'keywords':keywords,'sources':sorted(set(p.source for p in selected)) or ['arxiv'],
            'lookback_days':days,'_quality_filters':{'date_removed':counts['date_removed'],'topic_removed':counts['topic_removed']}}
    write_report(out_dir/'近期文献汇总_复核.docx','近期文献汇总（本地复核）',selected,config,len(selected))
    write_markdown(out_dir/'近期文献汇总_复核.md',selected,config)
    write_csv(out_dir/'文献索引_入选.csv',selected)
    (out_dir/'复核说明.md').write_text(
        '# 文献筛选复核\n\n'
        f'原始条目：{counts["total"]}；入选：{counts["selected"]}；日期超出范围或不完整：{counts["date_removed"]}；主题相关性不足：{counts["topic_removed"]}。\n\n'
        f'复核基准日期：{now.date().isoformat()}；回溯：{days}天；关键词：{", ".join(keywords)}。\n\n'
        '本复核仅处理已导出的题录，不连接学术网站，不改变原数据库。原始元数据真实性和论文研究结论仍需到论文主页核对。\n',
        encoding='utf-8')
    return counts


def main():
    p=argparse.ArgumentParser(description='使用本地 CSV 复核日期与研究主题，并生成 Word/Markdown')
    p.add_argument('--input',type=Path,required=True,help='Paper-Radar 生成的文献索引.csv')
    p.add_argument('--keywords',required=True,help='英文研究关键词，逗号分隔')
    p.add_argument('--as-of',required=True,help='基准日期 YYYY-MM-DD')
    p.add_argument('--days',type=int,default=7)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    as_of=datetime.strptime(args.as_of,'%Y-%m-%d').replace(tzinfo=timezone.utc)
    keywords=[x.strip() for x in args.keywords.replace('，',',').split(',') if x.strip()]
    counts=create_review(args.input,args.out,keywords,as_of,args.days)
    print(f"Paper-Radar：原始 {counts['total']}，入选 {counts['selected']}；日期排除 {counts['date_removed']}，主题排除 {counts['topic_removed']}")
    print('输出：',args.out.resolve())

if __name__=='__main__': main()
