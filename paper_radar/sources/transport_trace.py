# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Observe urllib's connection and response-read stages inside a source child.

This patch only exists in the short-lived isolated source child. The adapter's
request and response objects are not serialized, and no URLs are stored.
"""
from __future__ import annotations

from contextlib import contextmanager
from time import monotonic
from urllib import request
from urllib.parse import urlsplit
from .failures import code_for_exception
from .retrieval_trace import KNOWN_HOSTS, sanitized_event
from .rate_limit import safe_quota_headers


class _Response:
    def __init__(self, response, events: list, *, host: str, index: int):
        self._response, self._events = response, events
        self._host, self._index = host, index

    def __enter__(self):
        self._response.__enter__()
        return self

    def __exit__(self, *args):
        return self._response.__exit__(*args)

    def read(self, *args, **kwargs):
        started = monotonic()
        try:
            data = self._response.read(*args, **kwargs)
            self._events.append(sanitized_event('http_read', host=self._host,
                request_index=self._index, elapsed_ms=round((monotonic()-started)*1000), ok=True))
            return data
        except Exception as exc:
            self._events.append(sanitized_event('http_error', stage='read',
                host=self._host, request_index=self._index,
                error_code=code_for_exception(exc), elapsed_ms=round((monotonic()-started)*1000)))
            raise

    def __getattr__(self, name):
        return getattr(self._response, name)


@contextmanager
def observe_urllib(events: list):
    original = request.urlopen
    counter = 0

    def wrapped(req, *args, **kwargs):
        nonlocal counter
        counter += 1
        request_index = counter
        raw_url = req.full_url if isinstance(req, request.Request) else str(req)
        hostname = (urlsplit(raw_url).hostname or '').lower()
        host = hostname if hostname in KNOWN_HOSTS else ''
        timeout = kwargs.get('timeout', args[0] if args else None)
        started = monotonic()
        timeout_ms = round(float(timeout)*1000) if type(timeout) in (int,float) else 0
        if len(events) < 80:
            events.append(sanitized_event('http_start', host=host, request_index=request_index,
                                          timeout_ms=max(0,timeout_ms)))
        try:
            response = original(req, *args, **kwargs)
            if len(events) < 80:
                events.append(sanitized_event('http_connected', host=host,
                    request_index=request_index,
                    http_status=int(getattr(response, 'status', 200) or 200),
                    **(safe_quota_headers(getattr(response, 'headers', None)) if host == 'api.openalex.org' else {}),
                    elapsed_ms=round((monotonic()-started)*1000)))
            return _Response(response, events, host=host, index=request_index)
        except Exception as exc:
            if len(events) < 80:
                status = getattr(exc, 'code', None)
                events.append(sanitized_event('http_error', host=host, request_index=request_index,
                    stage='connect', error_code=code_for_exception(exc),
                    http_status=status if type(status) is int else 0,
                    **(safe_quota_headers(getattr(exc,'headers',None),status=status) if host == 'api.openalex.org' else {}),
                    elapsed_ms=round((monotonic()-started)*1000)))
            raise

    request.urlopen = wrapped
    try:
        yield
    finally:
        request.urlopen = original
