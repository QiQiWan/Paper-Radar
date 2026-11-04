# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Reproducible offline relevance evaluation using researcher-labeled CSV.

This script never calls cloud APIs. Metrics are relative to the explicitly
judged pool; unjudged results are tracked and must not be treated as known bad.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3


def read_labels(path: Path) -> dict[str, dict[str, int]]:
    labels: dict[str, dict[str,int]] = {}
    with path.open('r', encoding='utf-8-sig', newline='') as file:
        for line, row in enumerate(csv.DictReader(file),2):
            if not {'topic_id','paper_id','label'} <= set(row):
                raise ValueError('标注 CSV 需包含 topic_id,paper_id,label 三列。')
            topic = (row['topic_id'] or '').strip()
            paper = (row['paper_id'] or '').strip()
            if not topic or not paper:
                raise ValueError(f'第 {line} 行的主题或论文 ID 为空。')
            try:
                grade = int(row['label'])
            except (ValueError, TypeError) as exc:
                raise ValueError(f'第 {line} 行标签应为 0、1、2 或 3。') from exc
            if grade not in (0,1,2,3):
                raise ValueError(f'第 {line} 行标签必须在 0 至 3。')
            labels.setdefault(topic,{})
            if paper in labels[topic]:
                raise ValueError(f'第 {line} 行存在重复的主题论文标注。')
            labels[topic][paper] = grade
    if not labels:
        raise ValueError('没有有效的论文标注。')
    return labels


def metrics(ranked: list[str], judged: dict[str,int], k: int = 10,
            relevant_level: int = 2) -> dict:
    """Calculate judged-pool P@k/Recall@k/NDCG@k and labeling coverage.

    Unjudged positions contribute zero to a conservative lower-bound score,
    so coverage is always reported alongside metrics.
    """
    if k < 1:
        raise ValueError('k 必须大于零。')
    top=ranked[:k]
    matches = sum(judged.get(pid,0) >= relevant_level for pid in top)
    relevant_total = sum(level >= relevant_level for level in judged.values())
    def dcg(grades):
        return sum((2**g - 1)/math.log2(i+2) for i,g in enumerate(grades))
    got=dcg([judged.get(pid,0) for pid in top])
    ideal=dcg(sorted(judged.values(),reverse=True)[:k])
    observed = sum(pid in judged for pid in top)
    first_relevant=next((i+1 for i,pid in enumerate(ranked) if judged.get(pid,0)>=relevant_level),None)
    return {'k':k, 'judged_count':len(judged), 'ranked_count':len(ranked),
            'topk_judged_count': observed, 'topk_label_coverage':round(observed/max(1,len(top)),4),
            'precision_at_k_lower_bound':round(matches/k,4),
            'recall_at_k_judged_pool':round(matches/relevant_total,4) if relevant_total else None,
            'ndcg_at_k_judged_pool':round(got/ideal,4) if ideal else None,
            'mrr_judged_pool':round(1/first_relevant,4) if first_relevant else 0.0,
            'judged_relevant_total':relevant_total,
            'interpretation':'未标注的推荐位置以0计入保守得分；Recall分母仅为人工标注的相关文献，不代表全领域召回率。'}


def evaluate_database(db_path: Path, labels_file: Path, *, k: int=10) -> dict:
    labels=read_labels(labels_file)
    if not db_path.is_file():
        raise FileNotFoundError('未找到论文数据库：'+str(db_path))
    result={'generated_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'database':str(db_path),'labels_file':str(labels_file),'k':k,'topics':{}}
    with sqlite3.connect(db_path) as db:
        db.row_factory=sqlite3.Row
        for topic, judged in labels.items():
            rows=db.execute('''SELECT paper_id FROM topic_papers WHERE topic_id=?
                ORDER BY score DESC, matched_at DESC, paper_id ASC''',(topic,)).fetchall()
            ranking=[row['paper_id'] for row in rows]
            summary=metrics(ranking,judged,k)
            summary['judged_present_in_topic']=sum(pid in set(ranking) for pid in judged)
            summary['missing_judged_papers']=[pid for pid in judged if pid not in set(ranking)][:30]
            result['topics'][topic]=summary
    return result


def export_labeling_template(db_path: Path, output: Path, *, per_topic: int=50) -> dict:
    """Export a human-annotation template; score is never a ground-truth label."""
    if not db_path.is_file():
        raise FileNotFoundError('未找到论文数据库：'+str(db_path))
    if not 1 <= per_topic <= 300:
        raise ValueError('每主题导出数应为 1 至 300。')
    output.parent.mkdir(parents=True,exist_ok=True)
    total=0
    topic_count=0
    with sqlite3.connect(db_path) as db, output.open('w',encoding='utf-8-sig',newline='') as file:
        db.row_factory=sqlite3.Row
        writer=csv.writer(file)
        writer.writerow(['topic_id','paper_id','label','rank','title','source','published','url','notes'])
        topic_rows=db.execute('SELECT DISTINCT topic_id FROM topic_papers ORDER BY topic_id').fetchall()
        def safe(value):
            value=str(value or '')
            if value.lstrip().startswith(('=','+','-','@')):
                return "'"+value
            return value
        for topic in topic_rows:
            tid=topic['topic_id']
            rows=db.execute('''SELECT tp.paper_id,p.title,p.source,p.published,p.url
                FROM topic_papers tp JOIN papers p ON p.paper_id=tp.paper_id
                WHERE tp.topic_id=? ORDER BY tp.score DESC,tp.matched_at DESC,tp.paper_id ASC
                LIMIT ?''',(tid,per_topic)).fetchall()
            if rows:topic_count+=1
            for i,row in enumerate(rows,1):
                writer.writerow([safe(tid),safe(row['paper_id']),'',i,safe(row['title']),
                                 safe(row['source']),safe(row['published']),safe(row['url']),''])
                total+=1
    return {'output':str(output),'topics':topic_count,'papers':total,
            'instructions':'请仅填写 label 列：0=无关、1=弱相关、2=相关、3=高度相关。其他字段可作为人工审阅辅助。'}

def main(argv=None):
    parser=argparse.ArgumentParser(description='Paper-Radar 离线推荐质量评估，无 API 调用')
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--labels',type=Path,help='含 topic_id,paper_id,label 的已标注 CSV')
    mode.add_argument('--export-template',type=Path,help='导出人工标注 CSV 模板，不调用模型')
    parser.add_argument('--db',type=Path,default=Path('data/papers.sqlite3'))
    parser.add_argument('--k',type=int,default=10)
    parser.add_argument('--max-per-topic',type=int,default=50)
    parser.add_argument('--out',type=Path,default=Path('reports/recommendation_evaluation.json'))
    args=parser.parse_args(argv)
    if args.export_template is not None:
        summary=export_labeling_template(args.db,args.export_template,per_topic=args.max_per_topic)
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        return 0
    if not 1 <= args.k <= 100:
        parser.error('--k 必须在 1~100 之间')
    summary=evaluate_database(args.db,args.labels,k=args.k)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(args.out),'topics':summary['topics']},ensure_ascii=False,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
