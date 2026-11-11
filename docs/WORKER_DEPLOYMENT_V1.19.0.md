# Paper-Radar v1.19.0｜独立 Web 与 Agent Worker 部署、恢复及验收

**结论：生产环境建议使用两个独立服务。** 公网 Web 进程负责账号认证、API、任务排队与查询；科研 Worker 独立领取任务，在一次性子进程中执行 PDF、模型与报告任务。Web 服务内不再启动任务线程（开发环境可以显式选择 `--embedded-workers`）。

## 1. 实际架构与边界

`Nginx(HTTPS) -> public_portal (loopback HTTP) -> accounts.sqlite3(portal_jobs)`

`portal_worker (1..N 个独立服务) -> SQLite 原子领取/租约/心跳 -> 单任务子进程 -> 独立用户工作区`

- SQLite `BEGIN IMMEDIATE` 保证一次领取时只有一个 Worker 获得任务。`lease_token`、`lease_until` 和 `heartbeat_at` 保证旧 Worker 的最终确认不能覆盖新执行者的结果。
- 租约令牌只通过父子进程私有 stdin pipe 传递，不进入命令行或临时文件名；过长的 JSON 结果会被显式拒绝，避免截断成无效 JSON。
- Worker 每 5 秒心跳（默认），租约有效期 45 秒，执行时间预算 150 秒；请求取消或进程超时后会终止任务子进程。Linux 在子进程中设置 CPU/内存/文件描述符及文件大小限制，并尽可能使用父进程死亡信号；Windows 使用超时终止，但**当前未提供等效的强制内存隔离**。
- 丢失租约的检索/无付费分析可以按上限重试；可能造成收费或重复下载的长任务会标记失败，等待人工确认后重新提交。任务运行语义为 **at-least-once + 有条件重试**，不能保证第三方 API 的 exactly-once。
- 各用户 SQLite 与 PDF 路径按认证 ID 隔离；当前 Worker 运行在同一操作系统用户下，**没有严格的系统级多租户文件隔离**。更高安全级别须采用不同 UID 或容器和专门的任务 broker。
- 仅推荐单主机本地文件系统 SQLite。不要通过 NFS 共享同一个任务数据库让多台主机协作；多服务器场景应迁移 PostgreSQL 或可靠的消息队列。

## 2. Linux 启动（可自由指定 Web 端口）

升级前停止 Web 和 Worker，备份 `/var/lib/paperradar/portal`（包含账号 SQLite、用户 SQLite、PDF）。确认 v1.19.0 安装成功后分别启动：

```bash
export PAPERRADAR_PORTAL_DATA=/var/lib/paperradar/portal
export PAPERRADAR_PUBLIC_ORIGIN=https://papers.example.com
PYTHON=.venv/bin/python sh ./start_macos_linux.sh --public 9009
```

另一个终端执行：

```bash
export PAPERRADAR_PORTAL_DATA=/var/lib/paperradar/portal
PYTHON=.venv/bin/python sh ./start_macos_linux.sh --worker
```

两个进程必须指向 **相同的 `PAPERRADAR_PORTAL_DATA`**；在线更新程序应在两个服务都已停止后才可执行。Nginx 只反代 Web 端口 `127.0.0.1:9009`，不要暴露 Worker 端口（Worker 本身不监听 HTTP）。

## 3. systemd 生产配置

项目提供 `deploy/paperradar-public.service` 与 `deploy/paperradar-worker@.service`。按实际项目路径、域名及端口更新后，使用：

```bash
sudo cp deploy/paperradar-public.service /etc/systemd/system/
sudo cp deploy/paperradar-worker@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now paperradar-public.service
sudo systemctl enable --now paperradar-worker@1.service paperradar-worker@2.service
sudo systemctl status paperradar-public.service paperradar-worker@1.service
sudo journalctl -u 'paperradar-worker@*' -n 50 --no-pager
```

请确保 systemd 两个服务使用相同的受限用户 `paperradar`，且 `/var/lib/paperradar/portal` 可读写。Worker 的 `MemoryMax`/`CPUQuota` 仅为示例配置，需结合服务器物理资源核定。后台 Worker 可以单独重启，Web 登录通常不受影响。

## 4. Windows 开发与实机验收

```powershell
$env:PAPERRADAR_PUBLIC_ORIGIN = 'http://127.0.0.1:9009'
$env:PAPERRADAR_PORTAL_DATA = 'D:\PaperRadarData\portal'
.\start_windows.bat --public 9009
```

在新的 PowerShell 中运行 `start_windows.bat --worker`。请验证排队、取消、后台更新、服务终止和 Worker 重启恢复。Windows 上的 CPU/内存限制需要通过 Job Object 或其他宿主控制补齐，目前仅实现进程终止能力，不能视为完全的 Windows 资源沙箱。

## 5. 上线验收步骤

1. **健康检查**：访问 `/api/public/health`（具体返回以 Web 内置健康接口为准），确认登录保护、安全头以及反向代理 HTTPS。管理员查询 `/api/public/admin/jobs`，其中 `live_workers` 应大于零。
2. **空队列**：只启动 Web，不启动 Worker，提交任务后应保持 `queued`，不会偷偷在 Web 线程执行。
3. **多 Worker**：启动两名 Worker，用不同测试账号提交两项 `synthesize`；结果各自完成，`attempts` 为 1。
4. **故障注入（仅测试库）**：终止一个 Worker，等待租约过期；无付费任务应在尝试次数限制内重新排队，付费或长研究任务应转为失败并提示人工确认。
5. **租约过期**：向数据库注入过期令牌，验证旧 Worker 不能对同一任务写入成功状态。
6. **资源预算**：以可控测试任务验证运行时间到期后任务子进程被终止，不留持续占用资源的遗留进程。
7. **恢复性与数据保护**：备份账号和个人工作区后，进行服务重启、禁用账号、取消任务、PDF 替换、报告重建测试。
8. **负载试验**：在目标服务器使用模拟/公开数据做 2 小时运行验证，记录排队耗时、成功率、进程 RSS、CPU、SQLite busy、API 延迟和无效重放次数，再决定是否扩大 Worker 数量。

## 6. 未解决的生产级风险

- 同一宿主用户下的 Worker 子进程仍可访问其 UID 有权限的其他文件；隔离来自软件访问约束与 CPU/内存/时间限制，缺少每租户 OS 沙箱。
- 独立进程在崩溃窗口内发生外部 API 副作用时，任务可能执行过但没有成功确认；付费任务因此默认不自动重放。
- Web 与 Worker 均为本机 SQLite 模式；超多并发和跨服务器扩展需引入独立消息队列或 PostgreSQL。
- 本轮 Linux 子进程、SQLite 竞态及模拟外部任务已测试；**目标服务器高并发与 Windows 实机内存沙箱未在当前环境完成验收**。
