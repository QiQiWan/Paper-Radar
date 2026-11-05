# v1.16.0 起新增的签名验证与版本迁移要求

**请先手动安装具备可信 Ed25519 公钥的 v1.16.0**。此后自动更新须同时验证 GitHub Release 提供的 ZIP SHA-256 和发布者签名 manifest（`.json` + `.sig`）。只有三件套完整且互相匹配、版本更高、启动协议兼容，才允许执行代码替换。断网、签名错误或缺少签名时，保留旧版启动。

数据库不允许自动静默降级：`schema_migrations` 逐步登记迁移版本、名称、校验值和应用时间。对高于当前代码支持的 schema 或历史记录篡改将拒绝打开。升级前请用 `python tools/migration_cli.py --help` 查看备份/状态命令，并对真实库备份；无需上传个人数据或 API Key。

公网部署需要在维护窗口停止服务、备份账号库与 `storage/portal`，完成签名验证与迁移再启动。正在运行的公网服务会阻止桌面启动器热替换文件。

Windows 自动更新真实验收：`powershell -ExecutionPolicy Bypass -File tools/windows_update_acceptance.ps1`。Linux 模拟测试及离线 UI 检查不能代替 Windows 正式升级、HTTPS 反向代理与数据库回滚现场验收。

---

# v1.15.0 启动前自动更新与回滚（优先阅读）

**启动顺序：** 新版 `start_windows.bat` / `start_macos_linux.sh` → Python 标准库检查 GitHub → 符合条件则下载、验证、备份、安装 → 检查/安装依赖 → 打开新版本工作台。程序在启动前判断 GitHub API 是否可访问，无需单独 Ping；失联在短超时内回退到本地版本。

**首次升级：** v1.14.0 还没有启动时自动安装代码，首次安装 v1.15.0 请先关闭工作台，备份完整原目录并手工替换程序文件和两个启动脚本。保留 `settings.json`、`data/`、`downloads/`、`reports/`、`logs/`、`.venv/` 和本机 API 密钥。

**之后的自动更新：** 用户通过新版启动脚本启动，程序只检查 `QiQiWan/Paper-Radar` 的最新公开正式 GitHub Release，不检查普通 Git 标签。仅当版本更高，发布资产命名为 `Paper-Radar_vX.Y.Z_Source.zip`、提供 GitHub API `digest: sha256:...` 并通过大小、SHA、归档路径及启动协议检查时才会安装。保持 `startup.py`/`paper_radar.startup_update` 启动协议为 1 的后续正式版才允许自动升级。安装时会生成代码备份和 SQLite 快照，修改代码前已排除正在运行的本地 HTTP 工作台。

**自动恢复：** 若新版本依赖检查或工作台启动失败，启动器将恢复旧代码与更新前 SQLite 和 `settings.json` 快照，随后尝试运行旧版。若操作被断电打断，下次启动根据 `.paper_radar_updates/active.json` 继续恢复；若备份损坏，系统停止启动并提示人工处理。只有在升级前无法取得安全快照（例如数据库总量超过 512 MB、磁盘空间不足等）时才跳过升级。

**开发和运维命令：**

```bash
# Windows（cmd）
start_windows.bat --no-update
# macOS / Linux
sh start_macos_linux.sh --no-update
# 查看上次更新状态
python -m paper_radar.startup_update --status
# 只检查更新，不下载安装
python -m paper_radar.startup_update --check-only
# 手动回滚最近一次成功升级，仅回滚程序文件，保留当前文献数据库
python -m paper_radar.startup_update --rollback-last
```

**发布者必须做的工作：** 执行 `python tools/package_release.py --out dist/Paper-Radar_vX.Y.Z_Source.zip`（使用当前真实版本替换 X.Y.Z）；将 ZIP 以**完全相同名称**上传到仓库正式 GitHub Release，核对上传后 API 的 `assets[].digest`；客户端不使用 GitHub 自动生成的源码 ZIP。详见 `docs/PUBLISH_GITHUB.md`。

**安全说明：** SHA-256 验证的是 GitHub 发布记录与附件的一致性，并不证明源码绝对可信。启动自动更新默认启用，可在设置中关闭。已经运行的工作台不会被热更新；手动回滚成功运行较久的版本时，默认保留当前用户数据，不会静默丢弃升级之后的研究成果。后台定时检索使用 `main.py --once`，不会在任务执行中修改程序。

---

# v1.13.1 升级至 v1.14.0 的补充步骤

1. 完全关闭运行中的工作台和计划任务，备份整个项目目录，特别是 `settings.json`、`data/`、`downloads/`、`reports/`。
2. 解压独立源码 ZIP；确认根目录有 `main.py`、`paper_radar/agents/research/`、`paper_radar/experiments.py` 和 `CHANGELOG.md`。
3. 将程序源码与文档合并到旧目录。保留原有数据与本机系统凭据；新表 `experimental_findings` 只会在首次使用时按需创建。
4. 启动后先以现有主题执行一次不启用模型的普通检索，核查原有论文及报告。选择已合法下载的文字型 PDF，点击“全文证据”，再点击“提取实验指标”。调用云端模型前需自行确认发送内容与费用。
5. 检查“研究报告 → 导出实验数据证据索引”，确认导出只包含当前研究主题、有原文引文及页码的指标。跨论文数值仍由人工审核是否可以比较。
6. 新版已经内置自动检查 GitHub Release 的能力，但程序文件**不会自行更新**。发布者必须先创建公开的正式 `v1.14.0` Release 才会通知旧版本用户。

## v1.13.1 升级说明

从 v1.13.0 更新无需迁移数据库；保留 `settings.json`、`data/`、`downloads/`、`reports/` 和本机凭据管理器中 API Key。新增费用字段默认为 0（未配置），可在设置页填写单价。自动检查更新功能仍以 GitHub 已发布的正式 Release 为准，源码压缩包本身不会触发旧客户端升级提示。

升级后建议运行 `python -m paper_radar.agents doctor` 与 `python -m pytest -q`，并执行一次不启用模型的普通检索检查。Windows 后台任务、真实 Zotero 和模型服务需在目标机器验收。

## 历史版本（v1.10—v1.14）：仅提示更新，不自动安装

Paper-Radar 在本地浏览器工作台运行期间，可以在后台自动检查 GitHub 上是否发布了更新。检查只读取项目的公开 Release 元数据，不需要登录 GitHub，也不会把论文库、Zotero 数据、API Key 或本地配置发送给 GitHub。

## 一、自动检查在什么情况下执行

1. 启动本地 Web 工作台后，系统会在后台启动一次版本检查，**不阻塞页面、检索任务或本地报告导出**。
2. 检查间隔默认为24小时，可在“设置 → 软件更新检查”调整为6—168小时。上次检查时间保存到本地 `data/update_check.json`，退出再启动也遵守间隔。
3. 系统只查询 `https://api.github.com/repos/QiQiWan/Paper-Radar/releases/latest`。只有仓库已公开发布的正式 Release 才可用于比较；Draft、预发布版本、单独的 Git Tag 不会作为正式新版本推送。
4. 发现版本号高于当前软件时，工作台顶部显示“发现新版本”，可点“查看官方版本”；也可以选“本版本不再提醒”，直到下一次发现其他更高的版本才提醒。
5. 手动检查：进入“设置 → 软件更新检查”，点击“立即检查更新”。这个按钮可用于自动检查被关闭的情况。无需 API Key。

## 二、各状态怎样理解

| 状态 | 含义 | 处理建议 |
| --- | --- | --- |
| 发现新版本 | 公开正式 Release 的版本号高于当前安装版本 | 阅读 Release 更新说明，然后自行决定是否升级 |
| 当前不低于最新公开版本 | 当前安装版本号大于或等于 GitHub 的最新正式 Release | 不需要操作；开发版也可能出现此状态 |
| 暂无可访问的公开 Release | 官方端点返回404；可能尚未发布 Release，也可能仓库不可访问 | 核对仓库是否公开并已发布正式 Release |
| 检查失败/限流 | 网络故障、GitHub API 限流、格式异常或响应超时 | 不影响检索；可稍后手动重试 |
| 自动检查已关闭 | 用户主动关闭自动检查 | 仍可手动检查 |

**注意：** 程序不自动安装、下载或解压更新，避免覆盖个人数据及正在运行的 Python 文件。GitHub Release 链接经过固定域名与路径校验，界面只允许打开 HTTPS 官方地址。

## 三、如何安全升级源码版

推荐先关闭正在运行的 Paper-Radar，再完成以下步骤：

1. 在 GitHub Release 页面核对标签、发布日期、更新内容与附件名，例如 `v1.11.0`，不要从不明网站下载安装程序。
2. **先备份整个原文件夹**，尤其是 `settings.json`、`data/`、`reports/`、`downloads/` 和 `logs/`。Zotero 本身的数据库应继续由 Zotero 管理。
3. 将新源码 ZIP 解压到一个独立临时目录，确认根目录包含 `main.py`、`paper_radar/`、`requirements.txt`、`CHANGELOG.md`。
4. 将新版本的程序文件和文档合并到旧目录，保留原目录里的个人数据目录以及 `settings.json`。不要直接删除旧目录后重装，也不要将数据文件上传到 GitHub。
5. 双击 `start_windows.bat`，或在 macOS/Linux 执行 `sh start_macos_linux.sh`。启动脚本会按新版本的依赖清单检查/安装环境。
6. 打开“工作台”确认版本号，然后检查主题列表、历史论文库、报告、PDF 与 Zotero 配置。若工作台行为异常，可从完整备份恢复。

## 四、发布者必须完成的操作

更新检查依赖**GitHub Release**，因此每次正式发布都需要：

1. 同步调整 `paper_radar/__init__.py` 的版本号、`CHANGELOG.md`、`README.md` 和相关使用文档；源码 ZIP 里的版本也必须一致。
2. 运行单元测试、Python 编译检查、JavaScript 语法检查，并检查源码 ZIP 不含个人数据。
3. 合并正式代码到 GitHub 仓库，创建与版本一致的 Tag，例如 `v1.11.0`。
4. 进入仓库 Releases，创建**已发布的正式 Release**。不要只创建 Tag、Draft 或勾选 Prerelease。填写更新日志，上传源码 ZIP。
5. 在**未登录 GitHub 的浏览器**中确认 `https://github.com/QiQiWan/Paper-Radar/releases/latest` 可访问，并且显示的标签与 `v1.11.0` 一致。若仓库尚未公开、没有 Release 或创建了 Prerelease，程序不会推送这个版本。
6. 用低于最新正式版的旧版本程序进行一次模拟或实机检查，确认“发现新版本”以及跳转按钮可用。

更多信息请查看 [发布指南](PUBLISH_GITHUB.md)、[发布清单](RELEASE_CHECKLIST.md)、项目根目录 [CHANGELOG.md](../CHANGELOG.md)。

## 从 v1.10.0 升级到 v1.11.0

1. 完全关闭正在运行的工作台与 Windows 计划任务；备份完整旧目录，尤其是 `settings.json`、`data/`、`downloads/`、`reports/` 和 `logs/`。
2. 解压新版 `Paper-Radar_v1.11.0_Source.zip`，将程序代码迁入原目录。请保留个人数据，不能用空 `data/` 覆盖旧数据库。
3. 启动新版工作台，检查版本显示 `1.11.0`。打开各研究主题，核对默认主题原星标、阅读状态和旧报告。
4. v1.11 自动建立主题关系索引。旧版历史文献统一归入默认研究主题；如果以前已有其他主题，请逐一重新检索，以重建各自文献归属。
5. 先保持 AI 推荐与 OpenAlex 语义发现关闭，完成一次普通检索和报告检查；确认模型 API/语义额度后，再按需开启。
6. 可通过“依次追踪全部主题”验证每个主题独立生成报告；计划任务需在网页中重新保存“追踪全部主题”选项。更新提示功能保持原有官方 Release 检查方式。
7. 如需回滚，恢复相应版本的完整旧数据库备份；新旧数据库版本混用可能存在字段兼容问题。

## 从 v1.11.0 升级至 v1.12.0

1. 先停止本地工作台和每日计划任务，备份**整个旧目录**，特别是 `settings.json`、`data/`、`downloads/`、`reports/`、`logs/`。
2. 把 `Paper-Radar_v1.12.0_Source.zip` 解压到新目录。确认有 `paper_radar/fulltext.py`、`paper_radar/evaluation.py`、`requirements.txt` 和 `CHANGELOG.md`。
3. 按已有升级流程将程序文件和 `docs/` 合并到旧目录，保留原个人数据及密钥；不要用空的 `data/` 覆盖原库。启动脚本会安装新增 `pypdf` 依赖；离线机器应先准备相应依赖包。
4. 打开工作台确认显示 `1.12.0`；分别检查原研究主题、Zotero、星标、报告和本地 PDF 文件未丢失。
5. 先使用无模型模式执行常规检索；再选择已有开放获取 PDF，执行一次“解析本地 PDF”和正文检索。确认许可和云端发送范围后，才进行 AI 阅读卡片测试。
6. 新版在已有 SQLite 文件里增设全文相关表，全文索引时才建立记录。数据库备份应继续包含全文索引和证据卡片；新功能不会替换旧的元数据表。
7. 检查“设置 → 软件更新检查”仍能手动执行。**只有仓库创建正式公开 Release 后，旧版本才会收到 v1.12.0 更新提示**。本交付包本身不代表远程 Release 已发布。

如需回滚旧版本，应连同升级前的 `data/papers.sqlite3` 完整备份一并恢复。新旧版本对同一数据库来回运行可能产生兼容差异。


## 从 v1.12.0 升级至 v1.13.0

1. 关闭工作台、Windows 定时任务并备份原目录，重点保存 `settings.json`、`data/`、`downloads/`、`reports/` 和 `logs/`。
2. 将 v1.13.0 源码解压到新目录，核对 `paper_radar/agents/`、`docs/AGENT_ARCHITECTURE_V1.13.0.md` 和版本号；将程序文件合并进旧目录，保留全部个人数据。
3. 新版会在第一次运行检索后新增 `data/agent_traces.sqlite3`，与旧论文数据库分开，不会覆盖既有索引或主题反馈。
4. 先关闭 AI 功能执行一次普通检索，确认报告、主题、Zotero 与 PDF 正常。打开“设置 → Agent 工作流”检查步骤状态，再按需逐个启用模型。
5. 原 GitHub Release 自动检查仍然有效。要让旧客户端发现 v1.13.0，维护者必须将它作为正式公开 Release 发布。
6. 回滚应使用完整备份，避免把新旧数据库在不同版本程序中来回使用。


## 从 v1.13.1 升级至 v1.14.0

1. 停止本地工作台和 Windows 后台任务，完整备份 `settings.json`、`data/`、`downloads/`、`reports/` 与 `logs/`。
2. 解压新版源码至单独目录，确认 `paper_radar/experiments.py` 和 `paper_radar/agents/research/` 均存在；将程序文件与文档合并到已有目录，保留个人数据文件。
3. 启动工作台，确认显示 `1.14.0`；核对各研究主题、旧 PDF、已有中文报告和 Zotero 连接状态。
4. 先执行一次不开启模型的普通检索。再选择已下载 PDF，点击“全文证据 → 提取实验指标”，审核云端发送范围后确认。分析结果需要人工打开 PDF 的相应页码核查。
5. 到“研究报告”点击“导出实验数据证据索引”，仅保存当前主题的已验证结果。生成的新 CSV 和新增的数据库表不会删除旧报告及元数据。
6. 如需回滚，请使用升级前完整备份及对应旧版源码，不建议新旧版本反复共享同一个未经备份的数据库。

只有公开发布了相应的 GitHub **正式 Release**，旧客户端才会通过自动更新检查获知 v1.14.0，本地打包并不会自行推送版本。

## v1.17.0 公网队列数据库迁移

从 v1.16.0 升级前，应停机备份整个 `PAPERRADAR_PORTAL_DATA`。升级后的首次公网服务初始化将 `accounts.sqlite3` 的迁移版本从 `portal_auth` v2 升至 v3，并新增持久检索任务表；账号、会话及每个用户的论文数据库继续保留。不要运行多个公网实例共享同一数据根目录。

**自动安装签名限制：** 从具备 Ed25519 公钥固定信任的旧客户端升级到 v1.17.0 时，正式 Release 必须使用旧客户端已信任的公钥所对应的离线签名私钥，生成源码 ZIP / JSON manifest / `.sig` 三件套。如果没有匹配的私钥，请通过受控的手动升级和新信任根部署流程处理，不得声称旧客户端可以安全自动安装未经签名的 ZIP。
