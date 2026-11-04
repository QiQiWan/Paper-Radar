# 软件更新检查与升级指南（v1.10.0）

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

1. 在 GitHub Release 页面核对标签、发布日期、更新内容与附件名，例如 `v1.10.0`，不要从不明网站下载安装程序。
2. **先备份整个原文件夹**，尤其是 `settings.json`、`data/`、`reports/`、`downloads/` 和 `logs/`。Zotero 本身的数据库应继续由 Zotero 管理。
3. 将新源码 ZIP 解压到一个独立临时目录，确认根目录包含 `main.py`、`paper_radar/`、`requirements.txt`、`CHANGELOG.md`。
4. 将新版本的程序文件和文档合并到旧目录，保留原目录里的个人数据目录以及 `settings.json`。不要直接删除旧目录后重装，也不要将数据文件上传到 GitHub。
5. 双击 `start_windows.bat`，或在 macOS/Linux 执行 `sh start_macos_linux.sh`。启动脚本会按新版本的依赖清单检查/安装环境。
6. 打开“工作台”确认版本号，然后检查主题列表、历史论文库、报告、PDF 与 Zotero 配置。若工作台行为异常，可从完整备份恢复。

## 四、发布者必须完成的操作

更新检查依赖**GitHub Release**，因此每次正式发布都需要：

1. 同步调整 `paper_radar/__init__.py` 的版本号、`CHANGELOG.md`、`README.md` 和相关使用文档；源码 ZIP 里的版本也必须一致。
2. 运行单元测试、Python 编译检查、JavaScript 语法检查，并检查源码 ZIP 不含个人数据。
3. 合并正式代码到 GitHub 仓库，创建与版本一致的 Tag，例如 `v1.10.0`。
4. 进入仓库 Releases，创建**已发布的正式 Release**。不要只创建 Tag、Draft 或勾选 Prerelease。填写更新日志，上传源码 ZIP。
5. 在**未登录 GitHub 的浏览器**中确认 `https://github.com/QiQiWan/Paper-Radar/releases/latest` 可访问，并且显示的标签与 `v1.10.0` 一致。若仓库尚未公开、没有 Release 或创建了 Prerelease，程序不会推送这个版本。
6. 用低于最新正式版的旧版本程序进行一次模拟或实机检查，确认“发现新版本”以及跳转按钮可用。

更多信息请查看 [发布指南](PUBLISH_GITHUB.md)、[发布清单](RELEASE_CHECKLIST.md)、项目根目录 [CHANGELOG.md](../CHANGELOG.md)。
