# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Metadata-only reading notes with explicit evidence boundaries.

All scientific claims require source-abstract wording; never invent a score,
result or limitation because an abstract contains a technical buzzword.
"""
from __future__ import annotations
from collections import Counter
import re
from ..quality import matches, matched_keywords, is_broad

DOMAINS = (
    (('embankment', 'soft clay', 'ground improvement'), '路堤与软土地基'),
    (('slope stability', 'soil-rock', 'slope', 'landslide'), '边坡与土岩体稳定性'),
    (('railway subgrade', 'railway', 'subgrade', 'ballast'), '铁路路基及沉降'),
    (('tunnel boring', 'tbm', 'tunneling', 'tunnelling', 'excavation'), '隧道开挖与地层响应'),
    (('hypoplasticity', 'constitutive law', 'constitutive model', 'soil constitutive'), '土体本构与参数识别'),
    (('neural operator', 'operator learning', 'fourier neural operator'), '神经算子与偏微分方程学习'),
    (('thermal management', 'battery cooling', 'heat transfer'), '热管理与传热'),
    (('sediment yield', 'soil erosion', 'runoff'), '地表侵蚀与水土流失'),
    (('foundation pit', 'retaining wall', 'support excavation'), '基坑与支挡结构'),
    (('surrogate model', 'surrogate modeling', 'reduced order'), '代理模型与降阶模拟'),
)
METHODS = (
    (('finite element', 'finite-element', 'finite element method', 'plane-strain'), '有限元或平面应变计算'),
    (('response surface', 'dimensionality reduction'), '降维与响应面方法'),
    (('in situ monitoring', 'field monitoring', 'field study', 'field measurement'), '现场观测与实测分析'),
    (('reliability analysis', 'reliability assessment'), '可靠度分析'),
    (('neural operator', 'operator learning'), '神经算子学习'),
    (('physics-informed', 'physics informed', 'pinn'), '物理信息神经网络'),
    (('surrogate model', 'surrogate modeling', 'surrogate learning'), '代理建模'),
    (('parametric study', 'sensitivity analysis'), '参数分析'),
    (('experimental investigation', 'laboratory experiments', 'laboratory testing'), '试验研究'),
    (('numerical model', 'numerical simulation', 'finite difference'), '数值模拟'),
)
ISSUES = (
    (('generaliz', 'transferability', 'transfer across', 'out-of-distribution', 'domain shift'), '跨场景泛化能力'),
    (('computational cost', 'scalability', 'compute efficiency', 'computational efficiency'), '规模扩展与计算成本'),
    (('uncertainty', 'robustness', 'reliability'), '不确定性与稳健性'),
    (('sparse data', 'limited data', 'data scarcity'), '有限数据条件'),
    (('geometry', 'mesh', 'discretization'), '几何与网格适应性'),
)
RESULT_CUE = re.compile(
    r'\b(?:results?|findings?)\s+(?:show|shows|indicate|indicates|suggest|suggests|demonstrate|demonstrates|reveal|reveals|confirm|confirms)\b'
    r'|\bwe\s+(?:find|found|show|demonstrate|observe)\b'
    r'|\b(?:was|were)\s+found\s+to\b'
    r'|\b(?:the\s+study|experiments?)\s+(?:shows?|demonstrates?|reveals?)\b',
    re.IGNORECASE,
)


def domain_labels(text: str) -> list[str]:
    return [label for cues, label in DOMAINS if any(matches(text, cue) for cue in cues)]


def method_labels(text: str) -> list[str]:
    return [label for cues, label in METHODS if any(matches(text, cue) for cue in cues)]


def _short(text: str, max_chars: int = 190) -> str:
    value = re.sub(r'\s+', ' ', text or '').strip()
    return value if len(value) <= max_chars else value[:max_chars].rstrip(' ,;') + '…'


def result_evidence(abstract: str) -> str:
    """Return a directly inspectable original sentence or empty string.

    Deliberately refuses vague 'yield'/'achieve' substring matches because
    e.g. 'sediment yield' is a research variable, not an achieved result.
    """
    if not abstract or len(abstract.strip()) < 40:
        return ''
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z(\u201c])', re.sub(r'\s+', ' ', abstract.strip()))
    for sentence in sentences:
        if len(sentence) >= 35 and RESULT_CUE.search(sentence):
            return _short(sentence, 210)
    return ''


def paper_brief(paper, keywords: list[str]) -> dict[str, str]:
    title = str(paper.title or '').strip()
    abstract = str(paper.abstract or '').strip()
    body = title + ' ' + abstract
    fields = domain_labels(body)
    methods = method_labels(body)
    terms = matched_keywords(title, abstract, keywords)
    evidence = result_evidence(abstract)
    return {
        'topic': '、'.join(fields[:2]) if fields else ('匹配：' + '、'.join(terms[:3]) if terms else '题录指向的研究问题'),
        'method': '、'.join(methods[:3]) if methods else ('题名未明确方法，需查看原文' if not abstract else '摘要未提供可直接识别的方法名称'),
        'evidence': evidence,
        'status': '有摘要' if abstract else '仅题录：缺少摘要，暂不能判断研究方法和结论',
        'review': ('结合原文核对模型假设、试验/数值条件、基线对照及适用范围。'
                   if abstract else '先到 DOI 或论文主页核对摘要与正式发表信息。'),
    }


def _issue_matches(text: str, cue: str) -> bool:
    if cue == 'generaliz':
        return bool(re.search(r'\bgeneraliz[a-z]*\b', text.casefold()))
    return matches(text, cue)


def overview_counts(papers):
    methods = Counter(label for p in papers for label in method_labels((p.title or '') + ' ' + (p.abstract or '')))
    topics = Counter(label for p in papers for label in domain_labels((p.title or '') + ' ' + (p.abstract or '')))
    issues = Counter(label for p in papers for cues, label in ISSUES
                     if any(_issue_matches((p.title or '') + ' ' + (p.abstract or ''), cue) for cue in cues))
    return topics, methods, issues
