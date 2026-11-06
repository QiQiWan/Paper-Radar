# Paper-Radar v1.25.0 | 升级、回滚和部署验收

## 先完成备份

本次**工作区数据库由 v7 升级为 v8**，迁移不可通过简单替换旧版代码逆转。升级或回滚前必须停止所有 Web 和 Worker 进程，并用备份命令备份整个公网数据根目录（账号库、用户工作区、PDF 和模型账本）；同时保留桌面本地 `data/`、`reports/`、`downloads/` 和 `settings.json`。

```bash
# 在停止 Web 和全部 Worker 后执行；根据真实路径替换示例目录
python -m tools.public_backup --data-root /srv/paper-radar/data --output-root /srv/paper-radar/backups
```

升级前确认源版本、数据库完整性、磁盘可用空间、生产环境配置和可用的回滚快照。不要将密钥、用户 PDF 和数据目录打包到公开源码 Release 中。

## 升级过程

1. 停止公网 Web 和全部科研 Worker；备份数据库、用户 PDF 和密钥配置。
2. 解压 `Paper-Radar_v1.25.0_Source.zip` 到新的安装目录，使用 SHA-256 核对完整性；不要直接覆盖正在运行的源码目录，也不要混合复制新版与旧版包内文件。
3. 保留原 `PAPERRADAR_PORTAL_DATA`、公网域名、可信代理 IP 配置、HTTPS 证书和服务账号权限。使用项目原 `requirements.txt` 安装依赖；复杂 PDF OCR 等可选模块自行安装并核查授权。
4. 首先启动单个 Web、一个独立 Worker，首次访问账号工作区会以追加式迁移升至 v8；确认 `/api/public/health` 报告 `1.25.0`。此时旧源码不可直接安全回退。
5. 检查用户可登录、旧主题仍为 arXiv 单来源、旧阅读进度与历史报告未变化。新建测试主题，主动开启 OpenAlex 和 Crossref，执行检索并核对逐来源成功和失败状态。关闭任一来源后确认下一次检索遵守新设置。
6. 测试待阅读筛选、待复核论文跳转、已复核记录状态同步、历史任务安全重试（云端任务应拒绝），按账号导出 CSV 并确认跨账号不能访问。
7. 验证 Nginx 反向代理真实客户端 IP、上传限制、systemd 只读源码与可写用户数据目录、Worker 租约恢复、磁盘容量及备份恢复演练后，逐步放开用户。

## 离线自检命令

```bash
python -m pytest -q
python -m compileall -q paper_radar tools
node --check paper_radar/static/portal.js
python -m tools.verify_public_deployment --smoke
# 安装 Playwright 与 Chromium 后可运行；不访问外部网站
python -m tools.v125_offline_ui_check --screenshot /tmp/v125_ui.png
```

`v125_offline_ui_check` 使用模拟 API，在离线浏览器中覆盖主题操作、工作台跳转、移动端溢出及前端异常。真实 HTTP、浏览器 Cookie、TLS、下载和反向代理需另行在部署设备上测试。

## 回滚

如升级后需要恢复 v1.24.0：先停止全部 v1.25.0 Web/Worker，恢复升级前的**整套数据库/PDF 快照**，再恢复旧版本代码；不可直接以 v1.24.0 读取 v8 工作区库。SQLite 数据库恢复前确保没有进程持有文件锁；恢复后检查账号、历史报告和 PDF 指纹匹配。

## 未完成的生产门槛

- 目标 Windows 自动更新及生产私钥签名验收。
- 公网 TLS、Nginx/systemd 多用户并发、限流与受控故障注入。
- 真实 OpenAlex/Crossref/arXiv 查询成功率、源限流策略、服务方配额与公开题录变更。公网 OpenAlex 默认不继承主机密钥；仅管理员在评估计费与限流后才能显式设置 `PAPERRADAR_PUBLIC_ALLOW_OPENALEX_API_KEY=1`。
- 真实论文 PDF OCR、复杂表格抽取 F1 和跨论文比较错误率。
