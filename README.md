# Paper-Radar

**把最新文献整理成一份可以继续阅读和归档的研究简报。**

Paper-Radar 是一款运行在个人电脑上的科研文献追踪工具。输入研究关键词，或选择本机 Zotero 收藏夹作为研究参考，可以检索最近的论文、管理阅读状态、下载开放获取 PDF，并导出 Word、Markdown 与 CSV。界面通过本地浏览器访问，检索记录和报告保存在电脑上。

- **正式仓库：** https://github.com/QiQiWan/Paper-Radar
- **版本：** 1.9.0
- **作者与版权：** © 2026 @EatRice，代码按照 [MIT License](LICENSE) 发布（发布者须确认完整代码的授权资格）。
- **环境：** Python 3.10+，Windows 优先；macOS/Linux 可运行基础检索，Windows 后台计划任务仅在 Windows 上可用。

![Paper-Radar 工作台](docs/images/v19/dashboard.png)

## 可以做什么

**v1.9.0：** 首页突出检索入口，运行中才展开进度条；论文列表简化摘要，完整内容按需展开。近期报告严格检查出版日期和研究主题匹配，并根据原摘要给出可核实的研究方法、结果原句及待核查问题。

| 功能 | 当前支持 |
| --- | --- |
| 文献检索 | arXiv、OpenAlex、Crossref；可选 Springer Nature、Elsevier Scopus、ScienceDirect |
| 检索范围 | 默认按相关性选30篇；新版筛除未来、过期或日期不完整题录与仅匹配宽泛词的论文；可在检索高级选项中调整回溯天数、数量、排序 |
| Zotero | 连接本地 API 或只读读取 `zotero.sqlite`；按收藏夹提取关键词、排除已有文献 |
| 论文库 | DOI 去重、标题与摘要搜索、星标、阅读状态、分类折叠、分页 |
| PDF | 下载开放获取 PDF、批量下载、失败重试、下载历史 |
| 报告 | Word、Markdown、CSV，含本期研究综述；Markdown 可在网页内弹窗预览 |
| 运行监控 | 手动任务进度条、分阶段日志；Windows 每日后台任务日志与状态 |
| 模型 | DeepSeek、豆包、OpenAI API、Claude、通义千问、智谱、硅基流动、Gemini、兼容接口；调用用量统计 |

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

## Zotero 与授权来源

Zotero 用户先打开软件，在 **设置 → 高级 → 杂项** 勾选“**允许此计算机上的其他应用程序与 Zotero 通讯**”，再到 Paper-Radar **Zotero 研究方向** 连接收藏夹。也可使用只读 SQLite 方式。参考 [Zotero 图文说明](docs/ZOTERO_QUICKSTART.md)。

Springer Nature、Elsevier 的出版社 API 凭据在 **设置 → 出版社检索授权** 配置，开启相应来源后才发起请求。OpenAlex 的公开额度若不足，可通过环境变量 `OPENALEX_API_KEY` 配置。详情见 [文献来源及访问权限](docs/SOURCES_AND_ACCESS.md)。

## 模型设置与用量

在 **设置 → 中文速览** 选择服务商。推荐先尝试 DeepSeek：打开 [DeepSeek API 平台](https://platform.deepseek.com/)，创建 API Key，保持接口地址 `https://api.deepseek.com`，选择有权限的模型（预置为 `deepseek-flash`），点击“保存并测试连接”。

可选择豆包、OpenAI / ChatGPT API、Claude 等其他平台，模型 ID 和权限应以各平台控制台为准。Paper-Radar 记录本机成功调用的请求数与 Token；**DeepSeek 可尝试查询官方余额**，其他平台提供官方用量页面。详情见 [模型 API 配置](docs/API_PROVIDERS.md)。

## 文件位置

| 路径 | 内容 |
| --- | --- |
| `settings.json` | 检索、来源和模型设置（不保存密钥） |
| `data/papers.sqlite3` | 本地文献库 |
| `data/api_usage.json` | Paper-Radar 本机模型调用统计 |
| `downloads/` | 下载的开放获取 PDF |
| `reports/` | 最近的 Word、Markdown、CSV 报告 |
| `logs/` | 手动任务与后台任务日志 |

个人配置、数据库、下载和日志已列入 `.gitignore` 与源码打包排除规则。凭据默认由操作系统凭据管理器保存。**提交前仍应自行核对 `git status`。**

## 文档

[完整操作手册](docs/USAGE.md) · [文献来源与授权](docs/SOURCES_AND_ACCESS.md) · [模型 API](docs/API_PROVIDERS.md) · [报告说明](docs/REPORTS.md) · [v1.9真实截图清单](docs/REAL_SCREENSHOTS.md) · [Zotero](docs/ZOTERO_QUICKSTART.md) · [模块架构](docs/ARCHITECTURE.md) · [GitHub 发布](docs/PUBLISH_GITHUB.md) · [公众号教程](docs/PUBLIC_ARTICLE.md)

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
