# Paper-Radar 开发、部署与发布

本文件面向代码维护者。普通使用方法见 [使用手册](USER_GUIDE.md)。仓库根目录包含两套启动方式：本地工作台和公网多用户服务，两者共用 `paper_radar/` 中的核心科研模块。

## 1. 项目组织

| 路径 | 内容 |
| --- | --- |
| `main.py`、`startup.py` | 本地工作台与启动更新管理 |
| `paper_radar/` | 检索来源、数据库、全文解析、Zotero、研究任务、实验比较、证据链、报告和界面 |
| `start_public_*` | 公网 Web、后台 Worker 启动入口 |
| `deploy/` | 公网 Nginx 与 systemd 部署配置 |
| `.github/workflows/ci.yml` | Python 3.10–3.13 及 Windows 回归 |
| `.github/workflows/desktop-release.yml` | Windows / Ubuntu / macOS 构建、验收、校验、签名和 Release |
| `tools/packaging/` | 原生安装包、完整源码、发布签名、版本与仓库结构校验 |
| `tests/`、`benchmarks/` | 功能回归、边界测试和评估数据 |

设置与研究数据不会进入 Git 仓库，安装目录下的 `settings.json`、`data`、`downloads`、`reports`、`.paper_radar_updates` 均由 `.gitignore` 和打包器排除。Windows 新用户的资料默认保存在 `%LOCALAPPDATA%\Paper-Radar`。

## 2. 本地开发与基本检查

```bash
python -m pip install -r requirements.txt pytest
python -m tools.packaging.feature_parity
python -m tools.packaging.check_github_workflows
python tools/packaging/release_version.py
python -m paper_radar.agents doctor
python -m pytest -q
node --check paper_radar/static/app.js
node --check paper_radar/static/portal.js
```

`feature_parity` 会检查自 v1.34.0 完整仓库以来必须保留的模块、启动入口、测试工具和发行工作流。移除功能时应单独审查并更新清单，不能仅通过删除测试完成发布。合并的用户手册以 `<!-- GUIDE:... -->` 分节，`paper_radar/help_pages.py` 根据节标记提供各页面的帮助内容。

## 3. 公网版

公网启动文件为 `start_public_windows.bat`、`start_public_macos_linux.sh`，后台任务入口为 `start_public_worker_windows.bat`、`start_public_worker_macos_linux.sh`。按照 `deploy/` 中的 Nginx 和 systemd 配置进行生产部署。生产环境的数据库路径、域名、凭据、Workers 数量及注册策略需由管理员单独配置。必须使用 HTTPS、合适的服务权限、分离的运行数据目录，并在升级前停止 Web/Worker、备份数据。

环境变量模板为 `.env.example`。程序不会自动加载该文件，实际凭据通过操作系统环境、服务管理器或密钥管理系统配置。

## 4. 发布与工作流

将仓库根目录（包括隐藏目录 `.github`）完整提交到 GitHub。修改版本时执行：

```bash
python tools/packaging/bump_version.py X.Y.Z
python tools/packaging/release_version.py
python tools/package_release.py --out dist/Paper-Radar_vX.Y.Z_Source.zip
python -m tools.packaging.package_github_repo --out dist/Paper-Radar_vX.Y.Z_GitHub_Repository_Root.zip
```

正式 GitHub 仓库必须直接存在 `.github/workflows/ci.yml` 与 `.github/workflows/desktop-release.yml`；不能把 `Paper-Radar/` 目录再嵌套一层作为仓库根目录。`package_release.py` 生成供**源码更新器**使用的 `Paper-Radar/` 顶层压缩包；`package_github_repo.py` 生成无嵌套、可直接导入仓库根目录的压缩包。

发布流程先跑回归和 Chromium 性能测试，再在 Windows、Ubuntu、macOS ARM/Intel 上构建和验证安装包。正式发行要求完整附件 SHA-256 校验、独立 Ed25519 签名与人工原生平台批准。缺少签名或批准时仅发布 Prerelease，不允许自动升级。签名私钥只存放 GitHub Actions Secret `PAPER_RADAR_RELEASE_SIGNING_KEY_PEM`；人工批准变量为 `PAPER_RADAR_NATIVE_RELEASE_APPROVED`，其值必须与版本标签一致。用户安装包不能包含私钥。

发行后如需将既有已签名 Prerelease 晋升正式版，在 Actions 的 `desktop-release.yml` 通过 `promote_tag` 触发审核，流程核对旧附件签名和哈希，仅更新 Release 状态，不重新打包。

## 5. 持续跟进、备份与稳定性维护（v1.36.0）

跟进状态位于本地研究 SQLite 的 `following_*` 表（公网版在各用户独立工作区），数据库迁移通过现有迁移注册表执行。发现任务先规划各来源查询窗口，成功入库与变更事件在同一数据库事务中提交；达到返回上限、部分来源失败、缓存命中或关键词查询未完成时，来源检查点保留为 `pending`。API 提供源覆盖、事件与未读提醒，Web/Worker 的公网版采用登录态和 CSRF 验证。

本地完整备份及恢复命令如下。**恢复目标必须为空目录，先停止程序并验证快照，恢复完成后检查数据库、PDF 和配置再迁移运行路径。** 自动备份为仅元数据快照，最多保留 7 份，不会删除手工完整备份；生产环境还应将定期快照复制到异盘。

```bash
python -m tools.backup create
python -m tools.backup create --metadata-only
python -m tools.backup verify /path/to/backup-directory
python -m tools.backup restore /path/to/backup-directory --to /path/to/empty-research-dir
```

本地 SMTP 邮件默认禁用。由系统环境提供 `PAPER_RADAR_SMTP_HOST`、`PAPER_RADAR_SMTP_FROM`、`PAPER_RADAR_SMTP_PORT`（默认 465）、可选的 `PAPER_RADAR_SMTP_USER`/`PAPER_RADAR_SMTP_PASSWORD`。端口 465 使用 SMTP_SSL，其它端口必须先启用 STARTTLS。密码不保存到数据库和 Web 配置，站内提醒不依赖邮件发送。

持续跟进专项 CI 位于 `.github/workflows/reliability.yml`，覆盖 Windows、macOS、Linux 的迁移、备份、检索跟进和千篇元数据压力测试。正式版本发布流程的 Chromium 点击测试位于 `desktop-release.yml`；两种测试均不能替代人工 Windows/OneDrive 长时运行、外部论文接口和真实科研论文标注验收。

## 6. 文献发现、PDF 与 Zotero 的新增维护接口（v1.37–1.39）

`paper_radar/sources/openreview.py` 使用官方 OpenReview API 2 搜索公开 forum notes。它不会读取私人投稿，不保证所有会议版次都能检索，不把公开投稿自动认定为论文已录用。`paper_radar/retrieval_eval.py` 接收独立人工标注 JSONL 和排序结果 JSONL，`relevant` 必须为布尔值；当人工标签未覆盖所有预测时，P@k 返回 `null`。没有明确声明封闭评估池时，Recall@200 返回 `null`，同时单独报告部分标注池召回率。示例：

```bash
python -m paper_radar.retrieval_eval --gold labels.jsonl --run ranked.jsonl --k 20 --output retrieval_metrics.json
```

`paper_radar/pdf_geometry.py` 为两种工作区提供基于原 PDF SHA-256 的文字坐标、只读高亮 PNG 及表格候选。依赖可选安装 `python -m pip install "pymupdf>=1.24,<2"`，数据来自可选文字层，不进行 OCR，不能证明实验结果准确。

本地版 Zotero Web API 写入凭证必须来自进程环境 `ZOTERO_USER_ID` / `ZOTERO_API_KEY`，API 基址固定为 `https://api.zotero.org`，服务器端执行严格 DOI 匹配、预览指纹复核和 `If-Unmodified-Since-Version` 冲突校验。只创建子笔记；已创建的唯一 Paper-Radar 子笔记可经用户预览确认、远端指纹与本地 CAS 检查后回读并合并。不会自动更新 Zotero 题录、附件或已有笔记。公网多用户版只提供 BibTeX/RIS 导出，不接受个人 Zotero API 密钥。

新版专项测试位于 `tests/test_v139_sources_evidence_zotero.py` 与 `tests/test_v139_integration_and_eval.py`，CI 在 3.11 运行时安装 PyMuPDF，以验证 PDF 几何和跨账号读取保护。真实 OpenReview 查询、Zotero 账户写入、复杂 PDF 解析精度需要由有网络授权的实机执行。

## 7. 验收要求

提交变更至少检查完整 Python/JS 编译、两种服务的启动入口、本地检索到报告的模拟链路、账号隔离、全文证据、PDF/实验关联、更新签名/回滚、旧库迁移、两种压缩包中的 `.github` 路径和 GitHub Release 附件完整性。网页操作、Windows/macOS 原生安装和外部论文来源的网络访问需要在相应的真实环境做补充验收。CI 成功与原生操作系统安装成功应分别记录，不能互相替代。

## 7. 研究助手与检索可靠性维护（v1.40.0）

`paper_radar/research_intelligence.py` 在原证据链上运行五阶段问题驱动 Agent。数据迁移表中的 `research_inquiries` 保存不可覆盖的简报快照；存储时哈希、读取时校验，并重新核对人工结论修订及 PDF 指纹。公网版使用各用户独立数据库；模型排序仅在本地版、明确授权的范围内启用。论文题录仅形成阅读候选；实验差异和科学假设均须进一步核查。

所有公开来源的网络失败由 `paper_radar/sources/failures.py` 分类，浏览器只接收预定义的错误代码和说明，避免泄露 API URL 和凭据。监督子进程在 `sources/supervisor.py`，单请求 `sources/request_policy.py` 的 HTTP 超时短于子进程绝对时限；多来源调度在 `sources/base.py` 公平分配总预算。任务仍可因 DNS、公司代理、限流或授权缺失失败，不能以增加超时作为所有网络问题的解决办法。旧版默认 10/75 秒在 `core.validate_config()` 自动升级为 35/210 秒。

可在设置页主动执行“检查文献来源连接”，亦可在有网络权限的机器运行：

```bash
python -m paper_radar.sources.diagnostics --sources arxiv openalex crossref openreview --timeout 6
python -m pytest -q tests/test_v140_network_recovery.py tests/test_v140_research_inquiry.py
```

该诊断只访问内置公开服务白名单，不传递研究主题关键词、PDF 和密钥。`.github/workflows/provider-health.yml` 每周在 Windows/Ubuntu 上留存诊断结果，属于咨询性巡检，不作为外部服务断网时的主 CI 失败条件。不要在 CI 里把合成文献程序回归的通过率当作真实检索召回率。
