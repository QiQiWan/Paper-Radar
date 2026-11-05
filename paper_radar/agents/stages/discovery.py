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
        papers = fetch_enabled(ctx.config,logger=ctx.logger,now=ctx.now,progress=on_progress,
                               supervised=True,checkpoint=ctx.checkpoint)
    else:
        papers = ctx.fetcher(ctx.config,logger=ctx.logger,now=ctx.now)
    from ...models import Paper
    if not isinstance(papers,list) or any(not isinstance(p,Paper) for p in papers):
        raise TypeError('检索 Agent 应返回 Paper 列表')
    return AgentResult({'retrieved':papers},metrics={'retrieved':len(papers)})
