# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Ordered DAG orchestration, explicit safe retries, checkpoints and traces.

A cooperative time budget does not interrupt blocking third-party libraries;
network calls must retain their own connect/read timeouts. In particular, do
NOT retry agents that may already have written data or billed a cloud request.
"""
from __future__ import annotations

from datetime import datetime, timezone
from time import monotonic, sleep
from uuid import uuid4
import errno
import math
import re
from urllib.error import URLError
from typing import Iterable, Mapping

from .contracts import (Agent, AgentContext, AgentResult, AgentCancelled,
                        AgentDeadlineExceeded, AgentTransientError, CAPABILITIES)
from ..provider_usage import capture_agent_usage

NAME = re.compile(r'^[a-z][a-z0-9_]{1,48}$')
SIDE_EFFECT_CAPABILITIES = frozenset({'database_write', 'filesystem_write', 'cloud_model'})
TRANSIENT_ERRNOS = frozenset({errno.EAGAIN, errno.ETIMEDOUT,
                              errno.ECONNRESET, errno.ECONNABORTED,
                              errno.ECONNREFUSED, errno.ENETUNREACH})


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (AgentTransientError, ConnectionError, TimeoutError, URLError)):
        return True
    return isinstance(exc, OSError) and getattr(exc, 'errno', None) in TRANSIENT_ERRNOS


def _safe_metrics(metrics: dict) -> dict:
    return {k: v for k, v in metrics.items() if NAME.fullmatch(k)
            and type(v) in (int, float, bool)
            and (type(v) is not float or math.isfinite(v))}


class AgentRegistry:
    def __init__(self, agents: Iterable[Agent] = ()):
        self._agents: dict[str, Agent] = {}
        for agent in agents:
            self.register(agent)

    def register(self, agent: Agent) -> None:
        spec = agent.spec
        if (not NAME.fullmatch(spec.name) or spec.name in self._agents or
                spec.kind not in ('deterministic', 'llm_assisted', 'mixed') or
                spec.failure_policy not in ('stop', 'continue')):
            raise ValueError(f'Agent 配置无效或重复：{spec.name}')
        if any(not NAME.fullmatch(key) for key in (*spec.requires, *spec.produces)):
            raise ValueError(f'Agent 输入输出字段无效：{spec.name}')
        if (any(c not in CAPABILITIES for c in spec.capabilities) or
                len(set(spec.capabilities)) != len(spec.capabilities) or
                (spec.sends_to_cloud and 'cloud_model' not in spec.capabilities)):
            raise ValueError(f'Agent 权限声明无效：{spec.name}')
        if (type(spec.max_attempts) is not int or not 1 <= spec.max_attempts <= 3 or
                type(spec.retry_backoff_seconds) not in (int, float) or
                not math.isfinite(spec.retry_backoff_seconds) or
                not 0 <= spec.retry_backoff_seconds <= 3 or
                type(spec.time_budget_seconds) not in (int, float) or
                not math.isfinite(spec.time_budget_seconds) or
                not 0 <= spec.time_budget_seconds <= 7200):
            raise ValueError(f'Agent 执行预算无效：{spec.name}')
        if spec.max_attempts > 1 and SIDE_EFFECT_CAPABILITIES.intersection(spec.capabilities):
            raise ValueError(f'Agent {spec.name} 有外部副作用，禁止自动重试')
        self._agents[spec.name] = agent

    def plan(self) -> list[Agent]:
        for name, agent in self._agents.items():
            if any(dep not in self._agents for dep in agent.spec.depends_on):
                raise ValueError(f'Agent {name} 缺少依赖')
        output_owner = {}
        for agent in self._agents.values():
            for key in agent.spec.produces:
                if key in output_owner:
                    raise ValueError(f'Agent 输出字段冲突：{key}')
                output_owner[key] = agent.spec.name
        for agent in self._agents.values():
            for key in agent.spec.requires:
                owner = output_owner.get(key)
                if not owner or owner == agent.spec.name:
                    raise ValueError(f'Agent {agent.spec.name} 输入未声明生产者：{key}')
                if owner not in agent.spec.depends_on and owner not in self._ancestors(agent.spec.name):
                    raise ValueError(f'Agent {agent.spec.name} 未依赖输入生产者 {owner}')
        pending = dict(self._agents)
        result = []
        while pending:
            ready = next((name for name, agent in pending.items()
                          if all(dep not in pending for dep in agent.spec.depends_on)), None)
            if ready is None:
                raise ValueError('Agent 依赖存在循环')
            result.append(pending.pop(ready))
        return result

    def _ancestors(self, name: str) -> set[str]:
        visited = set()
        todo = list(self._agents[name].spec.depends_on)
        while todo:
            dep = todo.pop()
            if dep not in visited and dep in self._agents:
                visited.add(dep)
                todo.extend(self._agents[dep].spec.depends_on)
        return visited

    def manifest(self) -> list[dict]:
        return [item.spec.public() for item in self.plan()]

    def get(self, name: str) -> Agent:
        return self._agents[name]


class AgentRunner:
    """Sequential runner with private metadata, failure isolation and safe retries.

    `faults` is for local deterministic tests only and is not exposed by the
    web service or configuration. It injects faults before agent execution.
    """
    def __init__(self, registry: AgentRegistry):
        self.registry = registry
        self.last_trace: dict = {}

    def run(self, context: AgentContext, *, on_finish=None,
            faults: Mapping[str, list[BaseException]] | None = None) -> dict:
        agents = self.registry.plan()
        trace = {
            'run_id': uuid4().hex,
            'topic_id': str(context.config.get('active_topic_id', 'default'))[:48],
            'started_at': datetime.now(timezone.utc).isoformat(),
            'state': 'running', 'steps': [],
            'trace_version': 2,
        }
        self.last_trace = trace
        statuses = {}
        fault_plan = {name: list(errors) for name, errors in (faults or {}).items()}
        original_capabilities, original_deadline = context.active_capabilities, context.deadline_at
        try:
            for agent in agents:
                spec = agent.spec
                if any(statuses.get(dep) in ('failed', 'skipped_dependency') for dep in spec.depends_on):
                    statuses[spec.name] = 'skipped_dependency'
                    trace['steps'].append({'agent': spec.name, 'version': spec.version,
                                           'state': 'skipped_dependency', 'elapsed_ms': 0,
                                           'attempts': 0, 'metrics': {}})
                    continue
                absent = [name for name in spec.requires if name not in context.state]
                if absent:
                    raise RuntimeError(f'Agent {spec.name} 上游输出缺失：{absent}')
                started = monotonic()
                status, metrics, error_type = 'success', {}, ''
                attempts = 0
                over_budget = False
                context.active_capabilities = frozenset(spec.capabilities)
                context.deadline_at = (started + spec.time_budget_seconds
                                       if spec.time_budget_seconds else None)
                with capture_agent_usage(spec.name) as usage:
                    try:
                        for attempt in range(1, spec.max_attempts + 1):
                            attempts = attempt
                            context.checkpoint()
                            # No retry of state-mutating stages. Read-only retries
                            # restore the config reference to avoid stale previews.
                            config_before = dict(context.config)
                            try:
                                injected = fault_plan.get(spec.name, [])
                                if injected:
                                    raise injected.pop(0)
                                result = agent.execute(context)
                                context.checkpoint()
                                if not isinstance(result, AgentResult) or result.status not in ('success', 'skipped'):
                                    raise TypeError(f'Agent {spec.name} 输出类型无效')
                                missing = [key for key in spec.produces if key not in result.data]
                                if missing and result.status == 'success':
                                    raise ValueError(f'Agent {spec.name} 缺少输出：{missing}')
                                if any(key in context.state for key in result.data):
                                    raise ValueError(f'Agent {spec.name} 尝试覆盖上游输出')
                                if any(key not in spec.produces for key in result.data):
                                    raise ValueError(f'Agent {spec.name} 返回未声明的字段')
                                context.state.update(result.data)
                                status = result.status
                                metrics = _safe_metrics(result.metrics)
                                break
                            except Exception as exc:
                                can_retry = (_is_transient(exc) and not isinstance(exc, (AgentCancelled, AgentDeadlineExceeded))
                                             and attempt < spec.max_attempts)
                                if not can_retry:
                                    raise
                                context.config = config_before
                                context.logger(f'[{spec.title}] 读取阶段临时失败，进行第 {attempt+1} 次尝试：{type(exc).__name__}')
                                # Short bounded backoff; checkpoints permit cancellation.
                                if spec.retry_backoff_seconds:
                                    sleep(min(spec.retry_backoff_seconds * attempt, 3))
                                    context.checkpoint()
                    except Exception as exc:
                        status = 'failed'
                        error_type = type(exc).__name__  # never persist exception text/credentials
                        if spec.failure_policy == 'continue' and not isinstance(exc, AgentCancelled):
                            context.logger(f'[{spec.title}] 失败，继续执行其他 Agent：{type(exc).__name__}')
                        else:
                            raise
                    finally:
                        elapsed_ms = int((monotonic() - started)*1000)
                        over_budget = bool(spec.time_budget_seconds and elapsed_ms > spec.time_budget_seconds * 1000)
                        statuses[spec.name] = status
                        step = {'agent': spec.name, 'version': spec.version,
                                'state': status, 'elapsed_ms': elapsed_ms,
                                'attempts': attempts, 'metrics': metrics}
                        if over_budget:
                            step['budget_exceeded'] = True
                        if usage['requests']:
                            step['api_usage'] = {'requests': usage['requests'],
                                                 'input_tokens': usage['input_tokens'],
                                                 'output_tokens': usage['output_tokens']}
                            in_price = float(context.config.get('llm_input_usd_per_million', 0) or 0)
                            out_price = float(context.config.get('llm_output_usd_per_million', 0) or 0)
                            if in_price or out_price:
                                step['estimated_cost_usd'] = round(
                                    (usage['input_tokens']*in_price + usage['output_tokens']*out_price)/1_000_000, 6)
                        if error_type:
                            step['error_type'] = error_type
                        trace['steps'].append(step)
                        context.active_capabilities = original_capabilities
                        context.deadline_at = original_deadline
            output = on_finish(context) if on_finish else context.state
            trace['state'] = ('partial' if any(s['state']=='failed' for s in trace['steps']) else 'success')
            return output
        except AgentCancelled:
            trace['state'] = 'cancelled'
            raise
        except Exception:
            trace['state'] = 'failed'
            raise
        finally:
            context.active_capabilities = original_capabilities
            context.deadline_at = original_deadline
            trace['finished_at'] = datetime.now(timezone.utc).isoformat()
            if any('estimated_cost_usd' in s for s in trace['steps']):
                trace['estimated_cost_usd'] = round(sum(
                    s.get('estimated_cost_usd', 0.0) for s in trace['steps']), 6)
            trace['api_usage'] = {
                key: sum(s.get('api_usage', {}).get(key, 0) for s in trace['steps'])
                for key in ('requests', 'input_tokens', 'output_tokens')
            }
