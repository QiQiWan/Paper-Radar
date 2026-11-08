# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Hard wall-clock deadlines for official source adapters.

The source adapter lives in a child process. A blocked DNS lookup, TLS handshake,
trickling HTTP response or library parser can be terminated without freezing the
Web UI. Only a minimal search configuration crosses the private stdin pipe;
provider credentials are never serialized into logs or results.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from json import dumps, loads
from math import isfinite
from subprocess import PIPE, Popen, TimeoutExpired
import subprocess
import sys
import os
from time import monotonic

from ..models import Paper
from .failures import SourceQueryError
from .retrieval_trace import sanitized_event

MIN_BUDGET, MAX_BUDGET = 0.2, 180.0
_SAFE_FIELDS = ('keywords', 'categories', 'lookback_days', 'max_results',
                'research_goal', 'max_queries_per_source',
                '_window_start','_window_end','_watch_arxiv_ids',
                'openreview_invitation')


class SourceBatch(list):
    """Paper records with a small, explicitly unverified source warning."""
    def __init__(self, records=(), *, partial=False, warning_code='', stats=None):
        super().__init__(records)
        self.partial = bool(partial)
        self.warning_code = warning_code if partial else ''
        stats = stats if isinstance(stats,dict) else {}
        self.stats = {k: max(0, min(int(stats.get(k,0)), 1000000))
                      for k in ('remote_hits','date_rejected','invalid_notes')
                      if type(stats.get(k)) is int}


class SourceTimeout(TimeoutError):
    """An official source did not respond within the wall-clock budget."""


def source_child_env(name: str, *, public_metadata: bool = False) -> dict | None:
    """Do not pass model/publisher secrets to untrusted public metadata tasks.

    Public OpenAlex searches use the no-key free API by default. An operator
    must explicitly opt in before sharing a hosted OpenAlex key with users.
    """
    if not public_metadata and not (os.environ.get('ZOTERO_API_KEY') or os.environ.get('ZOTERO_USER_ID')):
        return None  # no cloud Zotero credentials to remove
    env = os.environ.copy()
    env.pop('ZOTERO_API_KEY', None)
    env.pop('ZOTERO_USER_ID', None)
    if not public_metadata:
        return env
    env['PAPERRADAR_PUBLIC_METADATA_CHILD'] = '1'
    for secret in ('PAPER_RADAR_API_KEY', 'PAPERRADAR_PUBLIC_LLM_KEY',
                   'PAPER_RADAR_SPRINGER_API_KEY', 'PAPER_RADAR_ELSEVIER_API_KEY',
                   'PAPER_RADAR_ELSEVIER_INSTTOKEN'):
        env.pop(secret, None)
    if name == 'openalex' and env.get('PAPERRADAR_PUBLIC_ALLOW_OPENALEX_API_KEY') != '1':
        env.pop('OPENALEX_API_KEY', None)
    if name != 'openalex':
        env.pop('OPENALEX_API_KEY', None)
    return env


def _terminate(proc: Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.communicate(timeout=1)
        except TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=2)


def fetch_isolated(name: str, config: dict, *, now: datetime | None = None,
                   timeout_seconds: float = 12, checkpoint=None, heartbeat=None,
                   command: list[str] | None = None,
                   public_metadata: bool = False, trace_hook=None) -> list[Paper]:
    """Invoke one query with an absolute deadline and cooperative cancellation.

    `command` is injected only in local deterministic tests. The actual app
    always uses the fixed module entry point; no user-controlled shell command.
    """
    budget = float(timeout_seconds)
    if not isfinite(budget) or not MIN_BUDGET <= budget <= MAX_BUDGET:
        raise ValueError('检索超时预算必须介于 0.2 和 180 秒之间')
    payload = dumps({'name':name, 'config':{k:config[k] for k in _SAFE_FIELDS if k in config},
                     'now': now.isoformat() if now else None,
                     'wall_timeout_seconds': budget}, ensure_ascii=False).encode('utf-8')
    if len(payload) > 32_768:
        raise ValueError('检索参数过长')
    args = command or ([sys.executable,'--source-child'] if getattr(sys,'frozen',False)
                       else [sys.executable,'-m','paper_radar.sources.source_process'])
    flags=(subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    def emit(event, **kwargs):
        if trace_hook:
            try: trace_hook(sanitized_event(event, source=name, **kwargs))
            except Exception: pass
    emit('child_spawn', timeout_ms=round(budget*1000))
    try:
        proc = Popen(args, stdin=PIPE, stdout=PIPE, stderr=PIPE,creationflags=flags,
                     env=source_child_env(name,public_metadata=public_metadata))
    except OSError:
        emit('child_error', error_code='SOURCE_CHILD_START')
        raise SourceQueryError('SOURCE_CHILD_START') from None
    deadline = monotonic() + budget
    first = True
    last_heartbeat = monotonic()
    try:
        while True:
            if checkpoint:
                checkpoint()  # Agent cancellation/deadline errors propagate.
            remaining = deadline - monotonic()
            if remaining <= 0:
                emit('child_error', error_code='SOURCE_TIMEOUT', elapsed_ms=round(budget*1000))
                raise SourceTimeout(f'{name} 来源连接超过 {budget:g} 秒，已停止该来源查询')
            try:
                output, stderr = proc.communicate(input=payload if first else None,
                                                   timeout=min(.35, remaining))
                emit('child_exit', exit_code=int(proc.returncode or 0),
                     stdout_bytes=len(output), stderr_bytes=len(stderr))
                # Do not log or expose raw child stderr: a library traceback
                # may contain a URL with API credentials or search terms.
                if proc.returncode not in (0, None):
                    code = ('SOURCE_CHILD_ENCODING' if b'UnicodeEncodeError' in stderr[:65536]
                            else 'SOURCE_CHILD_FAILED')
                    emit('child_error', error_code=code, exit_code=proc.returncode)
                    raise SourceQueryError(code)
                if not output:
                    emit('child_error', error_code='SOURCE_CHILD_EMPTY', stage='parse')
                    raise SourceQueryError('SOURCE_CHILD_EMPTY')
                if len(output) > 12_000_000:
                    emit('child_error', error_code='SOURCE_CHILD_PROTOCOL', stage='parse')
                    raise SourceQueryError('SOURCE_CHILD_PROTOCOL')
                try:
                    decoded = output.decode('utf-8', errors='strict')
                except UnicodeError:
                    emit('child_error', error_code='SOURCE_CHILD_ENCODING', stage='decode')
                    raise SourceQueryError('SOURCE_CHILD_ENCODING') from None
                try:
                    message = loads(decoded)
                except ValueError:
                    emit('child_error', error_code='SOURCE_CHILD_PROTOCOL', stage='parse')
                    raise SourceQueryError('SOURCE_CHILD_PROTOCOL') from None
                if not isinstance(message, dict) or not isinstance(message.get('ok'), bool):
                    emit('child_error', error_code='SOURCE_CHILD_PROTOCOL', stage='validate')
                    raise SourceQueryError('SOURCE_CHILD_PROTOCOL')
                if message.get('protocol_version') not in (None, 2):
                    emit('child_error', error_code='SOURCE_CHILD_PROTOCOL', stage='validate')
                    raise SourceQueryError('SOURCE_CHILD_PROTOCOL')
                if isinstance(message, dict):
                    emit('child_exit', proxy_configured=bool(message.get('proxy_configured',False)))
                    for item in message.get('transport_trace', [])[:80] if isinstance(message.get('transport_trace'),list) else []:
                        if isinstance(item,dict) and item.get('event') in ('http_start','http_connected','http_read','http_error'):
                            # Never trust child-provided fields without re-applying
                            # the same strict allowlist used by disk diagnostics.
                            safe=sanitized_event(item['event'], **{k:v for k,v in item.items() if k!='event'})
                            emit(safe.pop('event'), **{k:v for k,v in safe.items() if k!='at'})
                if not message['ok']:
                    emit('child_error', error_code=str(message.get('error_code') or 'SOURCE_UNAVAILABLE'))
                    raise SourceQueryError(str(message.get('error_code') or 'SOURCE_UNAVAILABLE'))
                raw = message.get('papers')
                if not isinstance(raw, list) or len(raw) > 500 or any(
                        not isinstance(row, dict) for row in raw):
                    emit('child_error', error_code='SOURCE_CHILD_PROTOCOL', stage='validate')
                    raise SourceQueryError('SOURCE_CHILD_PROTOCOL')
                allowed = {field.name for field in fields(Paper)}
                try:
                    papers = [Paper(**{k:v for k,v in row.items() if k in allowed})
                              for row in raw]
                except (TypeError, ValueError):
                    emit('child_error', error_code='SOURCE_CHILD_PROTOCOL', stage='validate')
                    raise SourceQueryError('SOURCE_CHILD_PROTOCOL') from None
                return SourceBatch(papers, partial=message.get('partial',False),
                                   warning_code=message.get('warning_code',''),
                                   stats=message.get('stats'))
            except TimeoutExpired:
                first = False
                if heartbeat and monotonic() - last_heartbeat >= 1:
                    heartbeat(max(0, round(deadline-monotonic(), 1)))
                    last_heartbeat = monotonic()
                continue
    finally:
        _terminate(proc)
