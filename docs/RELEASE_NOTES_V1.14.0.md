# Paper-Radar v1.14.0 发布说明

发布日期：2026-11-04（本地开发交付；未代表 GitHub 已发布正式 Release）

**主要变化：** 新增独立的科研实验分析 Agent 工作流，包括 FulltextAgent、ExperimentAgent、EvidenceVerifierAgent 和 ResearchStorageAgent；对选定 PDF 提取评价指标、原文数值、数据集、基线及页码，并以逐字引文门槛过滤。论文库新增“提取实验指标”，研究报告增加“导出实验数据证据索引”。

**升级兼容：** 继续保留九 Agent 日常流程和原数据库。新表 `experimental_findings` 按需创建；旧主题、PDF、Zotero、报告、更新检查功能不变。升级前务必备份全部个人目录。

**明确限制：** 只针对可提取文本的本地 PDF；复杂表格/多栏/OCR 尚未专门建模，结果主要覆盖连贯文字证据；数据集和指标可比性需要人工审阅。新版不自动调用模型、不静默更新代码、不提交远程仓库。

**详情：** [设计与验收](AGENT_ARCHITECTURE_V1.14.0.md) · [更新日志](../CHANGELOG.md) · [升级说明](UPDATING.md)。
