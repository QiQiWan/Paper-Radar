# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""DiscoveryAgent: obey official source adapters and query budgets."""
from ..contracts import AgentResult


def execute(ctx):
    from ... import core
    ctx.require('network_read')
    ctx.checkpoint()
    ctx.task.emit(12,'检索文献',detail='正在连接文献来源')
    if ctx.fetcher is core.fetch_arxiv:
        from ...sources import fetch_enabled
        def on_progress(done, total, detail):
            ctx.checkpoint()
            ctx.task.emit(12 + 33*done/max(1,total),'检索文献',current=done,total=total,detail=detail)
        # Checkpoints describe successful persisted searches, not attempted requests.
        # The discovery agent only plans the query window; the storage agent
        # commits checkpoint advancement after all database writes succeed.
        from ...following import plan_windows, watched_arxiv_ids
        with core.open_database(ctx.db_path) as db:
            ctx.config['_following_windows']=plan_windows(db,ctx.config,now=ctx.now)
            if 'arxiv' in ctx.config['_following_windows']:
                ctx.config['_watch_arxiv_ids']=watched_arxiv_ids(
                    db,ctx.config.get('active_topic_id','default'))
        # The desktop source cache is scoped to the user's local data directory.
        # Cache keys include incremental windows and watched-paper IDs.
        ctx.config['_local_source_cache'] = True
        try:
            papers = fetch_enabled(ctx.config,logger=ctx.logger,now=ctx.now,progress=on_progress,
                                   supervised=True,checkpoint=ctx.checkpoint)
        except Exception as exc:
            from ...sources.base import AllSourcesUnavailable
            if isinstance(exc,AllSourcesUnavailable):
                from ...following import record_cycle
                with core.open_database(ctx.db_path) as db:
                    record_cycle(db,ctx.config,[],[],exc.source_results,now=ctx.now)
            raise

    else:
        papers = ctx.fetcher(ctx.config,logger=ctx.logger,now=ctx.now)
    from ...models import Paper
    if not isinstance(papers,list) or any(not isinstance(p,Paper) for p in papers):
        raise TypeError('检索 Agent 应返回 Paper 列表')
    return AgentResult({'retrieved':papers},metrics={'retrieved':len(papers)})
