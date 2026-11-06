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
