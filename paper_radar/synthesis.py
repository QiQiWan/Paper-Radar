# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Grounded cross-paper synthesis; exact-condition grouping, no invented scores.

Input evidence from local PDF verification or separately labeled public imports.
No model-generated numerical comparisons, causal claims, or unsourced trends.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timezone
import re
from typing import Iterable


class SynthesisError(ValueError):
    pass


def _normalized(value: str) -> str:
    return re.sub(r'\s+',' ',str(value or '').casefold()).strip()


def compare_records(records: Iterable[dict], *, max_records: int = 1000) -> dict:
    """Create conservative study groups and a period count.

    A group is a *candidate for manual comparison* only if the task's
    dataset/metric/setting/kind are all supplied, and at least two distinct
    papers provide records. Units, splits, seed count and result direction
    still require human checking. This function never ranks raw numbers.
    """
    papers = {}
    records_out = []
    groups = defaultdict(list)
    periods = Counter()
    seen = set()
    diagnostics = {'unverified': 0, 'incomplete': 0, 'duplicates': 0,
                   'missing_baseline': 0, 'conflicting_values': 0,
                   'incompatible_settings': 0}
    loose_groups = defaultdict(set)
    within_paper = defaultdict(set)
    for item in records:
        if len(records_out) >= max_records:
            raise SynthesisError('Evidence quota exceeded')
        required=('paper_id','title','metric','value','quote','page')
        if any(not str(item.get(k) or '').strip() for k in required):
            continue
        record={key: str(item.get(key) or '')[:1200] for key in (
            'paper_id','title','doi','url','published','kind','dataset',
            'metric','value','baseline','setting','quote','provenance')}
        try:record['page']=int(item['page'])
        except (TypeError,ValueError):continue
        if record['page'] < 1:
            continue
        unique=(record['paper_id'],record['kind'],record['dataset'],record['metric'],
                record['value'],record['baseline'],record['setting'],record['page'],record['quote'])
        if unique in seen:
            diagnostics['duplicates']+=1
            continue
        seen.add(unique)
        if record['provenance'] != 'pdf_sha256_verified':
            diagnostics['unverified']+=1
        if not all(record[k] for k in ('kind','dataset','metric','setting')):
            diagnostics['incomplete']+=1
        if not record['baseline']:
            diagnostics['missing_baseline']+=1
        records_out.append(record)
        pid=record['paper_id']
        papers.setdefault(pid,record)
        year=(record['published'] or '')[:4]
        if len(year)==4 and year.isdigit():
            periods[(year, pid)]=1
        if all(record.get(key) for key in ('kind','dataset','metric','setting')):
            key=tuple(_normalized(record[k]) for k in ('kind','dataset','metric','setting'))
            groups[key].append(record)
            loose_groups[key[:3]].add(key[3])
            within_paper[(record['paper_id'],key,record['baseline'])].add(_normalized(record['value']))
    diagnostics['conflicting_values']=sum(len(values)>1 for values in within_paper.values())
    diagnostics['incompatible_settings']=sum(len(values)>1 for values in loose_groups.values())
    comparable=[]
    for key, entries in groups.items():
        if len({e['paper_id'] for e in entries})<2:
            continue
        flags=['即使字段相同，也须复核数据划分、指标定义和计算预算']
        baselines={_normalized(e['baseline']) for e in entries if e['baseline']}
        if len(baselines)>1:flags.append('对照基线不一致，不能直接比较数值')
        if any(not e['baseline'] for e in entries):flags.append('部分记录缺少基线')
        if any(e['provenance']!='pdf_sha256_verified' for e in entries):
            flags.append('包含未在服务器核验原文的用户导入记录')
        if len({('%' in e['value']) for e in entries})>1:
            flags.append('结果数值的百分号单位表达不一致')
        comparable.append({'kind':entries[0]['kind'],'dataset':entries[0]['dataset'],
            'metric':entries[0]['metric'],'setting':entries[0]['setting'],
            'papers':len({e['paper_id'] for e in entries}),
            'records':entries[:100], 'flags':flags,
            'qualification':'仅为字段一致的对比候选；尚未核实量纲、基线、数据划分和统计条件。'})
    comparable.sort(key=lambda item:(-item['papers'],_normalized(item['dataset']),_normalized(item['metric'])))
    return {'paper_count':len(papers),'finding_count':len(records_out),
        'comparisons':comparable,'records':records_out,'diagnostics':diagnostics,
        'year_counts':dict(sorted({year:sum(1 for y,_ in periods if y==year) for year,_ in periods}.items())),
        'generated_at':datetime.now(timezone.utc).isoformat(timespec='seconds')}


def _cell(s, max_len=150):
    return str(s or '')[:max_len].replace('|','\\|').replace('\n',' ').replace('\r',' ').replace('<','&lt;')


def to_markdown(result: dict, topic_name: str, *, imported: bool = False) -> str:
    lines=[f'# {_cell(topic_name,100)}：跨论文实验比较',
        '',f'生成时间：{result["generated_at"]}',
        f'覆盖论文：{result["paper_count"]} 篇；证据记录：{result["finding_count"]} 条；'
        f'相同条件候选组：{len(result["comparisons"])} 组。',
        '', '> 本报告仅依赖可定位的实验原文记录；同名指标与数据集不能保证实验条件相同。'
        + (' 本次证据来自用户导入，尚未由服务器核对 PDF 原文。' if imported else ' 源证据已按本地 PDF 指纹过滤，仍须人工核验。'),
        '', '## 一、研究记录按年份分布', '']
    if result['year_counts']:
        for year,count in result['year_counts'].items():
            lines.append(f'- {year}：{count} 篇（当前数据库中的证据论文数，不代表领域整体趋势）')
    else:lines.append('现有记录不足以形成时间分布。')
    lines+=['','## 二、可进一步比较的实验条件','']
    if not result['comparisons']:
        lines.append('暂无满足“同一数据集、指标、方法类型和实验条件”的跨论文候选组。')
    for i,group in enumerate(result['comparisons'],1):
        lines.extend([f'### {i}. {_cell(group["dataset"])} / {_cell(group["metric"])}',
            '', f'方法类型：{_cell(group["kind"])}；条件：{_cell(group["setting"])}；'
            f'涉及论文：{group["papers"]} 篇。','',
            '| 论文 | 原文数值 | 对照基线 | 定位 |','| --- | --- | --- | --- |'])
        for rec in group['records']:
            lines.append('| '+ ' | '.join((_cell(rec['title'],80),_cell(rec['value'],80),_cell(rec['baseline'],80),
                f'第 {rec["page"]} 页'))+' |')
        lines+=['','**复核提示：** '+'；'.join(_cell(f) for f in group.get('flags',[])),'']
    diag=result.get('diagnostics',{})
    lines+=['','### 数据完整性与可比性诊断','',
        f'未经本服务原文核验：{diag.get("unverified",0)} 条；缺少完整对比字段：{diag.get("incomplete",0)} 条；'
        f'重复记录：{diag.get("duplicates",0)} 条；缺少对照基线：{diag.get("missing_baseline",0)} 条。',
        f'同篇同条件存在不同结果值的候选：{diag.get("conflicting_values",0)} 组；'
        f'同一数据集和指标出现多种实验条件：{diag.get("incompatible_settings",0)} 组。',
        '', '以上统计仅描述当前已收录文献，不能据此推断整个研究领域的发展趋势。',
        '']
    lines+=['## 三、逐条证据与原文出处','']
    if not result['records']:
        lines.append('暂无可用的实验原文证据。')
    for n,rec in enumerate(result['records'],1):
        url=rec.get('url','')
        url_display=f'；原始链接：{url}' if url.startswith(('https://','http://')) else ''
        lines.extend([f'### [{n}] {_cell(rec["title"],180)}',
            f'数据集：{_cell(rec["dataset"])}；指标：{_cell(rec["metric"])}；'
            f'数值：{_cell(rec["value"])}；页码：{rec["page"]}{url_display}',
            '', f'> {_cell(rec["quote"],650)}',''])
    lines+=['## 四、使用边界','',
        '此报告仅用于检索与研究比较准备。数值均保留原文形式；未自动计算跨论文性能提升幅度。',
        '正式引用前，应人工核查 PDF 原文、指标定义、数据处理、统计显著性及版本差异。','']
    return '\n'.join(lines)


def collect_local(conn, topic_id: str, *, db_path) -> list[dict]:
    """Include only indexed, on-disk PDF versions whose hash is still valid."""
    from .experiments import read_findings, init_schema
    init_schema(conn)
    papers=conn.execute('''SELECT DISTINCT p.paper_id,p.title,p.doi,p.url,p.published
        FROM experimental_findings e
        JOIN topic_papers tp ON tp.paper_id=e.paper_id
        JOIN papers p ON p.paper_id=e.paper_id
        WHERE tp.topic_id=? ORDER BY p.published DESC,p.paper_id''',(topic_id,)).fetchall()
    output=[]
    for paper in papers[:1000]:
        current=read_findings(conn,paper['paper_id'],db_path=db_path)
        if current['status'] != 'ready':
            continue
        for row in current['findings']:
            output.append({**dict(paper),**row,'provenance':'pdf_sha256_verified'})
    return output
