# v1.23.0 升级、配置与验收

**升级前先停服务并备份。** 此版本的工作区数据库由 v5 升至 v6，旧二进制无法读取新 schema。建议先在隔离环境恢复备份、完成回滚演练，再操作正式服务。

1. 停止 Web 和全部 Worker。执行 `python tools/public_backup.py --data-root <portal-data> --output-root <backup-dir>`，并用同一工具的 `--verify` 校验备份。示例命令和安全权限要求请参见 [v1.22.1 升级手册](UPGRADING_V1.22.1.md)。
2. 解压更新后的源码至原安装目录，**不要覆盖数据目录**，保留系统自定义环境变量、证书及服务配置。执行 `python -m pip install -r requirements.txt`。
3. PDF 表格解析可选安装：`python -m pip install 'PyMuPDF>=1.24,<2'`；必须自行确认第三方许可证适用性。不安装时按正文解析降级。OCR 可选部署 Tesseract 二进制，并将 `PAPERRADAR_PUBLIC_PDF_OCR=1` 配置给 Worker 的环境变量；**默认关闭**。
4. 部署 `deploy/nginx-paper-radar.conf` 的 PDF 上传路径配置，核对该 location 的 `client_max_body_size 34m`，全局仍保持 400k。执行 `sudo nginx -t`，确认服务仅监听本地地址、TLS 安全和真实客户端 IP 配置。
5. 重新启动 Web 和 Worker。检查 `/api/public/health` 的版本返回 `1.23.0`。旧 PDF 用户重新发起分析时会补建解析档案和表格候选，已保存的论文、报告及阅读进度应保留。
6. 依次验证：30 MiB 以内合法 PDF 上传、65 页论文检索、后半部分候选页、云模型多批失败后只调用未成功批次、覆盖同一论文新 PDF 后旧证据清空、跨用户资料不可见。
7. 生产测试结束前不要宣布正式放行：还需验证真实 Windows、Nginx/systemd 多 Worker、代理网络中断、模型调用配额与恶意 PDF 资源耗尽控制。

**回滚：** 停 Web/Worker → 恢复旧代码 → 使用升级前备份恢复账号与工作区全部文件 → 校验文件权限和 SQLite 完整性 → 启动旧服务。单独回滚代码会触发新版数据库拒绝旧版访问，不能作为有效回滚。生产自动更新仍需正式 Ed25519 私钥签名流程。
