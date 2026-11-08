# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""StorageAgent: the only stage writing canonical paper and topic metadata."""
from ..contracts import AgentResult


def execute(ctx):
    from ... import core
    from ...sources.retrieval_trace import current_trace
    trace=current_trace()
    ctx.require('database_write')
    ctx.checkpoint()
    papers = ctx.state.get('ranked')
    if papers is None:  # Optional RankingAgent may have failed.
        papers = [p for p in ctx.state['curated'] if not ctx.config['keywords'] or p.score>0]
        papers.sort(key=lambda p:(p.score,p.published),reverse=True)
        papers = papers[:ctx.config['max_results']]
    with core.open_database(ctx.db_path) as conn:
        topic=ctx.config.get('active_topic_id','default')
        old_ids={row[0] for row in conn.execute(
            'SELECT paper_id FROM topic_papers WHERE topic_id=?',(topic,))}
        new = core.save_papers(conn,papers,topic_id=topic,commit=False)
        # The idempotent event/outbox write follows successful paper storage.
        # No checkpoint update occurs if a source query or this stage fails.
        if ctx.config.get('_source_results') and ctx.config.get('_following_windows'):
            from ...following import record_cycle
            result=record_cycle(conn,ctx.config,ctx.state['retrieved'],papers,
                ctx.config['_source_results'],new_to_topic={p.paper_id for p in papers
                    if p.paper_id not in old_ids},now=ctx.now)
            ctx.state['following_result']=result
    ctx.task.emit(55,'整理文献',detail=f'写入本地文献库；新增 {len(new)} 篇')
    ctx.config.setdefault('_retrieval_counts',{}).update({'stored':len(papers),'new_added':len(new)})
    if trace:
        trace.record('stage_count',filter_stage='stored',count=len(papers))
        trace.record('stage_count',filter_stage='new_added',count=len(new))
    new.sort(key=lambda x:(x.score,x.published),reverse=True)
    return AgentResult({'stored':papers,'new_papers':new},metrics={'stored':len(papers),'new':len(new)})
