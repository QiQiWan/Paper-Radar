# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""RankingAgent: optional evidenced model rerank and deterministic fallback."""
from ..contracts import AgentResult


def execute(ctx):
    from ... import core
    from ...sources.retrieval_trace import current_trace
    trace=current_trace()
    ctx.require('database_read')
    ctx.checkpoint()
    papers = ctx.state['curated']
    diagnostics = {'enabled':bool(ctx.config.get('ai_rerank_enabled')),'analyzed':0,'cached':0,'invalid':0,'failed_batches':0}
    if ctx.config.get('ai_rerank_enabled') and papers:
        ctx.require('cloud_model')
        from ...recommend import rerank_papers
        try:
            with core.open_database(ctx.db_path) as db:
                diagnostics = rerank_papers(papers,ctx.config,db,ctx.logger)
        except Exception as exc:
            ctx.logger('AI 推荐排序已降级为关键词排序：'+type(exc).__name__)
            diagnostics['failed_batches'] += 1
    # Explicit user feedback can gently reprioritize otherwise relevant
    # papers. It cannot make unrelated papers pass the relevance filter.
    interest_adjusted=0
    try:
        from ...research_continuity import interests, tokens
        with core.open_database(ctx.db_path) as db:
            preference=interests(db,ctx.config.get('active_topic_id','default'))
        weights=preference['weights']
        if weights:
            for paper in papers:
                if paper.score<=0:
                    continue
                signal=sum(weights.get(term,0) for term in tokens(paper.title))
                boost=max(-1.5,min(1.5,signal*0.20))
                if boost:
                    paper.score=max(0.01,round(paper.score+boost,4))
                    interest_adjusted+=1
    except (ValueError,OSError,RuntimeError) as exc:
        ctx.logger('兴趣偏好暂未应用：'+type(exc).__name__)
    before = len(papers)
    if ctx.config['keywords']:
        papers = [p for p in papers if p.score > 0]
        if len(papers)<before:
            ctx.logger(f'研究相关性检查：{before-len(papers)} 篇题录已排除。')
    if trace: trace.record('stage_count',filter_stage='relevance_passed',
                           count=len(papers),removed=before-len(papers))
    ctx.config['_quality_filters'] = {
        'date_removed':ctx.state['removed_dates'],
        'topic_removed':len(ctx.state['retrieved'])-ctx.state['removed_dates']-len(papers)}
    papers.sort(key=lambda p:(p.score,p.published),reverse=True)
    ranked_count=len(papers)
    if len(papers)>ctx.config['max_results']:
        ctx.logger(f"综合相关度排序后保留前 {ctx.config['max_results']} 篇，其他结果不写入本次论文库。")
        papers = papers[:ctx.config['max_results']]
    if trace: trace.record('stage_count',filter_stage='rank_limit',
                           count=len(papers),removed=ranked_count-len(papers))
    ctx.config.setdefault('_retrieval_counts',{}).update({
        'relevance_passed':ranked_count,'relevance_removed':before-ranked_count,
        'rank_limit':len(papers),'rank_limit_removed':ranked_count-len(papers)})
    ctx.config['_ai_rerank_results'] = diagnostics
    ctx.config.setdefault('_retrieval_counts',{})['interest_adjusted']=interest_adjusted
    return AgentResult({'ranked':papers,'ai_rerank':diagnostics},
                       metrics={'selected':len(papers),'model_analyzed':int(diagnostics.get('analyzed',0))})
