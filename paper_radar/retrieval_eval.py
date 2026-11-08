# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Reproducible topic-level retrieval benchmarks with explicit label coverage.

Gold JSONL: {"topic":"...","paper_id":"...","relevant":true|false}
Run JSONL: {"topic":"...","paper_id":"...","rank":1,"score":2.5}
Only closed, adjudicated topic pools may claim recall; otherwise result is
labelled partial-pool recall and cannot be presented as full corpus recall.
"""
from __future__ import annotations
from collections import defaultdict
import json
import math
from pathlib import Path


def _rows(path: Path, max_rows=100000):
    path=Path(path)
    if path.stat().st_size>12_000_000:
        raise ValueError('评估输入超过 12 MB')
    out=[]
    for line_no,line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(),1):
        if not line.strip():continue
        if len(out)>=max_rows:raise ValueError('评估记录数超过安全上限')
        item=json.loads(line)
        if not isinstance(item,dict) or not isinstance(item.get('topic'),str) or not isinstance(item.get('paper_id'),str):
            raise ValueError(f'第 {line_no} 行缺少 topic / paper_id')
        if not 1<=len(item['topic'])<=100 or not 1<=len(item['paper_id'])<=200:
            raise ValueError('评估 ID 长度无效')
        out.append(item)
    return out


def evaluate(gold_records: list[dict], prediction_records: list[dict], *, k=20, closed_pool=False) -> dict:
    if type(k) is not int or not 1<=k<=200:
        raise ValueError('k 必须在 1—200 之间')
    gold=defaultdict(dict);runs=defaultdict(list)
    for row in gold_records:
        key=(row.get('topic'),row.get('paper_id'))
        if type(row.get('relevant')) is not bool:
            raise ValueError('relevant 必须有明确的人工布尔标注')
        if key[1] in gold[key[0]]:
            raise ValueError('同一主题和论文存在重复金标准')
        gold[key[0]][key[1]]=row['relevant']
    for row in prediction_records:
        rank=row.get('rank')
        if type(rank) is not int or rank<1 or rank>100000:
            raise ValueError('rank 必须为正整数')
        runs[row['topic']].append((rank,row['paper_id']))
    reports=[]
    for topic in sorted(set(gold)|set(runs)):
        ranked=sorted(runs.get(topic,[]))
        if len(set(rank for rank,_ in ranked))!=len(ranked):
            raise ValueError('同一主题的排序名次不能重复')
        ids=[p for _,p in ranked]
        duplicated=len(ids)-len(set(ids))
        unique=list(dict.fromkeys(ids))
        relevant={p for p,v in gold.get(topic,{}).items() if v}
        top=unique[:k]
        known=[p for p in top if p in gold.get(topic,{})]
        tp=sum(p in relevant for p in top)
        positive=len(relevant)
        # Unjudged predictions MUST NOT silently count as false in precision;
        # they are an explicit coverage deficit and judged precision differs.
        judged_p=tp/len(known) if known else None
        precision=tp/k if len(known)==len(top) and top else None
        pool_recall=len(relevant.intersection(unique[:200])) / positive if positive else None
        recall=pool_recall if closed_pool else None
        reports.append({'topic':topic,'retrieved':len(unique),'duplicates':duplicated,
                        'gold_labeled':len(gold.get(topic,{})),'gold_positive':positive,
                        'judged_at_k':len(known),'unjudged_at_k':len(top)-len(known),
                        'precision_at_k':precision,'judged_precision_at_k':judged_p,
                        'recall_at_200':recall,'partial_pool_recall_at_200':None if closed_pool else pool_recall,
                        'recall_scope':'closed_pool' if closed_pool else 'partial_pool',
                        'label_coverage_at_k':len(known)/len(top) if top else 0.0})
    valid=[r['precision_at_k'] for r in reports if r['precision_at_k'] is not None]
    return {'k':k,'closed_pool':bool(closed_pool),'topics':reports,
            'macro_precision_at_k':sum(valid)/len(valid) if valid else None,
            'macro_precision_valid_topics':len(valid),
            'warning':('评估集尚未覆盖所有检索结果；不能把部分标注池召回率当作全库召回率。'
                       if not closed_pool else '封闭评估池由用户声明，请独立审核其标签与覆盖率。')}


def evaluate_files(gold: Path, run: Path, *, k=20, closed_pool=False):
    return evaluate(_rows(gold),_rows(run),k=k,closed_pool=closed_pool)


def main():
    import argparse
    ap=argparse.ArgumentParser(description='分主题检索质量和人工标签覆盖率')
    ap.add_argument('--gold',type=Path,required=True)
    ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--k',type=int,default=20)
    ap.add_argument('--closed-pool',action='store_true',help='仅在真实人工确认评估集封闭时使用')
    ap.add_argument('--output',type=Path)
    args=ap.parse_args()
    result=evaluate_files(args.gold,args.run,k=args.k,closed_pool=args.closed_pool)
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(text,encoding='utf-8')
    print(text)

if __name__=='__main__':main()
