# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""SynthesisAgent workflow. Reuses verified local experiment records."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import uuid
from ..contracts import Agent, AgentSpec, AgentContext, AgentResult
from ..runtime import AgentRegistry, AgentRunner
from ... import synthesis
from ...migrations import migrate
from ...core import get_output_dir, TOPIC_ID_RE


@dataclass
class _Task:
    def emit(self,*args,**kwargs):pass


def evidence_step(ctx):
    ctx.require('database_read')
    with sqlite3.connect(ctx.db_path) as db:
        db.row_factory=sqlite3.Row
        records=synthesis.collect_local(db,ctx.config['active_topic_id'],db_path=ctx.db_path)
    return AgentResult({'synthesis_evidence':records},metrics={'records':len(records)})


def synthesis_step(ctx):
    ctx.checkpoint()
    result=synthesis.compare_records(ctx.state['synthesis_evidence'])
    return AgentResult({'synthesis_result':result},metrics={'groups':len(result['comparisons'])})


def export_step(ctx):
    ctx.require('filesystem_write')
    ctx.require('database_write')
    result=ctx.state['synthesis_result']
    folder=get_output_dir(ctx.config)
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/'跨论文实验比较.md'
    value=synthesis.to_markdown(result,ctx.config.get('_topic_name') or ctx.config['active_topic_id'])
    temporary=path.with_suffix('.md.tmp')
    temporary.write_text(value,encoding='utf-8')
    temporary.replace(path)
    with sqlite3.connect(ctx.db_path) as db:
        migrate(db,'research')
        db.execute('INSERT INTO synthesis_runs VALUES (?,?,?,?,?,?,?)',(
            uuid.uuid4().hex,ctx.config['active_topic_id'],result['generated_at'],
            result['paper_count'],result['finding_count'],'ready',path.name))
        db.commit()
    return AgentResult({'synthesis_file':path.name,'synthesis_stats':{
        'papers':result['paper_count'],'findings':result['finding_count'],
        'groups':len(result['comparisons'])}},metrics={'papers':result['paper_count']})


def registry():
    return AgentRegistry([
        Agent(AgentSpec('evidence_collect','读取已核验证据','1.0','仅使用有效 PDF 版本',
            produces=('synthesis_evidence',),capabilities=('database_read',),
            owner_module='paper_radar.agents.research.synthesis'),evidence_step),
        Agent(AgentSpec('synthesis','跨论文比较','1.0','按相同实验条件组织候选项',
            depends_on=('evidence_collect',),requires=('synthesis_evidence',),
            produces=('synthesis_result',),
            owner_module='paper_radar.agents.research.synthesis'),synthesis_step),
        Agent(AgentSpec('synthesis_export','研究进展报告','1.0','导出本期比较与证据索引',
            depends_on=('synthesis',),requires=('synthesis_result',),
            produces=('synthesis_file','synthesis_stats'),
            capabilities=('filesystem_write','database_write'),
            owner_module='paper_radar.agents.research.synthesis'),export_step),
    ])


def run_synthesis_pipeline(config: dict, *, db_path: Path, logger=None) -> dict:
    if not TOPIC_ID_RE.fullmatch(str(config.get('active_topic_id') or '')):
        raise ValueError('Invalid topic id')
    ctx=AgentContext(config=dict(config),db_path=Path(db_path),folder=Path(db_path).parent,
        fetcher=lambda *a,**kw:None,now=None,logger=logger or (lambda _:None),task=_Task())
    runner=AgentRunner(registry())
    result=runner.run(ctx,on_finish=lambda c:{'file':c.state['synthesis_file'],
        'stats':c.state['synthesis_stats']})
    return {**result,'agent_run':runner.last_trace}
