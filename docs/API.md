## v1.17.0 公网接口更新（优先阅读）

公网路由独立于桌面接口，所有写操作要求 HttpOnly 会话 Cookie、相同 Origin、自定义操作头及 CSRF Token。

| 方法 | 路径 | 内容 |
| --- | --- | --- |
| POST | `/api/public/search` | 202 返回 `{state:"queued",job_id}`；提交时原子校验24h配额与活动任务数 |
| GET | `/api/public/jobs` | 最近一个任务（仅当前登录账号） |
| GET | `/api/public/jobs/history?limit=10` | 最近任务列表（1—30条） |
| POST | `/api/public/jobs/cancel` | 取消当前账号任务；可选传 `{job_id:整数}` |
| GET | `/api/public/admin/jobs` | 管理员可读的全站状态数量及过去24h任务统计 |
| GET | `/api/public/papers?topic_id=default&limit=30&offset=0` | 返回 `{papers,total,limit,offset}`，`limit=1..100`，`offset=0..5000` |

公网服务绑定 loopback，经可信 Nginx 才能对公网访问，不能将桌面 `/api/config`、`/api/zotero/*` 和本地文件接口反向代理。

---

## v1.16.0 新增接口和安全边界

本地桌面版保留原先仅 loopback 的 API，`POST /api/job` 增加 `mode=synthesis`，可生成当前研究主题 `跨论文实验比较.md`；设置面板的 Agent 清单增加 `synthesis_pipeline`。

公网版以 `python -m paper_radar.public_portal serve` 独立启动，所有接口以 `/api/public` 为前缀，**绝不能将桌面 Web 服务或 `/api/config`、`/api/zotero/*`、本地 PDF/下载器及更新接口代理到公网**。

- 无需登录：`GET /api/public/health`、`POST /api/public/register`、`POST /api/public/login`。
- 登录后：`GET /api/public/me`、`GET /api/public/topics`、`POST /api/public/topics`、`GET /api/public/papers?topic_id=`、`POST /api/public/search`、`GET /api/public/jobs`、`POST /api/public/import`、`GET /api/public/synthesis?topic_id=`、`POST /api/public/password`、`POST /api/public/logout`。
- 管理员：`GET /api/public/admin/accounts`、`POST /api/public/admin/account`。
- 写操作需严格同源 Origin、`X-Portal-Action: 1`，登录后还需服务器生成的 `X-CSRF-Token`；会话 Cookie 不包含可读的账号资料和密钥。敏感账号/主题与文件路径由服务器会话决定，不信任前端传入文件路径。

公网版目前为受控功能集，不含完整 LLM 凭据管理、PDF 自动下载、Zotero、本地 OS 系统控制和全文问答。部署和额度说明见 [公网部署文档](PUBLIC_DEPLOYMENT_V1.16.0.md)。

---

# Paper-Radar 本地 HTTP 接口

工作台由本机 Python 服务提供。仅用于当前电脑，默认绑定 loopback 地址，并校验本地 Host；修改操作需要页面中的本地访问令牌。不要把服务暴露到公网。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务与版本状态 |
| GET | `/api/topics` | 当前研究主题及全部主题简要清单 |
| POST | `/api/topics` | 创建、切换、重命名或删除主题，需本地 Token |
| GET | `/api/update` | 缓存的本机/远程版本、上次检查时间和检查状态；不联网 |
| POST | `/api/update/check` | 后台触发一次强制检查更新，需本地 Token |
| POST | `/api/update/dismiss` | 对当前发现的新版本关闭提醒，需本地 Token |
| GET | `/api/state` | 用户配置、任务状态、来源、统计和摘要 |
| GET | `/api/papers` | 带搜索/筛选/分页的论文索引 |
| GET | `/api/downloads` | PDF 下载记录及状态 |
| GET | `/api/reports` | 已生成报告文件列表 |
| GET | `/api/report-preview?name=...` | 预览 `reports/` 下的 `.md` 文件，路径和大小受限 |
| GET | `/api/logs` | 手动 / 后台任务日志 |
| GET | `/api/provider-catalog` | 模型服务商已保存凭据的标志及本机调用统计，不返回密钥 |
| GET | `/api/provider-usage?provider=...` | 当前模型本机 Token 统计与支持的官方余额 |
| POST | `/api/config` | 检索配置、来源开启与模型配置 |
| POST | `/api/source-credential` | 存储 Springer / Elsevier API Key 和机构令牌 |
| POST | `/api/job` | 检索、更新报告、下载、模型连接测试等任务 |
| POST | `/api/zotero/collections` | 读取 Zotero 收藏夹 |
| POST | `/api/zotero/preview` | 提炼收藏夹关键词 |
| POST | `/api/zotero/apply` | 应用研究方向 |
| POST | `/api/schedule` | Windows 后台任务状态与管理 |

本地接口通过前端 `paper_radar/static/app.js` 封装。新增端点时请核对 POST 令牌、最大请求体、文件名白名单、允许的下载路径和错误信息是否包含密钥。当前没有公开公网 API 或移动客户端。


## v1.9.1 可选 AI 检索规划

`POST /api/ai/plan`，请求 JSON 示例：`{"goal":"我希望追踪神经算子在复杂几何与跨网格迁移方面的最新研究"}`。需要本地 CSRF Token 和已经配置的模型 API Key；返回 JSON 中的 `plan.topic`、`plan.keywords`、`plan.notes`、`plan.exclusions` 仅用于预览。该接口不会保存配置或发起学术源检索。输入长度限制12—1500字符，模型输出进行结构化校验。


## v1.10.0 多主题 API

`GET /api/topics` 返回 `active_topic_id` 和 `topics`，包含每个主题的 `id`、`name`、`keyword_count`、`sources`。

`POST /api/topics` 使用本地 Token，JSON 示例：

```json
{"action":"create","name":"跨几何神经算子"}
```

`action` 也支持 `switch`、`rename`、`delete`，相应操作提交 `topic_id`，其中 `rename` 额外提交 `name`。创建与切换会更新当前活动主题的检索配置，报告按主题ID存到不同子目录。最多20个主题；默认主题不可删除。

## v1.10.0 官方 Release 更新检查 API

- `GET /api/update` 仅返回已经缓存的最新版本检查信息，不同步连接 GitHub。
- `POST /api/update/check` 提交空 JSON 对象，即使关闭定期检查仍允许手动触发，后台执行，返回任务是否已启动。
- `POST /api/update/dismiss` 提交 `{"tag_name":"v1.10.1"}`，仅对当前已经核验的可用正式版本允许忽略。

查询由 `updates.py` 的后台线程完成，遵守6—168小时配置间隔；只访问 `https://api.github.com/repos/QiQiWan/Paper-Radar/releases/latest`。成功解析后必须验证 Release 链接属于本项目官方 HTTPS 发布页。不支持静默下载安装。`GET /api/state` 会附带 `update` 与 `topics_info`，以便前端定时刷新状态。

可配置项包括 `update_check_enabled`、`update_check_interval_hours`、`max_queries_per_source`。私人密钥不通过这些接口返回。

## v1.11.0 主题数据、推荐及任务历史接口

| 方法 | 路径 | 返回/参数 |
| --- | --- | --- |
| GET | `/api/papers?scope=topic&limit=40&offset=0&sort=relevance` | 当前主题论文库，包含 `feedback`、`recommend_reason`、`recommend_evidence` 等；`scope=all` 查全库，仍显示当前主题标记 |
| POST | `/api/paper` | 例 `{"id":"arxiv:2601.12345","feedback":1}`；`feedback` 只能为 -1/0/1，也可传 `starred` 或 `status`；仅修改活动主题 |
| POST | `/api/job` | `{"mode":"search_all"}`：按顺序执行全部主题；任务状态 `partial` 表示部分成功 |
| GET | `/api/history?scope=topic` | 最近任务摘要；`scope=all` 返回跨主题历史；最多返回30条 |
| POST | `/api/config` | 新增 `research_goal`、`ai_rerank_enabled`、`ai_rerank_limit`、`schedule_all_topics`。`sources` 可选择 `openalex_semantic`，需用户主动开启 |

历史数据单独保存 `data/run_history.sqlite3`，不包含 API Key 或全文 PDF。独立主题只影响推荐分数、阅读状态和报告筛选，原始文献表保持共享。前端由本地访问令牌保护写操作；接口不对外提供公网服务。

## v1.12.0 全文证据与后台任务接口

所有接口只能通过本地服务调用。GET 只读取本地缓存；POST `/api/job` 需要 `X-Local-Token`，并沿用现有 Host/Origin 验证。API Key 不随接口返回。

| 方法 | 路径/输入 | 处理与输出 |
| --- | --- | --- |
| GET | `/api/fulltext/status?paper_id=<已入库ID>` | 返回 `has_pdf`、`indexed`、`document`（`state/indexed_pages/total_pages/chunk_count/note`）、`claims`（类别、陈述、页码、原文引文）。PDF 内容变更则 `state=stale` 且不返回旧卡片。不存在的论文返回404。 |
| GET | `/api/fulltext/search?q=geometry&scope=topic&paper_id=<可选ID>` | 已索引 PDF 中的原文片段检索，返回 `results`（`paper_id/title/page/section/quote/chunk_id`）与 `total`，最多20条；`scope=topic` 限当前研究主题，`scope=all` 查全库；指定 `paper_id` 时限定单篇。 |
| POST | `/api/job` + `{"mode":"index_fulltext","ids":["paper-id"]}` | 在后台为1–10篇本地 PDF 建立分页索引；报告 `indexed/failed/results`；不会访问外网模型。 |
| POST | `/api/job` + `{"mode":"analyze_fulltext","ids":["paper-id"]}` | 对已索引的单篇 PDF 使用配置的大模型生成阅读卡片；前端操作前须获得用户同意。失败不覆盖原论文元数据。 |

后台任务通过既有 `GET /api/state` 查询进度及结束状态。全文解析或模型任务可能耗时较长，HTTP 的 202 只表示**任务已接受**，不能理解成索引或 AI 阅读卡片已完成。客户端必须轮询任务状态并重新读取全文状态。

`fulltext_documents` / `fulltext_chunks` / `fulltext_claims` 建立在主论文 SQLite 数据库中。`fulltext_chunks` 保存从 PDF 提取的原文片段，数据库需与本地 PDF 一样按照个人科研资料妥善保管。PDF 指纹不一致时，旧证据不会在状态、检索或新报告中展示。

## v1.12.0 推荐质量离线 CLI

与 HTTP 接口分离，避免打开新的浏览器写文件权限：

```bash
python -m paper_radar.evaluation --db data/papers.sqlite3 --export-template reports/推荐标注模板.csv --max-per-topic 50
# 完成人工标注 label 列（0—3）后：
python -m paper_radar.evaluation --db data/papers.sqlite3 --labels reports/推荐标注模板.csv --k 10 --out reports/推荐评估.json
```

CSV 标注最少应含 `topic_id,paper_id,label`。模板另带 `rank,title,source,published,url,notes` 便于人工判断；只需填写 `label` 列。未标注 Top-K 位置计入保守下界且单独报告覆盖率，Recall 分母仅为已标注相关文献。


## v1.13.0 Agent 工作流与运行记录

`GET /api/agents` 返回 `{pipeline, on_demand, recent_runs}`。`pipeline` 为九个已注册的主流程 Agent（名称、职责、版本、输入输出及故障策略）；`on_demand` 为现有按需功能清单；`recent_runs` 返回当前主题的最近五条阶段统计。仅向本地工作台提供，遵守 Host 校验，不返回 API Key、模型提示词或 PDF 原文。

独立命令 `python -m paper_radar.agents list|graph|traces` 可查看同一组定义与本机最小化运行轨迹。配置、检索、报告相关旧版 HTTP 路由没有变化。

## v1.13.1 Agent 诊断字段及模型费用

`GET /api/agents` 的 `pipeline[]` 新增 `capabilities`、`max_attempts`、`retry_backoff_seconds`、`time_budget_seconds`；`recent_runs[].steps[]` 可包含 `attempts`、`budget_exceeded`、`api_usage` 和 `estimated_cost_usd`。`recent_runs[]` 汇总 `api_usage`，只有用户设置价格时才包含费用估算。运行轨迹不包含完整提示词、密钥或论文全文。

`POST /api/config` 可选设置 `llm_input_usd_per_million`、`llm_output_usd_per_million`，单位为美元/百万 Token；默认均为 0，表示未配置单价。此接口沿用本地 Token 校验。程序没有自动实时获取供应商价格，也不会据此预测真实账单。

`python -m paper_radar.agents doctor` 是本机只读诊断，不占用外部配额。当前不提供 HTTP 故障注入接口。


## v1.14.0 实验指标分析接口

| 方法 | 路径 / 模式 | 返回值及约束 |
|---|---|---|
| GET | `/api/experiments/status?paper_id=<已入库ID>` | 已核验的 `kind/dataset/metric/value/baseline/setting/chunk_id/page/quote`；PDF 变更时 `status=stale`、`findings=[]`；论文不存在返回404 |
| POST | `/api/job`，`{"mode":"analyze_experiments","ids":["paper-id"]}` | 用户确认后的单篇实验分析后台任务；发送最多16个有界 PDF 片段到指定模型，可能收费；需要 CSRF Token |
| POST | `/api/job`，`{"mode":"export_experiments"}` | 导出当前主题已核验且 PDF 未变更的实验结果 CSV，无模型费用；需 CSRF Token |

HTTP 202 仅表示任务已入队，应继续轮询 `/api/state`，任务完成后再刷新状态或报告文件列表。此接口只接受已管理的本地 PDF，禁止任意路径及未授权网页抓取。模型回答经过逐字连续引文校验，但不能替代科学性审查。

## v1.15.0 启动更新状态

`GET /api/update` 或 `GET /api/state` 中的 `update.startup_update` 现在返回本机上次启动更新的部分状态信息：`state`、`message`、`at`、`installed_version`、`previous_version`、`attempted_version`（视实际情况存在）。不返回数据库内容、备份路径或密钥。`POST /api/update/check` 仍只执行**后台版本提示检查**，不会在线安装或重启正在运行的应用。自动安装只发生在下次经 `startup.py` 进行的完整启动前。

新增全局配置项 `auto_update_on_startup`（布尔值，默认 true）。可通过原有 `POST /api/config` 保存。后台运行版本检查由 `update_check_enabled` 控制；关闭它时也会跳过启动自动更新。用户资料不参与源代码更新。
