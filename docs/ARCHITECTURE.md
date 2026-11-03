# 模块与数据流

Paper-Radar 前端为本地 HTML/CSS/JavaScript 工作台，后端为 Python HTTP 服务；任务通过 `paper_radar/core.py` 与 `paper_radar/progress.py` 协调。用户数据主要存入 SQLite，生成的 DOCX/Markdown/CSV 及下载 PDF 单独保存。

```text
本地浏览器 UI  <->  webapp.py（本地接口、任务与安全校验）
                         |
                         +-- core.py（配置、检索管线与本地数据库）
                         |     +-- sources/（arxiv、openalex、crossref、springer、elsevier、sciencedirect）
                         |     +-- zotero/（本地 API、只读 SQLite、关键词提取）
                         |     +-- downloads/（下载调度、开放获取验证）
                         |     +-- cloud_api.py / provider_usage.py（中文速览与本机 API 统计）
                         |     +-- reports/（Word、Markdown、CSV、本期研究综述）
                         +-- run_status.py / progress.py（阶段进度、持久化日志）
                         +-- scheduler.py（Windows 任务计划程序）
```

## 扩展边界

新增检索平台主要修改 `sources/` 适配器并注册，统一产出 `Paper`。下载器通过 `downloads/` 扩展，对每个站点遵循访问授权。Word 与 Markdown 复用 `reports/synthesis.py` 的综述逻辑。前端布局在 `paper_radar/static/`，避免将前端页面和所有数据处理逻辑混为一个巨大文件。

个人配置、数据、下载和日志保存在项目工作目录；运行时按配置来源顺序查询，单个来源报错可继续其他来源；全部失败则终止并提示错误。跨来源合并优先使用 DOI，排名采用关键词与 Zotero 文本相关性。默认最多选入30篇，下载单次上限为20篇。

详见 [增加检索器](ADDING_SOURCES.md)、[增加下载器](ADDING_DOWNLOADERS.md)、[本地 API](API.md)。
