# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Privacy-safe OpenAlex quota hints and deadline-aware 429 handling.

The provider's Retry-After and X-RateLimit headers are advisory. No raw
response text, full URL, or credential leaves this module.
"""
from __future__ import annotations
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math


def retry_after_seconds(value, *, now=None) -> int | None:
    if value is None or str(value).strip() == '':
        return None
    try:
        seconds = float(str(value).strip())
    except (ValueError, OverflowError):
        try:
            target = parsedate_to_datetime(str(value)).astimezone(timezone.utc)
            current = now or datetime.now(timezone.utc)
            seconds = (target - current).total_seconds()
        except (TypeError, ValueError, OverflowError, AttributeError):
            return None
    if not math.isfinite(seconds):
        return None
    return int(min(86400, max(0, math.ceil(seconds))))


def _number(value) -> float | None:
    try:
        result = float(value)
        return round(result, 4) if math.isfinite(result) and 0 <= result <= 1e9 else None
    except (TypeError, ValueError, OverflowError):
        return None


def safe_quota_headers(headers, *, status: int | None = None) -> dict:
    if headers is None:
        return {}
    remaining = _number(headers.get('X-RateLimit-Remaining'))
    reset = _number(headers.get('X-RateLimit-Reset'))
    retry = retry_after_seconds(headers.get('Retry-After'))
    output = {}
    if remaining is not None:
        output['rate_limit_remaining'] = remaining
    if reset is not None:
        output['rate_limit_reset_seconds'] = int(reset)
    if retry is not None:
        output['retry_after_seconds'] = retry
    if status == 429:
        # Exact cause cannot be inferred from status alone. Only classify a
        # depleted advertised budget or a long server-directed wait.
        output['rate_limit_scope'] = ('daily_budget' if remaining == 0 else
            'deferred' if retry is not None and retry >= 30 else 'unknown')
    return output


def backoff_for_429(headers, *, attempt: int, remaining_seconds: float,
                     semantic: bool = False) -> float | None:
    """Return seconds to sleep once or None when further retries are futile.

    Never ignore a long Retry-After by replacing it with a short retry.
    Leave at least 3s for response processing and IPC to the parent.
    """
    if attempt >= 1 or remaining_seconds <= 4:
        return None
    quota = safe_quota_headers(headers, status=429)
    if quota.get('rate_limit_scope') == 'daily_budget':
        return None
    raw = quota.get('retry_after_seconds')
    delay = max(1 if semantic else 0.8, float(raw) if raw is not None else 1.5)
    if delay + 3 >= remaining_seconds:
        return None
    return delay
