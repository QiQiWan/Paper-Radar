# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Conservative detection of *provable* cross-row extraction mistakes.

Raw PDF text is not a layout-aware table. A positive text match is therefore
never promoted to a verified table-cell relationship by this module.
"""
from __future__ import annotations
import re
from .quality import normalized

# Semi-colons and newlines are row separators only when at least two numeric
# rows are visible. Do not split on commas (numbers often use decimal commas).
_ROW_BREAK = re.compile(r'\r?\n|\s*;\s*')
_NUMERIC = re.compile(r'(?<![\w.])[-+]?\d+(?:[.,]\d+)?(?:\s*%)?(?![\w.])')


def _in_row(needle: str, row: str) -> bool:
    if not needle:
        return False
    a, b = normalized(needle), normalized(row)
    if not a or a not in b:
        return False
    # Avoid partial matches such as "FNO" inside "U-FNO" or "0.1" in "0.10".
    return re.search(r'(?<![\w.])' + re.escape(a) + r'(?![\w.])', b) is not None


def attribution_assessment(quote: str, *, dataset: str = '', value: str = '',
                           baseline: str = '') -> dict:
    """Reject an obvious dataset/value or baseline/value cross-row mismatch.

    ``relation_verified`` is intentionally always false until a future parser
    provides table row/column IDs and can validate the actual cell relationship.
    A single quoted line may be prose, a flattened table, or a valid row.
    """
    rows = [row.strip() for row in _ROW_BREAK.split(str(quote or '')) if row.strip()]
    number_rows = [r for r in rows if _NUMERIC.search(r)]
    for label, name in ((dataset, 'dataset'), (baseline, 'baseline')):
        if not label or not value or len(number_rows) < 2:
            continue
        label_rows = [r for r in rows if _in_row(label, r)]
        value_rows = [r for r in rows if _in_row(value, r)]
        if label_rows and value_rows and not any(_in_row(label, r) and _in_row(value, r) for r in rows):
            return {'accepted': False, 'relation_verified': False,
                    'reason': f'{name}_value_cross_row'}
    return {'accepted': True, 'relation_verified': False,
            'reason': 'table_relationship_requires_review'}
