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
    if not cfg.get("llm_data_consent", False):
        if cfg.get("ai_enabled") or cfg.get("ai_rerank_enabled"):
            logger("本次未获模型数据传输授权：关闭云端摘要与辅助排序，常规检索继续。")
        cfg = {**cfg,"ai_enabled":False,"ai_rerank_enabled":False}
    folder = core.get_output_dir(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    ctx = AgentContext(cfg, db_path, folder, fetcher, now, logger, task)
    runner = AgentRunner(build_registry())
    with core.single_instance(db_path.parent/'running.lock'):
        try:
            result = runner.run(ctx, on_finish=_public_result)
            # Optional metadata-only digest. Delivery failure never marks a
            # successful search as failed or advances a source checkpoint.
            if cfg.get('following_enabled', True) and result.get('following',{}).get('events'):
                try:
                    from ..following.delivery import send_digest
                    with core.open_database(db_path) as conn:
                        delivery=send_digest(conn,cfg.get('active_topic_id','default'))
                    result['following']['email']=delivery
                except Exception as exc:
                    logger('邮件摘要暂未发送；站内提醒已保存，可稍后重试。')
            # Local scheduled/interactive/batch searches share the same
            # idempotent, non-LLM periodic briefing mechanism. This must not
            # advance source checkpoints or turn search success into failure.
            try:
                from ..research_continuity import after_search
                report=after_search(db_path,cfg.get('active_topic_id','default'),now=now)
                if report.get('state')=='generated':
                    result['research_period_brief']={'status':'generated',
                        'id':report['result']['id']}
            except (OSError,RuntimeError,ValueError,core.sqlite3.DatabaseError) as exc:
                logger('周期研究简报生成失败：'+type(exc).__name__)
            if db_path.resolve()==core.DB_FILE.resolve():
                try:
                    from ..backups import backup_if_due
                    backup_if_due(core.APP_ROOT)
                except Exception as exc:
                    logger('自动数据备份未完成；请在研究跟进页面查看备份状态。')
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
        'retrieval_counts':ctx.config.get('_retrieval_counts',{}),
        'ai_rerank':state.get('ai_rerank',{}),
        'following':state.get('following_result',{}),
        **files,
    }
