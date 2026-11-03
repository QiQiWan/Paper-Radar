# Paper-Radar 本地 HTTP 接口

工作台由本机 Python 服务提供。仅用于当前电脑，默认绑定 loopback 地址，并校验本地 Host；修改操作需要页面中的本地访问令牌。不要把服务暴露到公网。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务与版本状态 |
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
