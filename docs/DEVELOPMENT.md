# 开发与调试

Paper-Radar v1.14.0 使用 Python 3.10+ 和标准库本地 HTTP 服务。主要模块见 [ARCHITECTURE.md](ARCHITECTURE.md)，扩展检索器见 [ADDING_SOURCES.md](ADDING_SOURCES.md)，PDF 下载器见 [ADDING_DOWNLOADERS.md](ADDING_DOWNLOADERS.md)。

```bash
python -m pip install -r requirements.txt
python -m pip install pytest
python -m pytest -q
python -m compileall -q paper_radar main.py
node --check paper_radar/static/app.js
python main.py --demo
python main.py --serve
```

本地 HTTP 不对公网开放，修改接口须校验 `X-Local-Token`，下载器严格校验目标 URL 与本地路径。敏感凭据通过系统 keyring 或显式环境变量设置；日志不得记录密钥。修改发布逻辑后，使用 `tools/package_release.py` 创建源码 ZIP，并从干净目录解压重新运行测试。

测试主要采用 Mock API、Mock Zotero、内建 HTTP 服务和本地浏览器前端模拟。真实机构出版社权限及 Windows 定时任务需要具备相应条件的实际电脑验收。


## v1.10.0 回归重点

运行 `tests/test_v110_updates_topics_queries.py` 覆盖版本识别、Release 元数据解析、状态缓存、主题增删改切换及多关键词查询；运行完整 `python -m pytest -q` 检查旧版兼容性。更新检查测试应使用 Mock 网络响应，不能依赖实时 GitHub Release 状态或用户 API Key。发布前还需在真实 Windows/Web 浏览器中检验更新提醒、设置开关、主题选择和研究报告页面。每次提升版本应同步 [更新日志](../CHANGELOG.md)、[更新指南](UPDATING.md)、[发布检查](RELEASE_CHECKLIST.md) 和相关操作手册。

## v1.11.0 新增测试说明

推荐排序的模型调用须完全 Mock 后纳入 CI，任何密钥或外部学术 API 都不得成为日常测试依赖。重点测试 `topic_papers` 的旧库迁移、跨主题反馈隔离、模型 JSON 及原文证据校验、OpenAlex 语义接口错误、多主题顺序运行及历史记录的隐私边界。真实模型质量与学术推荐结果使用单独的人工标注评估集完成，详见 [完整设计方案](DESIGN_V1.11.0.md)。

## v1.13.0 各 Agent 独立开发

主流程移至 `paper_radar/agents/stages/`；增加阶段须在 `agents/catalog.py` 显式注册，并提供不依赖真实 API 的契约测试。推荐先执行 `python -m paper_radar.agents list` 和 `python -m paper_radar.agents graph` 检查依赖，再执行 `python -m pytest tests/test_v113_agents.py -q`。不能将不可信配置字符串直接用作 Python 模块加载路径。完整方案见 [AGENT_ARCHITECTURE_V1.13.0.md](AGENT_ARCHITECTURE_V1.13.0.md)。

## v1.13.1 Agent 运行治理专项检查

```bash
python -m paper_radar.agents doctor
python -m pytest tests/test_v131_agent_runtime.py -q
```

修改 Agent 执行规则时必须检查 `max_attempts`、`capabilities`、可重复性与 `time_budget_seconds`；不能在写入或模型计费阶段启用盲目重试。协作式检查点不会自动杀死阻塞线程。完整说明见 [Agent 运行治理](AGENT_RUNTIME_V1.13.1.md)。


## v1.14.0 实验分析 Agent 开发约束

对 `paper_radar/agents/research/` 的修改必须满足：1）不得把模型候选值直接写入主库；2）模型上下文只来自已管理本地 PDF 且大小有界；3）每条实验记录需连续原文引文及页码；4）模型费用阶段不得自动重试；5）PDF 中途被替换时禁止持久化旧结果；6）CSV 导出需防止公式注入并只含当前主题有效证据。

测试：`python -m pytest tests/test_v114_research_agents.py -q` 与 `python -m pytest -q`，同时执行 `python -m paper_radar.agents doctor` 和 `python -m paper_radar.agents research-graph`。详细设计见 [AGENT_ARCHITECTURE_V1.14.0.md](AGENT_ARCHITECTURE_V1.14.0.md)。

## v1.17.0 开发与测试边界

独立维护公网任务逻辑时，优先修改 `paper_radar/portal_jobs.py`，保留 `Portal.search()` 的幂等写入语义、`JobCancelled` 协作式取消及旧 HTTP `/api/public/jobs` 响应兼容。不得通过独立线程直接写其他用户工作区；入队、额度消耗、活动状态必须在同一 SQLite 事务内完成。

新增公网功能需要覆盖 `tests/test_v117_durable_portal.py` 的并发、重启、权限、分组、回归场景。运行 `python -m pytest -q`、`node --check paper_radar/static/portal.js` 和 `python -m paper_radar.agents doctor`，然后使用真实 Nginx HTTPS 做端到端检查。Chromium 受到环境策略限制时只能报告模拟渲染，禁止将其描述为真实公网通过。
