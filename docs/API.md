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
