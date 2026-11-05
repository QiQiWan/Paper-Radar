# Windows 自动更新实机验收步骤（v1.16.0）

当前自动化测试和 Linux 系统运行只证明组件行为，**尚未在 Windows 实机完成完整升级验收**。仓库提供 `tools/windows_update_acceptance.ps1` 供用户在 Windows 10/11、Python 3.10+ 环境运行。

## 1. 验收前准备

关闭所有 Paper-Radar 窗口及计划任务，备份整个项目目录和真实 `settings.json`、`data/`、`reports/`、`downloads/`，在独立测试副本中完成升级。安装依赖：`python -m pip install -r requirements.txt` 和 `python -m pip install pytest`。首次 v1.15.0 → v1.16.0 应手动复制程序文件，包括更新启动器与固定公钥信任库。

## 2. 离线验收

```powershell
powershell -ExecutionPolicy Bypass -File tools/windows_update_acceptance.ps1
```

脚本检查 Python 环境、Ed25519 签名与篡改拒绝、数据库兼容和迁移、账号和 CSRF、Agent 入口以及 Windows 下的离线启动流程。结果写入 `reports/windows_update_acceptance_<时间>.txt`，失败会返回非零状态。

## 3. 真实 Release 检查

先在官方 GitHub 仓库发布测试/正式三件套 Release，确保 ZIP、manifest、signature 完全一致，再执行：

```powershell
powershell -ExecutionPolicy Bypass -File tools/windows_update_acceptance.ps1 -Online
```

该模式会查询 GitHub 是否能提供正式 Release 元数据；如果线上版本未超过本机版本，仍需要使用**旧版测试副本**模拟升级。请依次确认：新版本被识别；下载与签名/摘要校验；源码和个人数据库备份；新版本启动；配置与历史论文未丢失；故意破坏签名时拒绝安装；故意破坏启动文件后可回滚，重启后不会重复错误升级。原脚本**不自动制造真实新版本或篡改生产库**，这组生产级场景由人工/测试机器执行并记录截图及日志。

## 4. 验收记录模板

记录机器系统版本、Python 版本、原版本与目标版本、Release Tag、附件 SHA256、升级耗时、签名校验结果、回滚证据、用户数据抽样核对及人工签名。结果通过后方可宣称“Windows 实机自动更新已验收”。
