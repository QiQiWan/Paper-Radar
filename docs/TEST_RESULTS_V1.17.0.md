# Paper-Radar v1.17.0 质量验收报告

- 测试基线：v1.16.0 254 项测试通过。
- 增量：新增 `tests/test_v117_durable_portal.py`，覆盖队列持久化、并发、幂等恢复、权限、数据库升级、分页、科研比较诊断和 arXiv 来源节流等 28 项测试。
- 完整实测：**282 / 282 项自动化测试通过**。从 `Paper-Radar_v1.17.0_Source.zip` 独立解压后的 282 项回归再次通过（约19秒）。
- 编译和静态检查：`compileall`、桌面与公网 JS 的 `node --check` 均通过；`python -m paper_radar.agents doctor` 返回16个 Agent、零异常。
- 发布资产：`tools/package_release.py` 的内置更新归档验证及 ZIP CRC 检查通过；使用仅存在于临时目录的测试 Ed25519 私钥对 v1.17 ZIP 进行签名与核验通过，临时私钥和签名未分发，不构成正式 Release 签名。
- arXiv 节流：专项测试验证真实适配器调用时写入持久的最近访问时间；串行与末次请求时间窗口仍需在真实网络环境验收。
- 测试方法：`python -m pytest -q`、`python -m compileall -q paper_radar main.py startup.py`、`node --check` 和 `python -m paper_radar.agents doctor`。
- 接口：真实本地 `ThreadingHTTPServer` + HTTP 请求，覆盖自助注册、按账号访问论文、搜索任务入队/结果/取消、管理员汇总权限、CSRF 和 Origin 校验。
- 恢复：模拟进程中断后从数据库恢复，达到尝试上限后停止；单账号重复入队被拒绝且不额外扣除滚动配额。
- 比较：核查未验证的用户导入证据、重复记录、缺失实验条件、不同基线和单位冲突时保留人工复核提示。

**环境限制：** Chromium 对本地 HTTP 和 `file://` 均返回 `ERR_BLOCKED_BY_ADMINISTRATOR`；没有据此声称通过浏览器实测。前端使用 HTML 控件契约测试、静态资源检查及 `node --check`。真实 Windows BAT/PowerShell 更新、真实 GitHub 已签名 Release、Nginx HTTPS 和公网压力测试尚需部署环境验收。

## 需在目标公网环境补齐的验收

1. 至少两名普通账号、一个管理员，运行24小时任务与并发压测。
2. 使用真实 arXiv 网络访问模拟超时、429、断网、服务器重启及磁盘写入错误，确认重试/恢复的正确性。
3. 验证可信 Nginx 覆盖 `X-Real-IP` 后按真实访客 IP 限流，错误代理配置时保持安全默认。
4. 验证 HTTPS Cookie 属性、账号禁用后的会话撤销、任务隔离与管理员统计；检查登录攻击监测和备份恢复。
5. Windows 目标机器运行 `tools/windows_update_acceptance.ps1`，使用正式签名测试 Release 验收启动更新和回滚。
