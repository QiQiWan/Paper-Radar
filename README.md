# Paper-Radar

**把最新文献整理成一份可以继续阅读和归档的研究简报。**

Paper-Radar 是一款运行在个人电脑上的科研文献追踪工具。输入研究关键词，或选择本机 Zotero 收藏夹作为研究参考，可以检索最近的论文、管理阅读状态、下载开放获取 PDF，并导出 Word、Markdown 与 CSV。界面通过本地浏览器访问，检索记录和报告保存在电脑上。

- **正式仓库：** https://github.com/QiQiWan/Paper-Radar
- **版本：** 1.12.0
- **作者与版权：** © 2026 @EatRice，代码按照 [MIT License](LICENSE) 发布（发布者须确认完整代码的授权资格）。
- **环境：** Python 3.10+，Windows 优先；macOS/Linux 可运行基础检索，Windows 后台计划任务仅在 Windows 上可用。

![Paper-Radar 工作台](docs/images/v19/dashboard.png)

## 可以做什么

**v1.12.0：** 新增本地 PDF 按页全文索引、原文片段检索、用户确认后的 AI 证据阅读卡片和可引用页码的 Word/Markdown 报告；增加人工标注模板及离线推荐质量评价。PDF 更新后旧证据自动失效。全部新功能按需使用，普通检索无需模型 Key。详情见 [v1.12.0 完整设计](docs/DESIGN_V1.12.0.md)。

**v1.11.0：** 在 v1.10 研究主题与官方版本更新提醒基础上，进一步实现每个主题独立的论文相关度、星标、阅读状态与反馈；增加按顺序追踪全部研究主题、可查询的本地运行历史，以及**默认关闭**的 OpenAlex 语义发现和大模型“标题摘要证据”推荐排序。普通检索无需模型 Key。模型相关结论以标题/摘要为证据范围，重要学术判断需要查看原文。

**v1.10.0：** 新增多研究主题与独立报告归档、多查询关键词检索、GitHub Release 后台自动更新检查及桌面提示；保留 v1.9.1 的 AI 检索规划、跨来源摘要合并修复和 AI 数值依据检查。多查询按来源设定上限和间隔；关闭自动检查后仍可手动查询正式发布版本。

| 功能 | 当前支持 |
| --- | --- |
| 文献检索 | arXiv、OpenAlex、Crossref；可选 Springer Nature、Elsevier Scopus、ScienceDirect；每来源多关键词查询（查询量可设定） |
| 检索范围 | 默认按相关性选30篇；新版筛除未来、过期或日期不完整题录与仅匹配宽泛词的论文；可在检索高级选项中调整回溯天数、数量、排序 |
| 研究主题 / Zotero | 多主题独立检索配置、报告、星标、阅读状态及相关性反馈；连接 Zotero 本地 API 或只读读取 `zotero.sqlite`，按收藏夹提词、排除已有文献 |
| 论文库 | DOI 去重、主题级相关性推荐、标题摘要搜索、星标、阅读状态、相关/不相关反馈、分类折叠与分页 |
| PDF | 下载开放获取 PDF、批量下载、失败重试；按需建立本地分页索引和原文证据检索 |
| 报告 | Word、Markdown、CSV，含本期研究综述；可附已核验的 PDF 引文页码与 AI 阅读卡片；Markdown 在线预览 |
| 运行监控与更新 | 检索进度、分阶段日志；Windows 每日后台任务（可选择全部主题）；运行历史；自动/手动检查官方 GitHub Release 并提醒更新 |
| 模型 | DeepSeek、豆包、OpenAI API、Claude、通义千问、智谱、硅基流动、Gemini、兼容接口；可选 AI 检索词建议、摘要级中文速览、证据约束推荐排序、模型调用统计 |

**默认启用** arXiv、OpenAlex、Crossref；Springer、Scopus、ScienceDirect 默认关闭。后者需要向出版社申请 API Key 并满足相应接口权限，机构订阅、校园网/VPN 或机构令牌可能影响授权。此软件目前只通过出版社接口取得题录，不提供付费全文自动下载。

## 运行

Windows：安装 [Python 3.10+](https://www.python.org/downloads/)，勾选 **Add Python to PATH**；将仓库下载到固定文件夹，双击 `start_windows.bat`。首次运行会创建 `.venv` 并安装所需依赖；随后浏览器打开本机工作台。

macOS / Linux：

```bash
sh start_macos_linux.sh
```

命令行：

```bash
python main.py --serve   # 打开本地网页工作台
python main.py --once    # 立即检索一次并生成报告
python main.py --cached  # 只用本地论文库更新报告
python main.py --demo    # 生成虚构数据的版式演示
```

在工作台输入英文关键词，例如 `neural operator, operator learning`，保留默认来源，点击 **检索并生成报告**。任务进度和日志会显示在顶部。检索完成后前往 **论文库** 及 **研究报告**。不配置大模型也能检索和生成报告。

**AI 检索词规划（v1.9.1 起）：** 先在设置页配置并保存有权限的模型 API Key；回到工作台，展开“用 AI 规划检索词”，写下研究问题，点击“生成检索词建议”；确认、修改英语检索词后点击“应用至研究关键词”，最后由用户单独点击“开始检索并生成报告”。该规划不会自动保存主题、发起检索或访问 Zotero。AI 仅根据输入的研究描述提出检索词，不能保证文献覆盖完整。v1.10.0 起，各适用来源分别执行多组检索，默认最多采用前8组关键词，可在设置中调整（1—20组）；增加组数可能提升 API 调用量。

## 多研究主题与软件更新（v1.10.0）

在工作台顶部的“研究主题”选择器中可以新建、重命名、切换或删除主题。每个主题保留各自的关键词、来源、检索时间范围、Zotero 收藏夹关联和报告；默认主题依旧写入 `reports/`，新建主题的报告写入 `reports/themes/<主题ID>/`。历史文献数据库及下载的 PDF 共用；删除主题只移除配置记录，不会自动删除历史报告和论文。v1.11.0 可以在“每日自动检索”勾选“每日依次追踪全部研究主题”；未勾选时继续追踪当前活动主题。每个主题分别保存阅读状态、星标与相关性反馈，论文元数据、PDF 与下载状态共用。

启动本地网页后，系统默认每24小时后台查询一次 GitHub 仓库的最新**正式 Release**；发现更新会在工作台顶部提醒，点击“查看官方版本”前往官方发布页。可以在“设置 → 软件更新检查”开启/关闭自动检查、修改6—168小时的间隔、随时手动检查。软件不自动覆盖文件，升级前须备份本地数据。Git 标签或 Draft Release 不会触发提示。详细步骤请看 [更新检查与安全升级](docs/UPDATING.md)。

## AI 推荐、语义发现与运行历史（v1.11.0）

用户可在 **设置 → 智能推荐** 开启模型重排序，并将候选篇数设置为 1—30 篇（建议先用 10—15 篇）。请先配置相应服务商的 API Key。工作台的 AI 研究目标作为推荐依据。模型分析只使用候选论文题名与摘要，并需要给出来自原始文本的证据片段；若缺少证据或服务失败则保持既有排序。每次可能产生模型费用；当前没有验证全部服务商的实际费用与调用兼容性。

**OpenAlex 语义发现** 是检索高级选项中的可选来源，默认关闭。该接口支持自然语言研究问题，但可能使用独立的检索额度。建议先完成普通关键词检索，再确认 OpenAlex 官方账户与额度后开启。只接收返回的论文题录，不下载受版权限制的 PDF。

在 **论文库** 顶部选择“当前研究主题”或“所有已收录文献”，对论文点击 **相关 / 不相关**，当前主题的推荐顺序会随之调整。进入 **设置 → 运行记录** 可以检查近期主题执行情况。批量执行时部分主题失败，会显示“部分完成”，成功主题已有的报告不会丢失。

升级前请备份 `data/papers.sqlite3`。旧的星标与阅读进度保存在默认主题；升级前存在的其他研究主题无法自动恢复精确的历史归属，需要按各主题重新执行一次检索。

## 全文证据精读与推荐评估（v1.12.0）

论文库中已经下载 PDF 的卡片提供 **全文证据** 按钮。点击 **解析本地 PDF**，程序在本机按页提取原文并建立索引，**不调用云端模型**。随后可以搜索论文中的技术术语，查看 PDF 原文页码；切换为“当前主题”可检索本课题已索引的论文。

需要方法、实验和局限的 AI 阅读卡片时，点击 **生成 AI 阅读卡片** 并确认费用与上传范围。系统会向配置的模型发送最多12个有界的 PDF 文本片段，严格核对模型所引用的原文。生成的卡片可复制为 Markdown，也可重新导出 Word/Markdown 汇总报告。扫描 PDF、复杂表格和公式仍需人工阅读原文；含引文的模型解释不等于人工学术审查。文件被替换后必须重新建立索引。

**人工评价推荐质量：** 先导出 CSV 标注模板，再人工填写 0—3 级相关性评价，最后运行离线评估：

```bash
python -m paper_radar.evaluation --export-template reports/推荐标注模板.csv --max-per-topic 50
python -m paper_radar.evaluation --labels reports/推荐标注模板.csv --k 10 --out reports/推荐评估.json
```

推荐分数和人工标注有不同含义，评价报告同时说明标注覆盖率。详见 [完整操作指南](docs/USAGE.md) 和 [设计方案](docs/DESIGN_V1.12.0.md)。

## Zotero 与授权来源

Zotero 用户先打开软件，在 **设置 → 高级 → 杂项** 勾选“**允许此计算机上的其他应用程序与 Zotero 通讯**”，再到 Paper-Radar **Zotero 研究方向** 连接收藏夹。也可使用只读 SQLite 方式。参考 [Zotero 图文说明](docs/ZOTERO_QUICKSTART.md)。

Springer Nature、Elsevier 的出版社 API 凭据在 **设置 → 出版社检索授权** 配置，开启相应来源后才发起请求。OpenAlex 的公开额度若不足，可通过环境变量 `OPENALEX_API_KEY` 配置。详情见 [文献来源及访问权限](docs/SOURCES_AND_ACCESS.md)。

## 模型设置与用量

在 **设置 → 中文速览** 选择服务商。推荐先尝试 DeepSeek：打开 [DeepSeek API 平台](https://platform.deepseek.com/)，创建 API Key，保持接口地址 `https://api.deepseek.com`，选择有权限的模型（预置为 `deepseek-flash`），点击“保存并测试连接”。

可选择豆包、OpenAI / ChatGPT API、Claude 等其他平台，模型 ID 和权限应以各平台控制台为准。Paper-Radar 记录本机成功调用的请求数与 Token；**DeepSeek 可尝试查询官方余额**，其他平台提供官方用量页面。中文速览会发送论文标题与摘要；手动触发 AI 检索词规划则会发送研究描述。已缓存的旧模型摘要会按新提示词版本自动失效并在符合限额时重新生成，这可能产生一次额外 API 费用。自动数值校验只能筛查部分明显问题，科研结论仍须回到原论文核实。详情见 [模型 API 配置](docs/API_PROVIDERS.md)。

## 文件位置

| 路径 | 内容 |
| --- | --- |
| `settings.json` | 检索、来源和模型设置（不保存密钥） |
| `data/papers.sqlite3` | 本地文献库、全文分页片段和证据卡片（随数据库一起备份） |
| `data/api_usage.json` | Paper-Radar 本机模型调用统计 |
| `data/run_history.sqlite3` | v1.11.0 运行历史（只含任务摘要） |
| `data/update_check.json` | 最近一次 GitHub Release 检查时间和状态；不含研究数据 |
| `downloads/` | 下载的开放获取 PDF |
| `reports/` | 默认主题的 Word、Markdown、CSV 报告；其他主题分别存放在 `reports/themes/<主题ID>/` |
| `logs/` | 手动任务与后台任务日志 |

个人配置、数据库、下载和日志已列入 `.gitignore` 与源码打包排除规则。凭据默认由操作系统凭据管理器保存。**提交前仍应自行核对 `git status`。**

## 文档

[v1.12.0 设计方案](docs/DESIGN_V1.12.0.md) · [v1.12.0 实施审阅](docs/REVIEW_V1.12.0.md) · [v1.12.0 测试结果](docs/TEST_RESULTS_V1.12.0.md) · [v1.11.0 历史方案](docs/DESIGN_V1.11.0.md) · [v1.11.0 审阅](docs/REVIEW_V1.11.0.md) · [v1.10.0 历史审查](docs/REVIEW_V1.10.0.md) · [更新日志](CHANGELOG.md) · [更新与升级指南](docs/UPDATING.md) · [v1.9.1 历史审查](docs/REVIEW_V1.9.1.md) · [完整操作手册](docs/USAGE.md) · [文献来源与授权](docs/SOURCES_AND_ACCESS.md) · [模型 API](docs/API_PROVIDERS.md) · [报告说明](docs/REPORTS.md) · [v1.9 旧版截图清单](docs/REAL_SCREENSHOTS.md) · [Zotero](docs/ZOTERO_QUICKSTART.md) · [模块架构](docs/ARCHITECTURE.md) · [GitHub 发布](docs/PUBLISH_GITHUB.md) · [公众号教程](docs/PUBLIC_ARTICLE.md)

## 离线复核已生成的题录

程序自带 `tools/offline_review.py`，可读取本地 CSV，按指定基准日期和研究词复核，并重新输出 Word、Markdown。此过程不访问学术网站，也不修改原始数据库。示例命令见 [报告说明](docs/REPORTS.md)。

## 测试与发布

```bash
python -m pip install -r requirements.txt
python -m pip install pytest
python -m pytest -q
python -m compileall -q paper_radar main.py
node --check paper_radar/static/app.js
python tools/package_release.py --out Paper-Radar_Source.zip
```

单元测试使用模拟 API 和 Zotero 数据；真实 API 配额、机构授权、PDF 可用性及 Windows 任务计划程序仍应在使用环境中验证。下载论文的版权和再使用权限遵循原作者及出版社条款。本项目与 Zotero、arXiv、OpenAlex 和出版社无官方隶属关系。
