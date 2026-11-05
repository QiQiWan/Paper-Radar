"""On-demand structured research workflow: local -> cloud -> verify -> store.

Not run by daily searches: cloud access always starts from explicit user action.
"""
from __future__ import annotations
from pathlib import Path
from ..contracts import Agent, AgentSpec, AgentContext
from ..runtime import AgentRegistry, AgentRunner
from ..trace import store_trace
from . import fulltext, experiment, evidence, storage


def research_registry() -> AgentRegistry:
    specs = [
        (fulltext,AgentSpec('fulltext','全文准备','1.0','本地 PDF 版本核验和片段选取',
            produces=('fulltext_materials','evidence_map','pdf_document','cached_findings'),
            capabilities=('database_read','database_write'),time_budget_seconds=600,
            owner_module='paper_radar.agents.research.fulltext')),
        (experiment,AgentSpec('experiment','实验提取','1.0','从部分全文片段生成实验候选项',
            depends_on=('fulltext',),requires=('fulltext_materials','pdf_document','cached_findings'),
            produces=('experiment_raw','experiment_cached','experiment_cache_key'),
            capabilities=('cloud_model','database_read'),kind='llm_assisted',sends_to_cloud=True,
            time_budget_seconds=180,owner_module='paper_radar.agents.research.experiment')),
        (evidence,AgentSpec('evidence_verifier','证据核验','1.0','逐字核实原文、指标与数值',
            depends_on=('fulltext','experiment'),requires=('experiment_raw','experiment_cached','evidence_map','cached_findings'),
            produces=('verified_experiments',),time_budget_seconds=60,
            owner_module='paper_radar.agents.research.evidence')),
        (storage,AgentSpec('research_storage','结构化成果入库','1.0','只持久化已核验实验记录',
            depends_on=('evidence_verifier','fulltext'),
            requires=('verified_experiments','experiment_cached','experiment_cache_key','pdf_document'),
            produces=('experiment_result',),capabilities=('database_write',),time_budget_seconds=60,
            owner_module='paper_radar.agents.research.storage')),
    ]
    return AgentRegistry(Agent(spec,module.execute) for module,spec in specs)


def run_experiment_pipeline(paper_id: str, config: dict, *, db_path: Path,
                            task=None, logger=None, chat=None, get_key=None) -> dict:
    from ...progress import TaskProgress
    class _NoopTask:
        def emit(self,*args,**kwargs):pass
    if not paper_id or len(paper_id)>150:
        raise ValueError('论文 ID 无效')
    ctx=AgentContext(config={**config,'_analysis_paper_id':paper_id},
        db_path=Path(db_path),folder=Path(db_path).parent,
        fetcher=lambda *a,**kw:None,now=None,logger=logger or (lambda msg:None),
        task=task or _NoopTask())
    if chat is not None:ctx.state['_test_chat']=chat
    if get_key is not None:ctx.state['_test_key']=get_key
    runner=AgentRunner(research_registry())
    try:
        result=runner.run(ctx,on_finish=lambda c:c.state['experiment_result'])
        return {**result, 'agent_run':runner.last_trace}
    finally:
        # A separate file retains standard daily pipeline traces; one DB can hold both.
        try:store_trace(runner.last_trace,Path(db_path).parent/'agent_traces.sqlite3')
        except (OSError,ValueError):pass
