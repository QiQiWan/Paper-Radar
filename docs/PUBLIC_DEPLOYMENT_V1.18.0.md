# Paper-Radar v1.18.0 公网部署、账号与科研任务使用指南

## 1. 安装与准备

默认仅绑定 `127.0.0.1`，公网访问由 Nginx HTTPS 反向代理完成。建议使用专用、无特权系统用户；Python 3.10+、持久数据盘、独立备份盘。公网版不能直接代理桌面 `webapp.py` 服务。

```bash
cd /opt/Paper-Radar
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export PAPERRADAR_PUBLIC_ORIGIN=https://papers.example.com
export PAPERRADAR_PORTAL_DATA=/var/lib/paperradar/portal
PYTHON=.venv/bin/python sh start_public_macos_linux.sh 9009
```

指定端口可修改为任何本机未被占用的合法 TCP 端口，例如 `8899`、`9009`、`52000`。同步生成 Nginx 示例：

```bash
.venv/bin/python tools/render_public_nginx.py --domain papers.example.com --port 9009 --out ./paper-radar.conf
nginx -t
```

`server_name`、证书、proxy_pass 与网站实际端口须一致。Nginx 必须覆盖 Host 和 X-Real-IP 等代理请求头。仅在确认 Nginx 是唯一可信反向代理时配置 `PAPERRADAR_TRUST_PROXY_IP=1`。不要开放 Python 后端端口到公网。

## 2. 用户操作

1. 管理员先使用 `python -m paper_radar.public_portal --data-root /var/lib/paperradar/portal admin-create --username operator` 建立管理员，使用交互式密码输入。
2. 普通用户通过 HTTPS 注册、登录，创建研究方向并配置不超过 6 组检索词。
3. 单击“仅检索论文”仅获取元数据；单击“检索＋全文＋综合分析”会尝试获取最多 2 篇公开 arXiv PDF，建立原文索引，并生成当前主题的综合报告。
4. 默认云端分析关闭。如要提取实验指标，用户须勾选云模型授权，且服务器管理员配置受限模型。无云模型时原文索引仍可进行，报告正确显示证据不足。
5. 对单篇论文使用“上传 PDF”，只允许当前账号当前主题已经收录的论文；最大 12MiB，单用户最多 30 篇/180MiB，上传次数有限额。随后点击“分析全文”并查看每页证据。
6. 上传后核对原文的真实身份及版权。模型核验的是文字是否出现在上传文件中，无法验证该文件是否真的属于标记的论文。
7. 任务运行状态显示排队、运行阶段、完成/部分完成或失败。可申请取消；长时间外部请求在下一个安全检查点停止。
8. “跨论文实验比较”只列出具备可回溯原文的实验条件匹配候选；导入 CSV 记录仍单独标记未在服务器核验。

## 3. 服务器模型设置（可选）

```bash
export PAPERRADAR_PUBLIC_LLM_BASE=https://provider.example.com/v1
export PAPERRADAR_PUBLIC_LLM_MODEL=your-permitted-model-id
# 不要在公开 shell 历史、日志、README 或 Git 仓库里保存生产密钥
export PAPERRADAR_PUBLIC_LLM_KEY='...'  # 建议由 systemd EnvironmentFile/秘密管理器注入
```

公有模型供应商的接口应遵循其数据处理条款与实际 API 计费。公网版用户无法自设 API 地址；上传到云端的内容为受限 PDF 文字片段，全文文件默认不发送。系统对调用进行账号级限额，但外部服务实际费用需在供应商后台核验。

## 4. 本次升级和备份

升级前停止服务，完整备份 `PAPERRADAR_PORTAL_DATA`（`accounts.sqlite3`、`users/`、论文 PDF）；然后用新代码替换旧程序，保留数据目录。新版首次启动迁移 `portal_auth` v3→v4 和 `portal_workspace` v1→v2。

离线／本机安全检查：

```bash
python tools/public_acceptance.py --origin http://127.0.0.1:9009 --output acceptance.json
python tools/public_backup.py --data-root /var/lib/paperradar/portal --output-root /root/data/paperradar-backups
python tools/public_backup.py --verify /root/data/paperradar-backups/backup_YYYYMMDD_HHMMSS_xxxxxx
```

公网检查：将 `--origin` 改为真实 HTTPS 域名。此验收工具只做不需要登录的安全探测，不会自动生成帐号、攻击服务器或访问个人内容。备份工具使用 SQLite 在线备份 API 对数据库建立一致性副本，并为 PDF 建立文件哈希清单；恢复前必须停止服务，并在隔离环境验证备份清单、数据库以及用户数据。

## 5. 必须单独验收的生产条件

- Nginx TLS 证书、HSTS、证书自动续签、反向代理 Host 和 X-Real-IP、请求体大小限制以及公网端口关闭情况。
- 用户注册滥用、口令喷洒、验证码及密码找回；当前版本尚未实现邮件确认、验证码和密码找回。
- 系统异常退出和重启后的任务恢复；意外断电下 SQLite WAL、文件完整性和备份恢复。
- 大并发与恶意 PDF 压力测试；当前线程队列是受控单机方案，不保证对公开互联网的大规模负载安全。
- Windows 真实自动更新、回滚和正式 Ed25519 Release 发行，本环境未实机验证。

生产部署推荐先关闭自主注册进行灰度验收；完成运维安全检查后再向真实用户逐步开放。
