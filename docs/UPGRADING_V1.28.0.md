# v1.28.0 升级与回滚操作

升级前停止本机 Web、独立 Worker 和定时任务，并备份 `settings.json`、`data/`、`downloads/`、`reports/`、`storage/` 及公网账户数据库；保留 `.paper_radar_updates/`。首次从 v1.27.0 升级可使用完整源码包替换程序文件，但需保留个人数据和用户设置。

升级后用浏览器确认 `/api/health` 返回 `1.28.0`，首次运行引导和 `/guide/quickstart` 能正常打开；在设置中保存前后台模式，退出重新运行确认生效。手动更新只能从可信的正式 GitHub Release 触发；升级辅助进程会先核对发布文件再关闭旧服务，日志在 `.paper_radar_updates/`。

如果更新失败，升级器会尝试回滚并重启旧版。不要删除更新辅助目录；应先检查 `handoff_status.json` 和 `handoff.log`。若原版本无法恢复，停止进程后从更新前的源码和数据备份恢复。公网数据库过去的迁移仍遵循各版本升级说明。
