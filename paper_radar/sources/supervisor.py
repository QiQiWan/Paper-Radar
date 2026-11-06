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

MIN_BUDGET, MAX_BUDGET = 0.2, 180.0
_SAFE_FIELDS = ('keywords', 'categories', 'lookback_days', 'max_results',
                'research_goal', 'max_queries_per_source')


class SourceTimeout(TimeoutError):
    """An official source did not respond within the wall-clock budget."""


def source_child_env(name: str, *, public_metadata: bool = False) -> dict | None:
    """Do not pass model/publisher secrets to untrusted public metadata tasks.

    Public OpenAlex searches use the no-key free API by default. An operator
    must explicitly opt in before sharing a hosted OpenAlex key with users.
    """
    if not public_metadata:
        return None  # local desktop behavior remains unchanged
    env = os.environ.copy()
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
                   public_metadata: bool = False) -> list[Paper]:
    """Invoke one query with an absolute deadline and cooperative cancellation.

    `command` is injected only in local deterministic tests. The actual app
    always uses the fixed module entry point; no user-controlled shell command.
    """
    budget = float(timeout_seconds)
    if not isfinite(budget) or not MIN_BUDGET <= budget <= MAX_BUDGET:
        raise ValueError('检索超时预算必须介于 0.2 和 180 秒之间')
    payload = dumps({'name':name, 'config':{k:config[k] for k in _SAFE_FIELDS if k in config},
                     'now': now.isoformat() if now else None}, ensure_ascii=False).encode('utf-8')
    if len(payload) > 32_768:
        raise ValueError('检索参数过长')
    args = command or ([sys.executable,'--source-child'] if getattr(sys,'frozen',False)
                       else [sys.executable,'-m','paper_radar.sources.source_process'])
    flags=(subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    proc = Popen(args, stdin=PIPE, stdout=PIPE, stderr=PIPE,creationflags=flags,
                 env=source_child_env(name,public_metadata=public_metadata))
    deadline = monotonic() + budget
    first = True
    last_heartbeat = monotonic()
    try:
        while True:
            if checkpoint:
                checkpoint()  # Agent cancellation/deadline errors propagate.
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise SourceTimeout(f'{name} 来源连接超过 {budget:g} 秒，已停止该来源查询')
            try:
                output, _ = proc.communicate(input=payload if first else None,
                                             timeout=min(.35, remaining))
                if len(output) > 5_000_000:
                    raise RuntimeError(f'{name} 来源响应体过大')
                message = loads(output.decode('utf-8'))
                if not isinstance(message, dict) or not isinstance(message.get('ok'), bool):
                    raise RuntimeError(f'{name} 来源进程返回格式无效')
                if not message['ok']:
                    raise RuntimeError(str(message.get('error') or '文献接口不可用')[:180])
                raw = message.get('papers')
                if not isinstance(raw, list) or len(raw) > 300:
                    raise RuntimeError(f'{name} 来源结果数量异常')
                allowed = {field.name for field in fields(Paper)}
                return [Paper(**{k:v for k,v in row.items() if k in allowed})
                        for row in raw if isinstance(row,dict)]
            except TimeoutExpired:
                first = False
                if heartbeat and monotonic() - last_heartbeat >= 1:
                    heartbeat(max(0, round(deadline-monotonic(), 1)))
                    last_heartbeat = monotonic()
                continue
    finally:
        _terminate(proc)
