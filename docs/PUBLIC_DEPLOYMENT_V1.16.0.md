# Paper-Radar v1.16.0 公网门户部署及账户管理

## 1. 服务边界

公网版是一个**独立的安全入口**，不把桌面版 `webapp.py` 暴露给互联网。支持自主注册、登录退出、账号停用、密码修改、各账号独立研究主题和论文库、arXiv 限量检索、导入 v1.14 实验证据 CSV、跨论文比较 Markdown 下载。共享代码，用户工作区 SQLite 位于不同随机账号 ID 目录中。

**暂不开放**桌面版 Zotero、密钥设置、PDF 本机下载、Windows 计划任务、后台程序更新、上传任意 PDF 和所有本地文件操作接口。账号数据不会与桌面工作台自动同步。公网导入的实验证据被标识为“用户导入，云端未复核原始 PDF”。公网版不等于本地版全部功能上线。

## 2. 本地测试（Windows）

```bat
set PAPERRADAR_PUBLIC_ORIGIN=http://127.0.0.1:8899
start_public_windows.bat 8899
```

浏览器访问 `http://127.0.0.1:8899`。更换端口时，同步调整 `PAPERRADAR_PUBLIC_ORIGIN` 和 BAT 参数。例如使用 9009：

```bat
set PAPERRADAR_PUBLIC_ORIGIN=http://127.0.0.1:9009
start_public_windows.bat 9009
```

Windows 启动脚本会优先复用桌面版 `.venv`，首次没有虚拟环境时自动创建并安装依赖。

## 3. Linux 公网部署

建议 Python 3.10+、独立低权限系统账号、Nginx、有效 TLS 证书。先用系统 Python 创建虚拟环境并安装项目依赖；将运行数据独立放在 `/var/lib/paperradar/portal`，不要放进 Git 仓库。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export PAPERRADAR_PUBLIC_ORIGIN=https://papers.example.com
export PAPERRADAR_PORTAL_DATA=/var/lib/paperradar/portal
PYTHON=.venv/bin/python ./start_public_macos_linux.sh 8899
```

可以按部署端口自动生成 Nginx 配置，例如：

```bash
python tools/render_public_nginx.py --domain papers.example.com --port 9009 --out ./paperradar-9009.conf
```

此命令只生成配置草案，不会修改系统 Nginx 文件。检查域名、证书路径以及后端端口后，将配置安装到 Nginx 站点目录。也可手动编辑 `deploy/nginx-paper-radar.conf` 的域名、TLS 证书及后端端口。执行 `nginx -t` 并重载配置。放行服务器入站 443，**不要开放 8899 到公网**。服务端校验 Host 和 Origin，Nginx 须保留原始 Host。可参考 `deploy/paperradar-public.service` 创建 systemd 服务（修改路径、User、证书域名和端口）。

## 4. 管理员与自助注册

先通过服务器终端运行：

```bash
python -m paper_radar.public_portal --data-root /var/lib/paperradar/portal admin-create --username operator
```

密码用交互式提示输入，12—128 字符，使用 scrypt 和独立随机盐保存摘要。普通用户可在登录页自主注册。管理员登录后可查询账号列表、停用或重新启用账号；停用即时清除已登录会话。注册开关可通过 `serve --disable-registration` 关闭。

每个账号使用独立 SQLite 工作区，不接受前端提供的文件路径。会话保存在服务器端，仅存随机会话令牌的 SHA-256，浏览器 Cookie 使用 HttpOnly、SameSite=Lax；HTTPS 部署还使用 Secure。所有写操作要求同源 Origin + 自定义头 + 登录后 CSRF Token。

## 5. 安全和配额

- 单次联网检索只访问 arXiv 公共 API，返回上限 20 条；单账号一天最多发起 10 次，服务器同时最多运行 2 个检索任务。
- 单次导入 CSV 不超过 250 KB、500 条实验记录。仅接受固定列结构，不上传任意 PDF 或可执行文件。
- 注册按连接来源设置频率限制；服务最多 1000 个账号、每账号最多 30 个主题。公开大规模部署之前建议增加邮件验证、机器人验证、登录风险审计、每日 IP 总额度与外部数据库（如 PostgreSQL）。
- 不应把 `.env`、签名私钥、账号数据库和 `storage/portal/` 提交到 Git 或打包发给用户。对服务器数据进行独立备份。
- 公网服务不自动热更新；发布新版本请先做快照、验证签名并安排维护窗口重新启动。

## 6. 后续需补充的线上验收

使用真实域名验证 HTTPS、Cookie Secure、双用户互不可见、账号禁用/重置后会话失效、Nginx 413 与频率限制、断网处理、服务重启，以及高并发下的负载。当前属于受控公网功能集；在未增加邮箱验证和反机器人机制前，不建议面向不受控制的大量公众开放注册。
