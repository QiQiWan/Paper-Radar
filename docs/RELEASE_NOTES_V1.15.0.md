# Paper-Radar v1.15.0 正式发布说明

发行日期：2026-11-04。以 v1.14.0 为基线。

本次重点改变正常启动方式：启动时先用标准库快速检查固定 GitHub 仓库的正式 Release；如发现高版本且附件 SHA-256 和源码结构校验均通过，将先备份程序与有界 SQLite 数据库，再自动安装和启动新程序。断网、超时、无公开 Release、下载异常和磁盘空间不足时保留现有版本。新程序依赖安装或健康检查失败后自动恢复旧程序和升级前数据快照。

新增 `startup.py`、`paper_radar/startup_update.py`、版本发布工具，以及“启动前自动更新”设置项。保留 v1.14.0 的13个 Agent、研究主题、Zotero、PDF 全文与实验数据证据处理。已验证的既有核心行为无回归。

首次安装：v1.14.0 尚无法自行下载并安装 v1.15.0，需要关闭应用后手动升级一次源代码及 BAT/Shell 启动脚本。以后正式发布的兼容版本才会自动安装；仅创建 Git 标签或上传非标准名称的附件不会触发升级。GitHub 发布者需在正式 Release 中上传 `Paper-Radar_v1.15.0_Source.zip` 并核验 GitHub API 的 `sha256:` 摘要。

启动控制：

- Windows：双击 `start_windows.bat`；单次跳过更新：`start_windows.bat --no-update`。
- macOS/Linux：`sh start_macos_linux.sh`；单次跳过更新：`sh start_macos_linux.sh --no-update`。
- 上次更新记录：`python -m paper_radar.startup_update --status`。
- 手动回退源代码：`python -m paper_radar.startup_update --rollback-last`（须先关闭工作台；保留用户最新数据）。

验证：原有199项测试与新增31项测试均通过；最终源码ZIP独立解压复测230/230通过；后端HTTP版本及设置接口验证通过；桌面与移动端使用实际HTML/CSS/JS、离线数据完成布局和控件测试。真实GitHub发布下载、Windows BAT/macOS/Linux环境仍需要正式发布后的实机验收。
