# Paper-Radar v1.22.1 公网部署升级、验证与回滚

适用：原有 v1.22.0 独立公网 Web + Worker，Linux Nginx / systemd 部署。执行者应拥有服务器权限，且了解备份和数据目录的实际位置。下面的路径是随源码提供的部署模板示例；部署前请与本机配置逐项核对。

## 1. 升级前：停服务并创建可验证备份

**结论：必须先停 Web 和所有 Worker，再备份。** 本版增加不可逆的工作区数据库迁移 v5，直接回退旧代码将无法打开迁移后的数据库。

```bash
sudo systemctl stop paperradar-public
sudo systemctl stop 'paperradar-worker@1' 'paperradar-worker@2'
# 如已启用更多 Worker，全部停止后再继续。

# 数据备份放在独立备份卷/目录，并控制权限。
sudo -u paperradar /opt/Paper-Radar/.venv/bin/python /opt/Paper-Radar/tools/public_backup.py \
  --data-root /var/lib/paperradar/portal \
  --output-root /var/backups/paperradar

# 将命令实际打印出的 backup_YYYY... 路径替换到下列校验指令。
sudo -u paperradar /opt/Paper-Radar/.venv/bin/python /opt/Paper-Radar/tools/public_backup.py \
  --verify /var/backups/paperradar/backup_YYYYMMDD_HHMMSS_ffffff
```

建议使用新建的、**仅供 paperradar 用户写入的备份目录**；若 `/var/backups/paperradar` 不可访问，先由管理员创建并设置适当所有权和权限。上述工具备份账号数据库、所有用户工作区 SQLite 及 PDF，并为文件生成 SHA-256 清单；它不备份所有日志或本地桌面数据。对整个数据目录的快照可额外归档日志与资源统计文件。

## 2. 安装源码及依赖

1. 记录原 `/opt/Paper-Radar` 代码版本和原部署模板文件，不要删除 `/var/lib/paperradar/portal`。
2. 解压审查过的 `Paper-Radar_v1.22.1_Source.zip`，覆盖 `/opt/Paper-Radar` **代码文件**，保留其 Python 虚拟环境和全部持久化数据。
3. 由具有部署权限的运维人员安装/更新依赖：`/opt/Paper-Radar/.venv/bin/python -m pip install -r /opt/Paper-Radar/requirements.txt`。
4. 将 `deploy/paperradar-public.service` 和 `deploy/paperradar-worker@.service` 安装到实际 systemd 单元位置，修改服务用户、域名、路径和 Worker 数量后执行 `sudo systemctl daemon-reload`。
5. 确认 systemd 使用 `StateDirectory=paperradar`、`ReadWritePaths=/var/lib/paperradar`、`ProtectSystem=strict`；生产服务含 `--trust-proxy-ip --disable-registration`。
6. 核对 Nginx 使用 `proxy_set_header X-Real-IP $remote_addr;` **覆盖**输入头，且 `proxy_pass` 只指向 `127.0.0.1:8899`。后端 8899 必须只监听 127.0.0.1，不允许公网开放。

注意：新服务模板默认**关闭自助注册**。已创建账号的登录、阅读状态和数据保留不变。需要新账号时使用安全终端：

```bash
sudo -u paperradar /opt/Paper-Radar/.venv/bin/python -m paper_radar.public_portal \
  --data-root /var/lib/paperradar/portal user-create --username NEW_USER
```

上述命令将在终端提示输入密码，**不要把密码放入命令行、Shell 历史或聊天消息**。

## 3. 启动与快速验收

```bash
cd /opt/Paper-Radar
/opt/Paper-Radar/.venv/bin/python -m tools.verify_public_deployment --smoke
sudo nginx -t
sudo systemctl start paperradar-public
sudo systemctl start 'paperradar-worker@1' 'paperradar-worker@2'
sudo systemctl --no-pager --full status paperradar-public 'paperradar-worker@1'

# 改成实际域名，验证 HTTPS、证书与反向代理。
curl -fsS https://papers.example.com/api/public/health
```

`--smoke` 仅使用临时目录并启动回环 Python 服务，不会修改线上账户，不会运行正式 Nginx 或 systemd。实际 `nginx -t`、TLS、证书、端口和 systemd 状态需运维人员在目标服务器执行。

## 4. 核心人工验收清单

| 检查 | 操作 | 预期结果 |
| --- | --- | --- |
| 反向代理 IP | 从两台不同公网客户端尝试登录无效账号并检查对应 `auth_attempts` | 登录限流按真实客户端 IP 独立记录；缺失/无效代理头拒绝操作 |
| 注册安全 | 访问登录页、尝试直接调用 `/api/public/register` | 生产页面隐藏注册页签，API 拒绝注册；CLI 创建用户可正常登录 |
| 只读部署 | 启动 Web、查询 `systemctl` 日志，查看数据目录 | 运行标记位于 `<data-root>/runtime/public_service.active`，不写源码目录 |
| 版本迁移 | 使用旧账号查看历史主题、已读/星标、PDF 与报告 | 数据仍在；用户工作区数据库迁至 v5，不清空状态 |
| 元数据刷新 | 使用正式 arXiv `updated` 新旧版本重复检索 | 新版更新题录；旧版不能覆盖；阅读状态保持 |
| 轮询恢复 | 打开任务中心，中断浏览器网络再恢复 | 显示自动重试，恢复后继续读取同一任务状态，不重复提交 |
| 科研证据 | 导入明显跨行错配的合成实验片段 | 错配被拒绝；其余仅引文匹配数据仍提示人工核查行列关系 |
| 日志脱敏 | 人为构造受控内部异常或查看测试产生的 `E_INTERNAL` | 用户只见请求错误编号，内部日志不包含原文、密码或令牌 |
| Worker 并发用量 | 并发执行测试模型计数 | 汇总调用次数不丢失，位于 `<data-root>/metrics/api_usage.json` |

## 5. 回滚原则

**本版数据库前向迁移不能仅靠旧源码回滚。** 如果需要恢复 v1.22.0，必须：停止全部服务，先将 v1.22.0 已审计源码恢复，再把**本次升级之前**保存的账号数据库、工作区 SQLite 和 PDF 恢复至原数据根目录，验证文件所有权和数据库完整性，最后启动旧服务。恢复会丢弃备份之后产生的新任务、报告、论文状态及账号操作。备份的 SHA-256 校验通过并不自动保证业务层恢复，建议定期在隔离环境进行全量恢复演练。

对于生产自动更新：v1.22.1 的桌面更新器会检查旧标记、数据目录新标记以及同安装目录的 Linux 服务进程，从而尽量防止服务仍在运行时热替换源码。管理员仍须按上述步骤先停全部服务，尤其是自定义数据目录、Windows 或多主机情形。本地源码 ZIP 不包含生产私钥签名。必须按仓库的 Ed25519 正式发布工作流签名后，才能进入可信自动更新流程。

## 6. 未覆盖验收

Windows 实机自动更新、目标服务器 systemd 生命周期、高并发恶意流量、第三方真实 LLM、真实论文大规模质量指标均需要后续单独验收。当前纯文本 PDF 的完整行列关系仍需人工审查，v1.23.0 规划结构化表格与页码/单元格定位。
