# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Versioned, testable contracts for deterministic and model-assisted agents.

Capabilities are *application-level guards*, not an OS-level sandbox. Review
third-party agent code before importing it; dynamic plugin loading is disabled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import monotonic
from threading import Event
from typing import Any, Callable

CAPABILITIES = frozenset({
    'network_read', 'zotero_read', 'database_read', 'database_write',
    'filesystem_write', 'cloud_model',
})


class AgentCancelled(RuntimeError):
    """Raised at a cooperative cancellation checkpoint."""


class AgentDeadlineExceeded(TimeoutError):
    """Raised at a cooperative time-budget checkpoint."""


class AgentTransientError(RuntimeError):
    """Explicit signal that a read-only stage can safely be retried."""


@dataclass(frozen=True)
class AgentSpec:
    name: str
    title: str
    version: str
    purpose: str
    depends_on: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    kind: str = 'deterministic'
    failure_policy: str = 'stop'
    sends_to_cloud: bool = False
    owner_module: str = ''
    capabilities: tuple[str, ...] = ()
    max_attempts: int = 1
    retry_backoff_seconds: float = 0.0
    time_budget_seconds: float = 0.0  # cooperative; no unsafe worker termination

    def public(self) -> dict:
        return {
            'name': self.name, 'title': self.title, 'version': self.version,
            'purpose': self.purpose, 'depends_on': list(self.depends_on),
            'requires': list(self.requires), 'produces': list(self.produces),
            'kind': self.kind, 'failure_policy': self.failure_policy,
            'sends_to_cloud': self.sends_to_cloud,
            'owner_module': self.owner_module,
            'capabilities': list(self.capabilities),
            'max_attempts': self.max_attempts,
            'retry_backoff_seconds': self.retry_backoff_seconds,
            'time_budget_seconds': self.time_budget_seconds,
        }


@dataclass
class AgentResult:
    """Structured data stays in memory. Traces contain only bounded metrics."""
    data: dict[str, Any] = field(default_factory=dict)
    status: str = 'success'
    metrics: dict[str, int | float | bool] = field(default_factory=dict)


@dataclass
class AgentContext:
    config: dict
    db_path: Path
    folder: Path
    fetcher: Callable
    now: datetime | None
    logger: Callable[[str], None]
    task: Any
    state: dict[str, Any] = field(default_factory=dict)
    cancel_event: Event | None = None
    active_capabilities: frozenset[str] | None = None
    deadline_at: float | None = None

    def require(self, capability: str) -> None:
        """Check one capability when an agent crosses an application boundary."""
        if self.active_capabilities is not None and capability not in self.active_capabilities:
            raise PermissionError(f'Agent 未声明能力：{capability}')

    def checkpoint(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise AgentCancelled('用户取消了 Agent 任务')
        if self.deadline_at is not None and monotonic() > self.deadline_at:
            raise AgentDeadlineExceeded('Agent 已超出执行时间预算')


@dataclass(frozen=True)
class Agent:
    spec: AgentSpec
    execute: Callable[[AgentContext], AgentResult]
