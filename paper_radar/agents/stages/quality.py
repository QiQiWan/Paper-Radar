# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""QualityAgent: deterministic dates, ownership exclusions and baseline score."""
from ..contracts import AgentResult


def execute(ctx):
    from ... import core
    papers = ctx.state['retrieved']
    ctx.task.emit(45,'整理文献',detail=f'检索完成，共获取 {len(papers)} 条记录')
    filtered = [p for p in papers if core.valid_recent_publication(
        p.published,ctx.now,ctx.config['lookback_days'])]
    removed = len(papers)-len(filtered)
    if removed:
        ctx.logger(f'日期质量检查：{removed} 篇未来、过期或日期不完整的题录不进入本期结果。')
    ranker = ctx.state['profile_ranker']
    if ranker is not None:
        before = len(filtered)
        filtered = [p for p in filtered if not ranker.already_collected(p)]
        if len(filtered) < before:
            ctx.logger(f'已排除 Zotero 收藏夹中已有的 {before-len(filtered)} 篇论文。')
    for index, paper in enumerate(filtered):
        if index % 100 == 0: ctx.checkpoint()
        paper.score = core.rank(paper,ctx.config['keywords'],ranker)
        if paper.score <= 0 and 'openalex_semantic' in paper.source.split(',') and paper.abstract:
            paper.score = 0.35
    return AgentResult({'curated':filtered,'removed_dates':removed},
                       metrics={'eligible':len(filtered),'removed_dates':removed})
