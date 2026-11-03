# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Evidence-constrained summary of this report's actual selection."""
from __future__ import annotations
from .evidence import overview_counts, result_evidence, _short
from ..quality import is_broad, matched_keywords


def describe_research(papers, config: dict) -> str:
    """A short, source-grounded introduction, reproducible without model APIs."""
    papers = list(papers)
    keywords = [str(k).strip() for k in config.get('keywords', []) if str(k).strip() and not is_broad(k)]
    days = int(config.get('lookback_days', 7))
    if not papers:
        return (f'本次按所选研究方向检索最近 {days} 天的论文，暂未找到符合筛选条件的记录。'
                '请检查检索词、文献来源或日期范围；后续有结果时将在此处自动生成研究综述。')
    used_terms = [k for k in keywords if any(matched_keywords(p.title, p.abstract, [k]) for p in papers)]
    theme = '、'.join(f'“{_short(k,40)}”' for k in used_terms[:4]) or ('、'.join(keywords[:3]) or '当前研究方向')
    with_abstract = sum(bool(str(p.abstract or '').strip()) for p in papers)
    evidence_count = sum(bool(result_evidence(p.abstract)) for p in papers)
    topics, methods, issues = overview_counts(papers)
    subject = '、'.join(t for t,_ in topics.most_common(3))
    approach = '、'.join(m for m,_ in methods.most_common(3))
    lines = [f'研究范围：本期围绕{theme}收录 {len(papers)} 篇论文；其中 {with_abstract} 篇附有摘要，'
             f'{len(papers)-with_abstract} 篇目前只有题录。']
    lines.append('研究内容：' + (f'题名与摘要主要覆盖{subject}。' if subject else '当前题录主题较为分散，建议逐篇核查相关性。') +
                 (f'已有文献中可识别的研究方法包括{approach}。' if approach else '若题名或摘要未明确方法，报告不会补写具体算法。'))
    lines.append(f'结果依据：{evidence_count} 篇摘要含有明确的结果性表述，可在下方查看原始英文句子；'
                 '其他论文尚不足以仅凭现有信息判断方法效果。')
    concern = '、'.join(i for i,_ in issues.most_common(2))
    lines.append(('待核查问题：' + (f'部分摘要提及{concern}；' if concern else '') +
                  '需要结合原文核对工况条件、数据规模、对照实验和实际适用范围。'
                  '以下先列出优先阅读论文，再给出研究对象、方法线索、摘要证据与原文链接。'))
    return '\n'.join(lines)
