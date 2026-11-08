# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""QualityAgent: deterministic dates, ownership exclusions and baseline score."""
from ..contracts import AgentResult


def execute(ctx):
    from ... import core
    from ...sources.retrieval_trace import current_trace
    trace=current_trace()
    papers = ctx.state['retrieved']
    if trace: trace.record('stage_count',filter_stage='source_candidates',count=len(papers))
    ctx.task.emit(45,'整理文献',detail=f'检索完成，共获取 {len(papers)} 条记录')
    filtered = [p for p in papers if core.valid_recent_publication(
        p.published,ctx.now,ctx.config['lookback_days'])]
    removed = len(papers)-len(filtered)
    if trace: trace.record('stage_count',filter_stage='date_passed',
                           count=len(filtered),removed=removed)
    if removed:
        ctx.logger(f'日期质量检查：{removed} 篇未来、过期或日期不完整的题录不进入本期结果。')
    # Merge the same DOI from different indexes before ranking. Otherwise two
    # copies of a highly ranked paper can consume two of the final N slots.
    unique={}
    for paper in filtered:
        identity=core.normalize_doi(paper.doi) or paper.paper_id.casefold().strip()
        first=unique.get(identity)
        if first is None:
            unique[identity]=paper
            continue
        first.source=','.join(dict.fromkeys(x for x in
            (first.source+','+paper.source).split(',') if x))
        first.abstract=core.richer_abstract(first.abstract,paper.abstract)
        first.title=core.richer_title(first.title,paper.title)
        if not first.pdf_url and paper.pdf_url: first.pdf_url=paper.pdf_url
        if not first.download_url and paper.download_url: first.download_url=paper.download_url
        if not first.authors and paper.authors: first.authors=paper.authors
        if not first.venue and paper.venue: first.venue=paper.venue
        first.is_oa=int(bool(first.is_oa or paper.is_oa))
    date_count=len(filtered)
    filtered=list(unique.values())
    if trace: trace.record('stage_count',filter_stage='cross_source_unique',
                           count=len(filtered),removed=date_count-len(filtered))
    ranker = ctx.state['profile_ranker']
    zotero_before=len(filtered)
    if ranker is not None:
        before = len(filtered)
        filtered = [p for p in filtered if not ranker.already_collected(p)]
        if len(filtered) < before:
            ctx.logger(f'已排除 Zotero 收藏夹中已有的 {before-len(filtered)} 篇论文。')
    if trace: trace.record('stage_count',filter_stage='zotero_passed',
                           count=len(filtered),removed=zotero_before-len(filtered))
    ctx.config['_retrieval_counts']={
        'source_candidates':len(papers),'date_passed':date_count,
        'date_removed':removed,'cross_source_unique':len(unique),
        'duplicate_removed':date_count-len(unique),
        'zotero_passed':len(filtered),'zotero_removed':zotero_before-len(filtered)}
    for index, paper in enumerate(filtered):
        if index % 100 == 0: ctx.checkpoint()
        paper.score = core.rank(paper,ctx.config['keywords'],ranker)
        if paper.score <= 0 and 'openalex_semantic' in paper.source.split(',') and paper.abstract:
            paper.score = 0.35
    return AgentResult({'curated':filtered,'removed_dates':removed},
                       metrics={'eligible':len(filtered),'removed_dates':removed})
