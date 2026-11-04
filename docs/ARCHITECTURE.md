# 模块与数据流

Paper-Radar 前端为本地 HTML/CSS/JavaScript 工作台，后端为 Python HTTP 服务；任务通过 `paper_radar/core.py` 与 `paper_radar/progress.py` 协调。用户数据主要存入 SQLite，生成的 DOCX/Markdown/CSV 及下载 PDF 单独保存。

```text
本地浏览器 UI  <->  webapp.py（本地接口、任务与安全校验）
                         |
                         +-- core.py（配置、检索管线与本地数据库）
                         |     +-- sources/（arxiv、openalex、crossref、springer、elsevier、sciencedirect）
                         |     +-- zotero/（本地 API、只读 SQLite、关键词提取）
                         |     +-- downloads/（下载调度、开放获取验证）
                         |     +-- cloud_api.py / ai_planner.py / provider_usage.py（中文速览、检索词建议与 API 统计）
                         |     +-- reports/（Word、Markdown、CSV、本期研究综述）
                         +-- run_status.py / progress.py（阶段进度、持久化日志）
                         +-- scheduler.py（Windows 任务计划程序）
                         +-- topics.py（课题配置及生命周期）
                         +-- updates.py（官方 Release 后台检查与缓存）
```

## 扩展边界

新增检索平台主要修改 `sources/` 适配器并注册，统一产出 `Paper`。下载器通过 `downloads/` 扩展，对每个站点遵循访问授权。Word 与 Markdown 复用 `reports/synthesis.py` 的综述逻辑。前端布局在 `paper_radar/static/`，避免将前端页面和所有数据处理逻辑混为一个巨大文件。

个人配置、数据、下载和日志保存在项目工作目录；运行时按配置来源顺序查询，单个来源报错可继续其他来源；全部失败则终止并提示错误。跨来源合并优先使用 DOI，排名采用关键词与 Zotero 文本相关性。默认最多选入30篇，下载单次上限为20篇。

详见 [增加检索器](ADDING_SOURCES.md)、[增加下载器](ADDING_DOWNLOADERS.md)、[本地 API](API.md)。


## v1.9.1 研究规划与分析缓存

`ai_planner.py` 通过独立系统提示词调用现有云端模型，规范化 JSON 检索建议；用户确认后，由前端填入输入框，仍需手动点击检索。检索词保存到现有配置项，不额外创建多主题数据库。

`papers.summary_fingerprint` / `papers.summary_model` 记录 AI 分析依据及模型。当题名、摘要或提示词版本有变化时，候选速览会重新进入分析队列；模型生成内容经过有限的数值依据检查，任何关键科研判断仍需人工核对原文。


## v1.10.0 研究主题、检索规划和更新服务

`topics.py` 通过 `settings.json` 的 `topics` 列表与 `active_topic_id` 管理最多20个研究方向。原有的顶层配置字段保留为**活动主题的兼容快照**，使 v1.9.1 的配置能直接迁移。主题切换先恢复对应配置，再通过原有 `save_config()` 写入。文献 SQLite 和 PDF 下载仍是共享资源；`core.get_output_dir(config)` 根据主题ID将报告输出到 `reports/` 或 `reports/themes/<ID>/`。

`sources/base.py` 的 `query_plan()` 按来源规划查询：arXiv 使用组合检索；其他来源按单独关键词分组请求，设置 `max_queries_per_source` 默认8（1—20组）；各来源适配器仍按原签名收取其查询参数。多组成功结果轮流合并，去重后进行既有的日期和本地相关性筛选；若部分查询失败，来源状态标记 `partial`，成功结果保留。由于 API 使用量上升，应结合实际来源的频率限制调整查询预算。

`updates.py` 只访问固定 GitHub Release API，检查结果保存到 `data/update_check.json`，后台线程运行、HTTP 请求只读缓存。启动和运行中按检查间隔调度，前端发现较新正式版后显示可关闭的通知条。**不接受任意仓库 URL、不执行远程安装、不覆盖本地程序**。

下一阶段建议新增跨主题独立计划任务与语义混合检索，并建立人工标注集衡量推荐质量。

## v1.11.0 研究主题状态与可选推荐层

新增 `topic_papers(topic_id,paper_id)`，将主题特有的分数、星标、阅读状态、反馈、AI 推荐理由与证据和分析缓存指纹从共享 `papers` 中剥离。`connect()` 启动时执行幂等结构升级和一次默认主题迁移。`get_recent()`、`list_library()`、`count_library()`、`get_selected()`、报告导出读取活动主题关联，避免跨课题混用数据。

`recommend.py` 负责可选标题摘要 LLM 重排序，执行有限的原文证据检查、主题指纹缓存和失败降级。`sources/openalex_semantic.py` 使用 OpenAlex 语义检索端点补充新来源，用户在 `sources` 配置中显式开启，按照官方额度运行。`batch.py` 冻结全部主题配置后顺序运行，允许部分完成；`history.py` 只保存简化任务摘要并限定条目总数。`webapp.py` 提供批量任务、主题级论文 API、反馈和运行历史。

**截至历史版本 v1.11.0，**尚未建立全文 PDF 的结构化片段存储；下一阶段设计当时详见 [v1.11.0 完整设计方案](DESIGN_V1.11.0.md)，其中严格区分已实现模块和后续规划。

## v1.12.0 本地 PDF 全文证据与推荐评价模块

`fulltext.py` 负责受控 PDF 路径的本地读取、`pypdf` 按页文本提取、近似章节识别、有重叠文本切片、SQLite FTS5 检索与退化 LIKE 查询。它在主 SQLite 中新增 `fulltext_documents`（指纹、解析进度、状态）、`fulltext_chunks`（页码和原文片段）、`fulltext_claims`（AI 解释、准确引文、引用片段 ID、页码和模型缓存键）。索引缓存依赖 PDF SHA-256、解析器版本；AI 卡片还绑定模型与提示词版本。

`webapp.py` 通过 `/api/fulltext/status`、`/api/fulltext/search`、`/api/job` 的 `index_fulltext`/`analyze_fulltext` 调用这些服务。前端在“论文库”中按需打开“全文证据”，并在用户确认之后发送部分正文到云端模型；解析 PDF 时不调用模型。`core.py` 将符合当前实际 PDF 指纹的卡片附加到报表生成配置，`reports/text.py`、`reports/word.py` 才在逐篇详情中显示具体页码及引用内容。

`evaluation.py` 从 `topic_papers` 读取主题级排名，可导出人工标注模板，随后计算 P@K、标注集内 Recall@K、NDCG@K、MRR 和标签覆盖率。该模块不修改论文库，不调用网络，也不代替研究人员打标签。

> v1.11 章节中“当前尚未建立全文 PDF 的结构化片段存储”描述的是历史版本；v1.12 已完成以上第一阶段全文索引。真实结构化表格、公式与向量化 RAG 尚未实现。完整决策与局限详见 [v1.12.0 设计](DESIGN_V1.12.0.md)。
