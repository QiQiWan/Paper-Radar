# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""ProfileAgent: read-only Zotero signals and research-interest enrichment."""
from ..contracts import AgentResult


def execute(ctx):
    from ... import core
    ctx.require('zotero_read')
    ctx.checkpoint()
    ctx.task.emit(5, 'Zotero 收藏夹', detail='读取当前主题参考文献')
    config, ranker = core.prepare_zotero(ctx.config, logger=ctx.logger)
    ctx.config = config
    return AgentResult({'profile_ranker':ranker}, metrics={
        'reference_count':int(config.get('zotero_reference_count',0))})
