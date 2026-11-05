# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""SummaryAgent: optional controlled cloud abstract briefings."""
from ..contracts import AgentResult


def execute(ctx):
    from ... import core
    ctx.task.emit(58,'中文速览',detail='检查需要生成速览的论文')
    if not ctx.config['ai_enabled']:
        return AgentResult(status='skipped',metrics={'generated':0})
    ctx.require('cloud_model')
    ctx.require('database_write')
    ctx.checkpoint()
    with core.open_database(ctx.db_path) as conn:
        try:
            count=core.annotate_papers(conn,ctx.config,logger=ctx.logger,now=ctx.now,
                ranker=ctx.state['profile_ranker'],progress=lambda done,total,detail:
                ctx.task.emit(58+16*done/max(1,total),'中文速览',current=done,total=total,detail=detail))
        except RuntimeError as exc:
            ctx.logger('云端 AI 跳过：'+str(exc)[:150])
            count=0
    return AgentResult(metrics={'generated':count})
