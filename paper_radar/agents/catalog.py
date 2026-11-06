# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Explicit registry: no dynamic loading of untrusted third-party modules."""
from .contracts import Agent, AgentSpec
from .runtime import AgentRegistry
from .stages import profile, discovery, quality, ranking, storage, summary, download, selection, report

VERSION = '1.27.0'


def build_registry() -> AgentRegistry:
    """Specialist stage ordering and versioned input/output ownership."""
    definitions = [
        (profile, AgentSpec('profile','研究画像','1.1','读取当前主题 Zotero 参考文献',
            produces=('profile_ranker',),capabilities=('zotero_read',),time_budget_seconds=180,owner_module='paper_radar.agents.stages.profile')),
        (discovery, AgentSpec('discovery','文献发现','1.1','使用已授权来源与查询预算获取题录',
            depends_on=('profile',),produces=('retrieved',),capabilities=('network_read',),
            max_attempts=2,retry_backoff_seconds=0.75,time_budget_seconds=1800,
            owner_module='paper_radar.agents.stages.discovery')),
        (quality, AgentSpec('quality','质量筛选','1.1','检查日期、排除 Zotero 重复并计算基础相关度',
            depends_on=('discovery','profile'),requires=('retrieved','profile_ranker'),
            produces=('curated','removed_dates'),time_budget_seconds=180,owner_module='paper_radar.agents.stages.quality')),
        (ranking, AgentSpec('ranking','推荐排序','1.1','可选大模型证据重排；失败时保持关键词评分',
            depends_on=('quality',),requires=('curated',),produces=('ranked','ai_rerank'),
            failure_policy='continue',kind='llm_assisted',sends_to_cloud=True,
            capabilities=('cloud_model','database_read'),time_budget_seconds=900,
            owner_module='paper_radar.agents.stages.ranking')),
        (storage, AgentSpec('storage','论文入库','1.1','写入共享论文元数据及主题独立关联',
            depends_on=('quality',),requires=('curated',),produces=('stored','new_papers'),capabilities=('database_write',),
            time_budget_seconds=600,owner_module='paper_radar.agents.stages.storage')),
        (summary, AgentSpec('summary','中文速览','1.1','按额度为候选论文生成中文摘要',
            depends_on=('storage','profile'),requires=('stored','profile_ranker'),
            failure_policy='continue',kind='llm_assisted',sends_to_cloud=True,
            capabilities=('cloud_model','database_write'),time_budget_seconds=3600,
            owner_module='paper_radar.agents.stages.summary')),
        (download, AgentSpec('download','PDF 下载','1.1','仅处理允许获取的公开 PDF',
            depends_on=('storage',),requires=('stored',),failure_policy='continue',capabilities=('network_read','database_write','filesystem_write'),
            time_budget_seconds=3600,owner_module='paper_radar.agents.stages.download')),
        (selection, AgentSpec('selection','报告选文','1.1','按主题选择近期论文并附上有效证据',
            depends_on=('storage','profile'),requires=('new_papers','profile_ranker'),
            produces=('recent','new_selected'),capabilities=('database_read',),time_budget_seconds=180,owner_module='paper_radar.agents.stages.selection')),
        (report, AgentSpec('report','研究报告','1.1','导出 Word、Markdown 和 CSV',
            depends_on=('selection','storage'),requires=('recent','new_selected','new_papers','stored'),
            produces=('report_files',),capabilities=('filesystem_write',),time_budget_seconds=600,owner_module='paper_radar.agents.stages.report')),
    ]
    return AgentRegistry(Agent(spec,mod.execute) for mod,spec in definitions)


# Service agents are already callable from the UI on demand. They are kept
# outside the daily search workflow until the user requests such a task.
ON_DEMAND = [
    {'name':'query_planner','title':'检索规划','kind':'llm_assisted','entrypoint':'paper_radar.ai_planner.plan_research',
     'sends_to_cloud':True,'invocation':'user_confirmed'},
    {'name':'fulltext_reader','title':'全文证据','kind':'mixed','entrypoint':'paper_radar.fulltext.index_paper / analyze_paper',
     'sends_to_cloud':True,'invocation':'user_confirmed'},
    {'name':'experiment_analysis','title':'实验指标分析','kind':'mixed',
     'entrypoint':'paper_radar.agents.research.pipeline.run_experiment_pipeline',
     'sends_to_cloud':True,'invocation':'user_confirmed'},
    {'name':'evaluation','title':'推荐评估','kind':'deterministic','entrypoint':'paper_radar.evaluation',
     'sends_to_cloud':False,'invocation':'manual'},
    {'name':'synthesis','title':'跨论文实验比较','kind':'deterministic',
     'entrypoint':'paper_radar.agents.research.synthesis.run_synthesis_pipeline',
     'sends_to_cloud':False,'invocation':'user_confirmed'},
    {'name':'update_monitor','title':'版本监测','kind':'deterministic','entrypoint':'paper_radar.updates',
     'sends_to_cloud':False,'invocation':'scheduled'},
]
