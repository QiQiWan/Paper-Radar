# Paper-Radar v1.19.1 → v1.20.0 升级指南

本次增加公网五入口工作台、账号主题级阅读状态、桌面任务中心。**升级前必须先停 Web 和全部 Worker，并备份数据。**

1. 在 Linux 使用 systemd 时先停止 Worker 与 Web 服务；Windows 请关闭公网服务与后台 Worker。若正在执行付费模型任务，先等待正常结束。
2. 备份 `PAPERRADAR_PORTAL_DATA` 完整目录：至少 `accounts.sqlite3`、`users/`、论文 PDF、研究报告和所有 SQLite 文件。桌面版备份 `settings.json`、`data/`、`downloads/`、`reports/`。建议保留旧源码目录，备份须可读取。
3. 在原程序目录部署新代码，保留以上用户数据；安装项目依赖。不要把新的空数据库覆盖原数据。
4. 使用原有端口配置启动 Web 与独立 Worker。公网启动示例：`PAPERRADAR_PUBLIC_ORIGIN=https://papers.example.com PYTHON=.venv/bin/python sh start_public_macos_linux.sh 9009`；Worker 另开独立服务，使用相同 `PAPERRADAR_PORTAL_DATA`。
5. 新版首次访问用户工作区时，`portal_workspace` 会从 v3 迁移到 v4，创建 `topic_paper_flags`。账号库 `portal_auth` 仍为 v6，桌面文献数据库无破坏性变更。
6. 用现有账号登录：确认原主题、论文数、报告快照仍在；标记一篇星标、设为在读，刷新页面确认已保存；换主题确认标志独立；打开历史报告确认不会重新生成。
7. 在 390px 手机视口检查底部导航和论文库；用第二账号确认不能读取第一账号主题、星标、报告及 PDF。

### 发生问题时

立刻停止 Web 与 Worker，然后**同时**恢复旧源码与升级前的数据备份；避免用 v1.19.1 旧代码直接读取已经迁移到 v4 的用户数据库。不要只回滚代码而继续使用新结构数据库。

### 自动更新与签名

本地交付 ZIP 尚未由仓库持有者用**生产 Ed25519 私钥**签名。旧客户端的可信自动安装需要 GitHub 正式 Release 按协议提供源码 ZIP、发布清单 JSON 与签名文件，并且必须匹配已内置的受信任公钥。测试包不得冒充正式可自动更新版本。

### 尚未完成的验收

真实 Windows 启动与自动回滚、目标域名 HTTPS/Nginx、高负载并发、真实模型计费与学术 PDF 长时间运行，都必须在实际运行环境分别验收。参见 `docs/TEST_RESULTS_V1.20.0.md`。
