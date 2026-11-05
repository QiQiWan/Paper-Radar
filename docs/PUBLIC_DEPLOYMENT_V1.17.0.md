# Paper-Radar v1.17.0 公网部署与任务管理

## 1. 部署条件

支持受控公网功能集：自主注册、账号管理、主题与论文隔离、arXiv 限量检索、实验 CSV 导入和科研比较。原桌面版仍只提供本地服务，不允许直接反向代理桌面 Web。环境建议：Python 3.10+、受限 Linux 系统用户、HTTPS 域名、Nginx、持久数据盘与自动备份。

## 2. 从 v1.16.0 升级

1. 停止公网服务，保存 `accounts.sqlite3`、`users/` 全部内容的备份，并备份桌面 `settings.json` / `data/` / `downloads/` / `reports/`（如同机部署）。
2. 仅替换程序源码和公开文档，不覆盖 `PAPERRADAR_PORTAL_DATA`。
3. 核验新版本源码（正式 Release 必须用**已经信任的 Ed25519 发布公钥**验证）；使用虚拟环境安装/校验依赖。
4. 启动服务。首次启动将 `portal_auth` 数据库 schema 从 v2 迁移至 v3；不会清空旧账号或工作区。
5. 登录管理员、普通用户，检查运行历史、分页和重新检索。确认服务器日志、文件权限、备份策略与 Nginx 配置。

## 3. 启动示例：可指定端口

```bash
cd /opt/Paper-Radar
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export PAPERRADAR_PUBLIC_ORIGIN=https://papers.example.com
export PAPERRADAR_PORTAL_DATA=/var/lib/paperradar/portal
export PAPERRADAR_TRUST_PROXY_IP=1
PYTHON=.venv/bin/python sh start_public_macos_linux.sh 9009
```

脚本只绑定 `127.0.0.1:9009`。配置 `PAPERRADAR_PUBLIC_ORIGIN` 为浏览器实际访问的 HTTPS 域名。如果只在本机测试，可以改为 `http://127.0.0.1:9009`；此时默认不需要 `PAPERRADAR_TRUST_PROXY_IP`。

```bash
python tools/render_public_nginx.py --domain papers.example.com --port 9009 --out ./paperradar-9009.conf
```

检查证书文件路径、`server_name`、`proxy_pass`，使用 `nginx -t` 验证后启用。Nginx 必须显式设置 `proxy_set_header Host $host;` 和 `proxy_set_header X-Real-IP $remote_addr;`。**仅在可信 Nginx 覆盖 X-Real-IP 时设置 `PAPERRADAR_TRUST_PROXY_IP=1`**，否则保持默认关闭，禁止浏览器直接访问后端端口。

Windows 本机测试：设置 `PAPERRADAR_PUBLIC_ORIGIN=http://127.0.0.1:9009`，然后运行 `start_public_windows.bat 9009`。这不构成实际 Windows 自动升级及公网 HTTPS 验收。

## 4. 管理员初始化

```bash
.venv/bin/python -m paper_radar.public_portal --data-root /var/lib/paperradar/portal admin-create --username operator
```

密码经交互式输入，至少 12 字符；无需将密码写入脚本或环境变量。管理员可在网页停用账号，并查看待执行、正在执行、完成和失败任务的汇总统计；不会在此接口暴露用户论文全文。

## 5. 用户操作

登录后选择研究主题，点击“检索最新论文”。任务先进入服务器 SQLite 队列，当前页面显示排队位置与任务编号；运行中显示尝试次数。检索过程中可以继续浏览已有文献。点击“取消任务”会请求协作式取消，网络请求可能正在处理，需要到下一个安全检查点才会结束。

重启后重新登录，任务状态与最近任务记录会恢复。单账号只允许一个活动任务；总并发默认 2 个。每个账号滚动 24 小时最多提交 10 次，失败任务也计入已经提交的配额；重复请求或队列满而拒绝的操作不扣配额。论文库每页 30 篇，通过“加载更多论文”逐页浏览。

## 6. 备份、故障恢复与限制

- 通过受控账号权限定期备份 `PAPERRADAR_PORTAL_DATA` 下的 `accounts.sqlite3` 与完整 `users/` 工作区；大流量部署宜使用 SQLite 在线备份 API 或短暂停机一致性备份。不能只复制数据库主文件而忽略 WAL 文件。
- 已经开始的 idempotent arXiv 检索在进程中断后最多允许一次自动恢复；多次中断后标记失败，人工查看任务记录。后台线程无法强制中止任意阻塞式第三方库。
- 该版本仅支持单进程 worker，**不得对同一数据目录并行启动多套服务实例**；不具备跨机器分布式锁和工作节点租约。后续增长到较多用户，应迁移 PostgreSQL + 任务消息队列，并分离 Web 与 Worker 进程。
- 全部提交与失败信息保留在 `portal_jobs`；90 天以上的已结束记录在后续提交新任务时清理。不要将账号数据库、PDF、密钥、日志或 `.paper_radar_updates` 目录加入 Release。
- 自主注册需要谨慎上线：当前尚未提供邮箱验证、找回密码或验证码。应在 Nginx 加请求速率控制、部署 TLS/HSTS、监测 CPU/内存/磁盘并使用逐步开放注册的发布策略。

## 7. 发布检查

- `python -m pytest -q`
- `python -m compileall -q paper_radar main.py`
- `node --check paper_radar/static/portal.js`
- `python -m paper_radar.agents doctor`
- 管理员和两个普通账号分开测试注册、会话、数据隔离、队列、取消、迁移、CSV 导入及报告。
- 真实域名检查 Cookie Secure、Host / Origin 保护、Nginx `X-Real-IP` 覆盖、断网恢复和恢复备份。

正式发布仍需使用已有可信公钥对应的离线签名私钥签署 v1.17.0 Release；仅生成本地 ZIP 不会触发旧客户端自动更新。

### 可选队列参数

可通过 `PAPERRADAR_JOB_WORKERS=2`（1—4）、`PAPERRADAR_MAX_PENDING_JOBS=100`（1—1000）和 `PAPERRADAR_DAILY_SEARCH_LIMIT=10`（1—100）调整本地线程与配额。也可直接向 `python -m paper_radar.public_portal serve --workers 2 --max-pending 100 --daily-search-limit 10` 传参。调大限制之前，需确认 arXiv 官方接口速率限制、服务器资源与实际网络能力；并发 worker 并不意味着可以并行突破上游 API 的访问规则。

真实公网 arXiv 访问在所有 worker 之间串行化，并通过持久限流键至少间隔四秒；将 `PAPERRADAR_JOB_WORKERS` 调大只能改善任务管理和其他工作，不会提高 arXiv API 请求速率。服务器应检查真实 arXiv 接口是否有额外访问规定。
