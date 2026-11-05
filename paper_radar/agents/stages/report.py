# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""ReportAgent: editable Word, Markdown and indexed CSV export."""
from datetime import datetime
from ..contracts import AgentResult


def execute(ctx):
    ctx.require('filesystem_write')
    ctx.checkpoint()
    from ...report import write_report,write_markdown,write_csv
    recent=ctx.state['recent']
    incoming=ctx.state['new_selected']
    folder=ctx.folder
    file_new=None
    if incoming:
        file_new=folder/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_新增论文.docx')
        write_report(file_new,'本次新增论文',incoming,ctx.config,len(ctx.state['new_papers']),demo=False)
    ctx.task.emit(90,'生成 Word',detail='写入研究综述、优先阅读和论文详目')
    weekly=folder/'近期文献汇总.docx'
    write_report(weekly,'近期文献汇总',recent,ctx.config,len(recent),demo=False)
    ctx.task.emit(95,'生成 Markdown',detail='正在整理可预览的 Markdown 报告')
    markdown=folder/'近期文献汇总.md'
    csv=folder/'文献索引.csv'
    write_markdown(markdown,recent,ctx.config)
    ctx.task.emit(98,'生成 CSV',detail='正在保存文献索引')
    write_csv(csv,recent)
    ctx.task.emit(100,'已完成',detail=f'共检索 {len(ctx.state["stored"])} 篇；报告收录 {len(recent)} 篇')
    ctx.logger(f'已检索 {len(ctx.state["stored"])} 篇，新增 {len(ctx.state["new_papers"])} 篇。')
    ctx.logger(f'汇总文档：{weekly}')
    return AgentResult({'report_files':{'weekly':weekly,'markdown':markdown,
                                        'csv':csv,'new_file':file_new}},
                       metrics={'exported':len(recent),'files':3+(1 if file_new else 0)})
