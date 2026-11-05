# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""StorageAgent: the only stage writing canonical paper and topic metadata."""
from ..contracts import AgentResult


def execute(ctx):
    from ... import core
    ctx.require('database_write')
    ctx.checkpoint()
    papers = ctx.state.get('ranked')
    if papers is None:  # Optional RankingAgent may have failed.
        papers = [p for p in ctx.state['curated'] if not ctx.config['keywords'] or p.score>0]
        papers.sort(key=lambda p:(p.score,p.published),reverse=True)
        papers = papers[:ctx.config['max_results']]
    with core.open_database(ctx.db_path) as conn:
        new = core.save_papers(conn,papers,topic_id=ctx.config.get('active_topic_id','default'))
    ctx.task.emit(55,'整理文献',detail=f'写入本地文献库；新增 {len(new)} 篇')
    new.sort(key=lambda x:(x.score,x.published),reverse=True)
    return AgentResult({'stored':papers,'new_papers':new},metrics={'stored':len(papers),'new':len(new)})
