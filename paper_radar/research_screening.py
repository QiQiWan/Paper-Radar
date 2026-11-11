# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Explainable, high-precision topical screening of bilingual research questions.

Only published metadata is inspected. Labels are reading priorities, never
scientific claims, verified findings, or estimates of method performance.
"""
from __future__ import annotations

import re

# The terms below are internal query patterns, not named academic methods.
FACETS = {
    'operator': {
        'label': '神经算子',
        'question': r'神经算子|算子学习|\bneural operator\b|\boperator learning\b|\bfno\b|\bdeeponet\b',
        'paper': r'\bneural operators?\b|\boperator learning\b|\bfourier neural operators?\b|\bFNO\b|\bDeepONet\b|\bPINO\b|\bgraph neural operators?\b',
        'weight': 5,
    },
    'mesh': {
        'label': '跨网格与分辨率',
        'question': r'网格|分辨率|离散化|\bmesh(?:es)?\b|\bgrid\b|\bresolution\b|\bdiscretization\b',
        'paper': r'\bmesh(?:es)?\b|\bgrid(?:s)?\b|\bresolution\b|\bdiscretiz(?:ation|ation-independent|ed)\b|\bunstructured\b',
        'weight': 3,
    },
    'geometry': {
        'label': '跨几何与形状',
        'question': r'几何|形状|\bgeometr(?:y|ies|ical)\b|\bshape(?:s)?\b',
        'paper': r'\bgeometr(?:y|ies|ical)\b|\bshape(?:s)?\b|\bcomplex domains?\b|\b(?:irregular|varying) domains?\b',
        'weight': 3,
    },
    'generalization': {
        'label': '泛化与外推',
        'question': r'泛化|外推|迁移|\bgenerali[sz]\w*\b|\bextrapolat\w*\b|\btransfer\b|\bout.of.distribution\b',
        'paper': r'\bgenerali[sz]\w*\b|\bextrapolat\w*\b|\btransfer\b|\bunseen\b|\bout.of.distribution\b|\bzero.shot\b',
        'weight': 3,
    },
    'comparison': {
        'label': '实验与基线',
        'question': r'实验|对比|基线|评估|\bbenchmark\b|\bcomparison\b|\bevaluation\b|\bbaseline\b',
        'paper': r'\bbenchmark(?:s)?\b|\bcompar(?:e|ison|ative|ed)\b|\bbaseline(?:s)?\b|\bevaluat\w*\b',
        'weight': 1,
    },
    'physics': {
        'label': '物理约束',
        'question': r'物理|方程|\bphysics.informed\b|\bgoverning equation\b',
        'paper': r'\bphysics.informed\b|\bphysical constraints?\b|\bgoverning equations?\b|\bPDEs?\b',
        'weight': 1,
    },
}


def _search(pattern: str, value: str) -> bool:
    return bool(re.search(pattern, value or '', flags=re.IGNORECASE))


def active_facets(question: str) -> list[str]:
    return [name for name, spec in FACETS.items() if _search(spec['question'], question)]


def screen_paper(paper: dict, selected: list[str], broad_terms: set[str]) -> dict | None:
    title = str(paper.get('title') or '')[:500]
    abstract = str(paper.get('abstract') or '')[:6000]
    matches = [name for name in selected if _search(FACETS[name]['paper'], title + ' ' + abstract)]
    # A negated abstract statement such as 'No cross-geometry test was performed'
    # should not score as an affirmative evaluation of that dimension.
    for negative in re.finditer(r'\b(?:no|without|not)\s+(.{0,70}?)\s+(?:was|were|is|are)\s+(?:not\s+)?(?:studied|evaluated|tested|reported)\b',abstract,re.I):
        affected=negative.group(1)
        matches=[name for name in matches if name=='operator' or
                 not _search(FACETS[name]['paper'],affected)]
    # Mentions framed exclusively as 'unrelated to operator learning' do not
    # establish that this paper studies neural operators.
    if 'operator' in matches:
        text=title+' '+abstract
        mentions=list(re.finditer(FACETS['operator']['paper'],text,re.I))
        if mentions and all(re.search(r'\b(?:unrelated to|not concerned with|irrelevant to)\s*$',
                            text[max(0,m.start()-40):m.start()],re.I) for m in mentions):
            matches.remove('operator')
    # A neural-operator research question must not rank a generic mesh paper as
    # an operator paper simply because its abstract says 'mesh' or 'transfer'.
    if 'operator' in selected and 'operator' not in matches:
        return None
    if selected and not matches:
        return None
    title_matches = [name for name in matches if _search(FACETS[name]['paper'], title)]
    base = sum(FACETS[name]['weight'] * (1.65 if name in title_matches else 1) for name in matches)
    bonus = 2.0 if 'operator' in matches and any(n in matches for n in ('mesh','geometry','generalization')) else 0.0
    lexical = sum(1 for term in broad_terms if len(term) >= 5 and term in (title + ' ' + abstract).casefold())
    score = round(base + bonus + min(lexical, 4) * .18, 3)
    focus = [n for n in matches if n not in ('operator', 'comparison', 'physics')]
    level = ('high' if len(focus) >= 2 else 'medium' if focus else 'related') if 'operator' in selected else (
        'high' if len(matches) >= 2 else 'medium')
    labels = [FACETS[name]['label'] for name in matches]
    return {'score': score, 'level': level, 'facets': matches, 'labels': labels,
            'reason': '题名/摘要涉及' + '、'.join(labels[:4]) if labels else '研究主题相关词匹配'}


def excerpt_near_focus(abstract: str, matches: list[str], *, max_chars=440) -> str:
    """Show a source-metadata excerpt around a subject term, not an invented summary."""
    raw = re.sub(r'\s+', ' ', str(abstract or '')).strip()
    if len(raw) <= max_chars:
        return raw
    anchors = [name for name in matches if name in ('mesh', 'geometry', 'generalization')]
    anchors += [name for name in matches if name not in anchors]
    for name in anchors:
        located = re.search(FACETS[name]['paper'], raw, flags=re.I)
        if located:
            start = max(0, located.start() - 95)
            end = min(len(raw), start + max_chars)
            return ('…' if start else '') + raw[start:end].strip() + ('…' if end < len(raw) else '')
    return raw[:max_chars] + '…'


def topic_map(candidates: list[dict], active: list[str]) -> list[dict]:
    """Topic-level reading map with grounded references, not empirical consensus."""
    notes = {
        'mesh': '检查训练与测试网格是否独立、分辨率是否变化，以及有无跨网格误差报告。',
        'geometry': '确认测试几何是否在训练中出现过，并区分同类形状插值与真正跨几何测试。',
        'generalization': '核对外推、分布外评估和泛化等用语对应的实际实验划分。',
        'comparison': '检查基线、数据划分、评价指标、重复试验和计算预算是否一致。',
        'physics': '检查使用的控制方程、物理约束和边界条件是否可比。',
        'operator': '识别神经算子架构和对应的输入、输出、几何或物理系统。',
    }
    groups = []
    for name in active:
        matched = [row for row in candidates if name in row.get('matched_facets', [])]
        if matched:
            groups.append({'facet': name, 'title': FACETS[name]['label'],
                           'paper_ids': [row['paper_id'] for row in matched[:5]],
                           'candidate_count_in_shortlist': len(matched),
                           'question_to_verify': notes[name],
                           'status': 'metadata_level_unverified'})
    return groups
