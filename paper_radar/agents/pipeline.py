# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Public v1.12-compatible entry point backed by independent task agents."""
from __future__ import annotations

from .catalog import build_registry
from .contracts import AgentContext
from .runtime import AgentRunner
from .trace import store_trace


def run_research_cycle(config, logger, *, db_path, fetcher, now=None, progress=None):
    from .. import core
    from ..progress import TaskProgress
    task = TaskProgress(progress)
    task.emit(1,'准备运行',detail='检查检索范围与报告目录')
    cfg = core.validate_config(config or core.read_config())
    folder = core.get_output_dir(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    ctx = AgentContext(cfg, db_path, folder, fetcher, now, logger, task)
    runner = AgentRunner(build_registry())
    with core.single_instance(db_path.parent/'running.lock'):
        try:
            result = runner.run(ctx, on_finish=_public_result)
            return {**result, 'agent_run':runner.last_trace}
        finally:
            try:
                store_trace(runner.last_trace, db_path.parent/'agent_traces.sqlite3')
            except Exception as exc:
                logger('Agent 运行记录未能保存：'+type(exc).__name__)


def _public_result(ctx):
    state = ctx.state
    files = state['report_files']
    return {
        'retrieved': len(state['stored']),
        'new': len(state['new_papers']),
        'selected': len(state['recent']),
        'source_results':ctx.config.get('_source_results',[]),
        'ai_rerank':state.get('ai_rerank',{}),
        **files,
    }
