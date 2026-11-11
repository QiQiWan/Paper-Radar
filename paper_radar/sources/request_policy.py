# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Conservative Retry-After handling for public bibliographic APIs."""
from __future__ import annotations
import math


def retry_seconds(value, *, attempt=0, minimum=.5, maximum=6.0):
    # Never sleep unboundedly because another subprocess owns the hard deadline.
    try:
        raw=float(value)
    except (TypeError,ValueError,OverflowError):
        raw=minimum*(2**min(4,attempt))
    if not math.isfinite(raw):raw=maximum
    return min(maximum,max(minimum,raw))


def request_timeout(config: dict, default: float) -> float:
    """Keep each socket request inside the enclosing subprocess deadline.

    Reserve time for Windows process startup, parsing and JSON handoff. A slow
    source should fail with a typed timeout instead of freezing the workspace.
    """
    try:
        budget = float(config.get('_source_wall_timeout_seconds', 0) or 0)
    except (TypeError, ValueError, OverflowError):
        budget = 0
    if not math.isfinite(budget) or budget <= 0:
        return float(default)
    return max(2., min(float(default), budget - 4.))


def remaining_timeout(config: dict, default: float, started: float) -> float:
    """Bound retries and pagination by the *remaining* source-child deadline.

    Call before every individual HTTP request, including retries. The child
    needs time to serialize any already successful page back to the parent.
    """
    from time import monotonic
    try:
        wall = float(config.get('_source_wall_timeout_seconds',0) or 0)
    except (TypeError,ValueError,OverflowError):
        wall = 0
    if not math.isfinite(wall) or wall<=0:
        return float(default)
    left=wall-(monotonic()-started)-2.
    if left < 2.:raise TimeoutError('source child wall budget nearly exhausted')
    return min(float(default), left)


def is_configuration_network_error(exc: BaseException) -> bool:
    """Don't retry failed name resolution, TLS trust, or a broken proxy.

    These need a network/configuration change. Retrying in a short-lived
    child only spends the next search source's budget and hides the original
    diagnostic behind a final wall timeout.
    """
    from .failures import code_for_exception
    return code_for_exception(exc) in ('DNS_ERROR', 'TLS_ERROR', 'PROXY_ERROR')
