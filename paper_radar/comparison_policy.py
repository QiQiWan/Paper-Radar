# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Conservative comparability protocol for manually reviewed PDF evidence.

A positive verdict records a *user's audited judgement*. It does not assert
reproducibility, statistical significance, or a machine-verified experiment.
No normalization here silently converts percentage to fraction or units.
"""
from __future__ import annotations
import re

CONDITIONS = ('dataset_version', 'split', 'unit', 'metric_direction',
              'aggregation', 'training_budget', 'evaluation_protocol')
DIRECTIONS = {'higher', 'lower', 'neutral'}
SPACE = re.compile(r'\s+')


def norm(value: object) -> str:
    return SPACE.sub(' ', str(value or '').casefold()).strip()


def validate_conditions(raw: object, *, require_complete: bool = False) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) - set(CONDITIONS):
        raise ValueError('实验条件必须是指定字段构成的对象')
    result: dict[str, str] = {}
    for field in CONDITIONS:
        value = raw.get(field, '')
        if not isinstance(value, str) or len(value) > 120 or any(ord(c) < 32 for c in value):
            raise ValueError('实验条件存在无效字段：' + field)
        result[field] = SPACE.sub(' ', value).strip()
    if result['metric_direction'] and result['metric_direction'].lower() not in DIRECTIONS:
        raise ValueError('metric_direction 只允许 higher/lower/neutral')
    if require_complete and (any(not value for value in result.values()) or
                             result['metric_direction'].casefold() == 'neutral'):
        raise ValueError('确认可比之前须完整记录数据版本、划分、单位、指标方向、聚合方式、训练预算和评估协议')
    return result


def _reviewed(record: dict) -> bool:
    return (record.get('review_decision') == 'approved'
            and record.get('quote_verified_against_pdf', False)
            and bool(record.get('evidence_id')))


def pair_assessment(a: dict, b: dict) -> dict:
    """Block unsafe claims; only explicit *human* reviews can support acceptance."""
    blockers: list[str] = []
    if a.get('paper_id') == b.get('paper_id'):
        blockers.append('必须来自两篇不同论文')
    for field, label in (('kind', '方法类型'), ('dataset', '数据集'), ('metric', '指标'),
                         ('setting', '实验设置'), ('baseline', '对照基线')):
        if not norm(a.get(field)) or not norm(b.get(field)) or norm(a.get(field)) != norm(b.get(field)):
            blockers.append(label + '不一致或缺失')
    if not _reviewed(a) or not _reviewed(b):
        blockers.append('双方原文证据尚未完成用户关系复核')
    conditions_a = a.get('review_conditions') or {}
    conditions_b = b.get('review_conditions') or {}
    try:
        ca = validate_conditions(conditions_a, require_complete=True)
        cb = validate_conditions(conditions_b, require_complete=True)
    except ValueError:
        blockers.append('实验条件存在缺项或无效值')
        ca = cb = {}
    for field in CONDITIONS:
        if ca and cb and norm(ca[field]) != norm(cb[field]):
            blockers.append(field + ' 不一致')
    for rec, conditions in ((a, ca), (b, cb)):
        if conditions:
            unit = norm(conditions['unit'])
            value = str(rec.get('value', ''))
            # A declared unit must not contradict the literal percentage marker.
            if '%' in value and unit not in ('%', 'percent', 'percentage', '百分比'):
                blockers.append('数值百分号与人工声明单位矛盾')
            elif '%' not in value and unit in ('%', 'percent', 'percentage', '百分比'):
                blockers.append('数值未注明百分号，声明百分比须独立核查')
    return {'eligible': not blockers, 'blockers': list(dict.fromkeys(blockers)),
            'verdict': 'ready_for_manual_pair_review' if not blockers else 'blocked',
            'meaning': '仅校验登记条件的一致性，不自动判定模型性能优劣'}


def pair_key(a_id: str, b_id: str) -> str:
    if not a_id or not b_id or a_id == b_id:
        raise ValueError('比较对象必须为两条不同证据')
    return '|'.join(sorted((a_id, b_id)))
