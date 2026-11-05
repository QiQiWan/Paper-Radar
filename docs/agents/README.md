# Paper-Radar Agent 独立研发清单（v1.13.1）

各文件对应一个可独立修改、测试和发布契约版本的 Agent。主流程注册在 `paper_radar/agents/catalog.py`，接口定义在 `contracts.py`，通用编排器在 `runtime.py`，现有完整架构见 [Agent 模块化设计](../AGENT_ARCHITECTURE_V1.13.0.md)。

## 工作分配原则

- 先冻结该 Agent 的输入、输出、错误行为，再改提示词或算法。
- 任何涉及外部模型的功能都需要离线 Mock 测试、额度限制、原文证据检查与降级路径。
- 文献元数据、主题反馈与 PDF 文件的写权限不得随意扩散到其他 Agent。
- 一个独立 Agent 任务必须提交 `tests/test_v113_agents.py` 或专属测试文件的新增案例，运行全库测试，并更新主 `CHANGELOG.md`。
- **Agent 单独版本**保存在 `catalog.py` 的 `AgentSpec.version`；项目整体版本保存在 `paper_radar/__init__.py`。

## 可独立认领的研发任务

| Agent | 当前版本 | 下一项最值得完成的工作 | 验收依据 |
|---|---|---|---|
| Profile | 1.0 | 从 Zotero 收藏夹生成可审阅研究画像并跟踪兴趣漂移 | 手工确认后才改写主题，跨主题隔离 |
| Discovery | 1.0 | 对查询子计划做命中分布分析与增量去重 | 各来源召回与失败统计可复核 |
| Quality | 1.0 | 日期、撤稿、出版版本与 DOI 统一处理 | 标准题录集 F1 与冲突处理报告 |
| Ranking | 1.0 | 加入本地 Embedding 混合排序与人工反馈 | P@10、NDCG@10 对比关键词基线 |
| Storage | 1.0 | 完成版本/来源溯源与原子化更新 | 幂等入库、升级回滚和无数据丢失 |
| Summary | 1.0 | 摘要结构化输出与多模型成本路由 | 错误率、引用证据与调用费用 |
| Download | 1.0 | PDF 队列检查点、自动退避及磁盘容量管理 | 网络失败恢复、许可过滤与文件完整性 |
| Selection | 1.0 | 支持跨文献重复结果识别与覆盖质量诊断 | 当前主题选文可重现，证据无过期 |
| Report | 1.0 | 报告增量生成、技术比较表、来源审计页 | Word/Markdown 数值与原文来源一致 |

已有按需功能 `ai_planner.py`、`fulltext.py`、`evaluation.py`、`updates.py` 仍保留独立入口；相关职责迁入统一 Agent 契约属于下一阶段，不宜先改变用户已有使用入口。

## v1.13.1 Agent 责任边界

现有 Agent 必须在 `AgentSpec.capabilities` 中声明资源能力，并在涉及资源的阶段入口调用 `ctx.require()`。请根据是否产生数据库写入、文件写入或云端模型费用决定是否允许自动重试。新增契约参见 [Agent 执行治理](../AGENT_RUNTIME_V1.13.1.md)。

单 Agent 可通过 `AgentRunner.run(context, faults={'discovery':[TimeoutError('mock')]})` 在离线测试中注入读取故障。此接口不能由网页用户调用，不能用来注入真实执行任务。提交 PR 前运行 `python -m paper_radar.agents doctor`、专项测试及全量测试。
