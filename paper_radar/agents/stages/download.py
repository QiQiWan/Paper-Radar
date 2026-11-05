# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""DownloadAgent: authorized OA links, bounded queue and PDF validation."""
from ..contracts import AgentResult


def execute(ctx):
    from ... import core
    ctx.task.emit(75,'PDF 下载',detail='检查自动下载设置')
    if not ctx.config.get('auto_download'):
        return AgentResult(status='skipped')
    for cap in ('network_read', 'database_write', 'filesystem_write'):
        ctx.require(cap)
    ctx.checkpoint()
    from ...downloads.manager import download_selected
    papers=ctx.state['stored']
    ids=[p.paper_id for p in sorted(papers,key=lambda x:x.score,reverse=True)
         if p.download_url][:ctx.config['download_limit']]
    if not ids:
        return AgentResult(status='skipped')
    with core.open_database(ctx.db_path) as conn:
        results=download_selected(conn,ids,ctx.config,logger=ctx.logger,db_path=ctx.db_path,
            progress=lambda done,total,detail:ctx.task.emit(75+11*done/max(1,total),
                'PDF 下载',current=done,total=total,detail=detail))
    successes=sum(r['status']=='downloaded' for r in results)
    ctx.logger(f'PDF：成功 {successes} / {len(results)} 篇')
    return AgentResult(metrics={'downloaded':successes,'attempted':len(results)})
