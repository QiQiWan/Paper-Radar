# Paper-Radar v1.19.0 发布说明

本次升级将公网 Web 和科学计算 Worker 分为两个独立进程，新增 SQLite 任务租约、心跳、受限子进程执行和 Worker 运行监测。沿用账号与 PDF 数据结构，在 `portal_auth` 数据库执行追加式 v5 迁移；升级前务必停机备份账号及用户工作区。

**启动顺序**：先启动公网 Web，再用 `start_public_worker_macos_linux.sh` 或 `start_public_worker_windows.bat` 启动科研 Worker，两个服务配置相同的数据根目录。未启动 Worker 时，科研任务将保持排队，界面和管理员统计可用于诊断。

新增三篇真实论文的来源、固定 PDF 版本和实验数据种子基准；可在能访问 arXiv 的机器上使用 `python tools/eval_real_papers.py fetch`、`score` 生成真实解析质量报告。源代码发行包不包含原始论文 PDF 或用户资料。

**限制**：当前 Linux 的进程级内存/CPU 限额不等于每账户 OS 沙箱；Windows 系统级内存隔离及生产服务器负载压力未验收。此包尚未由生产签名私钥重新签署，不能直接作为现有客户端可信自动更新附件。
