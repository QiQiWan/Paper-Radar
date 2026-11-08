# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Metadata-only retrieval diagnostics for local and scheduled research tasks.

Only allowlisted scalar fields can cross into a diagnostic file. Keywords,
API keys, request URLs, HTTP headers, paper titles, prompts and PDF text never
enter this component, including when an upstream adapter raises an exception.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from threading import Lock
from uuid import uuid4
import json
import os
import platform
import re
import sys
import zipfile

RUN_ID = re.compile(r'^\d{8}T\d{6}Z-[a-f0-9]{10}$')
SOURCE = re.compile(r'^[a-z][a-z0-9_]{0,47}$')
EVENTS = frozenset({
    'run_start', 'run_plan', 'run_finish', 'source_start', 'source_finish',
    'query_start', 'query_finish', 'query_error', 'child_spawn', 'child_exit',
    'http_start', 'http_connected', 'http_read', 'http_error', 'child_error',
    'stage_count',
})
FIELDS = {
    'source': str, 'stage': str, 'error_code': str, 'outcome': str,
    'host': str, 'request_index': int, 'query_index': int, 'query_total': int,
    'source_index': int, 'source_total': int, 'lookback_days': int,
    'max_results': int, 'count': int, 'elapsed_ms': int,
    'timeout_ms': int, 'http_status': int, 'exit_code': int, 'ok': bool,
    'partial': bool, 'proxy_configured': bool, 'retries': int,
    'stdout_bytes': int, 'stderr_bytes': int,
    'retry_after_seconds': int, 'rate_limit_reset_seconds': int,
    'rate_limit_remaining': float, 'rate_limit_scope': str, 'source_status': str,
    'filter_stage': str, 'removed': int, 'remote_hits': int,
    'date_rejected': int, 'invalid_notes': int,
}
KNOWN_HOSTS = frozenset({
    'export.arxiv.org', 'api.openalex.org', 'api.crossref.org',
    'api2.openreview.net', 'api.springernature.com', 'api.elsevier.com',
})
CODES = frozenset({
    'OK', 'DNS_ERROR', 'PROXY_ERROR', 'TLS_ERROR', 'NETWORK_ERROR',
    'SOURCE_TIMEOUT', 'RATE_LIMIT', 'AUTH_REQUIRED', 'BAD_QUERY',
    'REMOTE_SERVER_ERROR', 'SOURCE_UNAVAILABLE', 'SOURCE_PARTIAL',
    'SOURCE_BUDGET_EXHAUSTED', 'SOURCE_CHILD_FAILED', 'SOURCE_CHILD_PROTOCOL',
    'SOURCE_CHILD_START', 'SOURCE_CHILD_ENCODING', 'SOURCE_CHILD_EMPTY',
    'SOURCE_RESPONSE_INVALID', 'QUERY_PARTIAL',
    'LIMIT_REACHED',
})
OUTCOMES = frozenset({'success', 'failed', 'partial', 'empty', 'cancelled', 'running'})
ACTIVE_TRACE: ContextVar['RetrievalTrace | None'] = ContextVar('paper_radar_retrieval_trace', default=None)


def current_trace():
    return ACTIVE_TRACE.get()


def sanitized_event(event: str, **values) -> dict:
    """Enforce a fixed schema rather than trying to redact unpredictable text."""
    if event not in EVENTS:
        raise ValueError('未知检索诊断事件')
    item = {'event': event, 'at': datetime.now(timezone.utc).isoformat(timespec='milliseconds')}
    for key, kind in FIELDS.items():
        value = values.get(key)
        if type(value) is not kind:
            continue
        if kind is str:
            if key == 'source' and not SOURCE.fullmatch(value):
                continue
            if key == 'host' and value not in KNOWN_HOSTS:
                continue
            if key == 'error_code' and value not in CODES:
                value = 'SOURCE_UNAVAILABLE'
            if key == 'outcome' and value not in OUTCOMES:
                continue
            if key == 'rate_limit_scope' and value not in ('daily_budget','deferred','unknown'):
                continue
            if key == 'source_status' and value not in ('success','empty','limit_reached','partial_failure','failed'):
                continue
            if key == 'filter_stage' and value not in {
                'source_candidates','date_passed','cross_source_unique',
                'zotero_passed','relevance_passed','rank_limit',
                'stored','new_added','report_selected'}:
                continue
            if key == 'stage' and value not in {'connect', 'read', 'parse', 'decode', 'validate', 'spawn', 'finish'}:
                continue
        elif kind is int:
            value = max(-2147483648, min(value, 4294967295)) if key == 'exit_code' else max(0, min(value, 3600000))
        elif kind is float:
            import math
            if not math.isfinite(value) or value < 0:
                continue
            value = round(min(value, 1e9), 4)
        item[key] = value
    return item


class RetrievalTrace:
    def __init__(self, root: Path, *, run_id: str | None = None, mode: str = 'search'):
        self.root = Path(root)
        self.run_id = run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:10]
        if not RUN_ID.fullmatch(self.run_id):
            raise ValueError('无效的检索诊断编号')
        self.mode = mode if mode in ('search', 'search_all', 'scheduled') else 'search'
        self.events = []
        self.lock = Lock()
        self.write_error = False
        self.path = self.root / 'logs' / 'retrieval' / (self.run_id + '.jsonl')
        self.record('run_start')

    def record(self, event: str, **fields):
        row = sanitized_event(event, **fields)
        with self.lock:
            self.events.append(row)
            if len(self.events) > 600:
                self.events = self.events[-600:]
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Never follow a symlinked diagnostics directory or log file.
                if self.path.parent.is_symlink() or self.path.is_symlink():
                    raise OSError('unsafe diagnostics path')
                if not self.path.exists() or self.path.stat().st_size < 1_000_000:
                    with self.path.open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
            except OSError:
                self.write_error = True  # Diagnostics must not break retrieval.
        return row

    def close(self, outcome: str, *, count: int = 0):
        self.record('run_finish', outcome=outcome, count=max(0, int(count)))
        _prune(self.path.parent)


def _prune(folder: Path, keep: int = 30):
    try:
        if folder.is_symlink():
            return
        entries = sorted((p for p in folder.glob('*.jsonl') if RUN_ID.fullmatch(p.stem)
                          and not p.is_symlink()), key=lambda p: p.name, reverse=True)
        for old in entries[keep:]:
            old.unlink()
    except OSError:
        pass


def _trace_dir(root: Path) -> Path:
    folder = Path(root) / 'logs' / 'retrieval'
    if folder.is_symlink():
        raise ValueError('诊断目录不可使用符号链接')
    return folder


def latest_id(root: Path) -> str | None:
    folder = _trace_dir(root)
    if not folder.exists():
        return None
    entries = sorted((p.stem for p in folder.glob('*.jsonl')
                      if RUN_ID.fullmatch(p.stem) and not p.is_symlink()), reverse=True)
    return entries[0] if entries else None


def read_trace(root: Path, run_id: str | None = None, limit: int = 250) -> dict:
    run_id = run_id or latest_id(root)
    if not run_id or not RUN_ID.fullmatch(run_id):
        raise ValueError('暂无可用的检索诊断记录')
    path = _trace_dir(root) / (run_id + '.jsonl')
    if path.is_symlink() or not path.is_file():
        raise ValueError('检索诊断记录不存在')
    # File size is bounded by writer. Do not parse arbitrarily large files.
    if path.stat().st_size > 1_050_000:
        raise ValueError('检索诊断记录超出大小限制')
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict) or obj.get('event') not in EVENTS:
                continue
            # Re-apply the allowlist to treat the on-disk file as untrusted.
            safe = sanitized_event(obj['event'], **{k:v for k,v in obj.items() if k!='event'})
            safe['at'] = str(obj.get('at', ''))[:35] if re.fullmatch(r'[0-9T:.+Z-]{1,35}', str(obj.get('at', ''))) else ''
            rows.append(safe)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    errors = [x for x in rows if x['event'] in ('query_error', 'http_error', 'child_error')]
    sources = {}
    stages = []
    for x in rows:
        if x['event'] == 'source_finish' and x.get('source'):
            sources[x['source']] = {k: x.get(k) for k in ('source', 'ok', 'partial', 'count', 'elapsed_ms', 'error_code') if k in x}
            if x.get('source_status'):
                sources[x['source']]['status'] = x['source_status']
        if x['event'] == 'stage_count' and x.get('filter_stage'):
            stages.append({k:x[k] for k in ('filter_stage','count','removed') if k in x})
    return {'run_id': run_id, 'events': rows[-max(1,min(3000,int(limit))):],
            'event_count': len(rows), 'error_count': len(errors),
            'sources': list(sources.values()), 'stages': stages}


def export_bundle(root: Path, run_id: str | None = None) -> bytes:
    result = read_trace(root, run_id, limit=3000)
    from .. import __version__
    from urllib.request import getproxies
    manifest = {
        'schema': 'paper-radar-retrieval-v1', 'app_version': __version__,
        'run_id': result['run_id'], 'os': platform.system(),
        'python': f'{sys.version_info.major}.{sys.version_info.minor}',
        'proxy_configured': any(k.lower() in ('http','https','all') for k in getproxies()),
        'events': result['event_count'], 'errors': result['error_count'],
        'privacy': '无关键词、URL 参数、令牌、请求头、论文内容、个人路径',
    }
    memory = BytesIO()
    with zipfile.ZipFile(memory, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        bundle.writestr('retrieval.jsonl', ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in result['events']))
        bundle.writestr('sources.json', json.dumps(result['sources'], ensure_ascii=False, indent=2))
        # Keep the original three-file diagnostic format compatible with
        # existing support scripts. stage_count events live in retrieval.jsonl.
    return memory.getvalue()
