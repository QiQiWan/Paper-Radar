# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""SelectionAgent: topic-scoped report queue and valid fulltext evidence."""
from ..contracts import AgentResult


def execute(ctx):
    from ... import core
    ctx.require('database_read')
    ctx.checkpoint()
    with core.open_database(ctx.db_path) as conn:
        recent=core.get_recent(conn,days=ctx.config['lookback_days'],limit=ctx.config['report_limit'],
            keywords=ctx.config['keywords'],now=ctx.now,sort_mode=ctx.config.get('sort_mode','relevance'),
            ranker=ctx.state['profile_ranker'],topic_id=ctx.config.get('active_topic_id','default'),
            rerank_enabled=ctx.config.get('ai_rerank_enabled',False))
        new=ctx.state['new_papers']
        incoming=core.get_selected(conn,[p.paper_id for p in new[:ctx.config['report_limit']]],
            topic_id=ctx.config.get('active_topic_id','default')) if new else []
        incoming.sort(key=lambda x:(x.score,x.published),reverse=True)
        from ...fulltext import load_report_cards
        ctx.config['_fulltext_cards']=load_report_cards(conn,
            [p.paper_id for p in recent]+[p.paper_id for p in incoming],db_path=ctx.db_path)
    ctx.task.emit(87,'整理研究报告',detail='正在生成 Word、Markdown 和 CSV')
    return AgentResult({'recent':recent,'new_selected':incoming},
                       metrics={'recent':len(recent),'new_selected':len(incoming)})
