# Paper-Radar v1.19.0：独立科研 Worker、租约隔离与真实论文评估——设计与审查

## 1. 结论与交付边界

v1.18.0 公网 Web 服务内部执行科研任务，HTTP 生命周期、耗时 PDF 解析、云端模型调用与任务恢复相互影响。本轮将 Web 改为纯入队/查询服务，新增单独部署的科研 Worker，任务在独立子进程执行。SQLite 任务增加 lease_token、lease_until、heartbeat_at 与 resume_allowed，保证租约的领取、续租及最终确认可追溯，旧执行者不能用过期令牌确认任务。

学术评估建立三个真实公开论文固定版本及六条经人工核对的实验表格数值，使用 SynthesisAgent 实际生成跨论文证据报告，并对五组已知不可比的关系检查误归组。受执行环境下载限制，原始 PDF 解析准确率与模型抽取 F1 尚未实测。评估工具提供在联网电脑上可复现的下载、解析、核查及评分命令。

## 2. 生产级组件及权限

| 组件 | 负责 | 不允许的工作 |
|---|---|---|
| Web/Nginx | HTTPS、登录、权限、API、排队、查看任务、后台管理摘要 | 直接解析 PDF 或计费模型调用 |
| SQLite 任务队列 | BEGIN IMMEDIATE 原子领取、租约与恢复、用户级任务历史 | 保证对第三方副作用的 exactly-once |
| Worker Supervisor | 注册心跳、排队领取、租约续期、超时/取消、进程管理 | 将租约令牌传入命令行参数 |
| Task Child | 按任务调用 Portal.search 或 research_job、写入本用户结果 | 任意调用其他账号的路由；无限资源使用 |
| 用户工作区 | 用户独立 SQLite、PDF、结果报告 | 跨账号的路径寻址 |

单主机 SQLite 适用于低并发、受控科研门户。建议 Worker 1—2 个并行实例，避免文献来源 API 限速与 SQLite 写锁竞争。多服务器或高并发转为 PostgreSQL + 分布式任务队列，不可把 SQLite 文件放在 NFS 共享盘上进行跨机器竞争。

## 3. 租约状态机

`queued` → `running(worker_id, token, lease_until)` → `success/failed/cancelled`。领取和写回依赖原子事务。Supervisor 每隔若干秒用 token 续租；失去所有权、中断或被取消时停止任务子进程。旧 token 的回写无效。

`recover()` 只回收已过期的 `running` 任务；续租正常的任务不会被 Web/其他 Worker 重排。`search` 和不产生模型付费的 `synthesize` 等任务达到上限前可重试；`research` 和已启用云端模型的 `analyze` 默认停止自动重放。因为外部 API 写入/计费与数据库事务无法原子提交，这仍是 **at-least-once、有条件重试** 的系统，不能声称完全一次执行。

新增额外保护：租约 token 通过 Worker 与子进程之间的私有 stdin pipe 传递，进程参数和结果文件名不包含 token；输出 JSON 超长时明确标记失败，不截断为无法解析的 JSON。

## 4. 资源与多租户隔离

Supervisor 对单任务子进程设置墙钟时间预算、取消检查与进程组终止。Linux 子进程使用 RLIMIT_AS、CPU、FSIZE、NOFILE 与父进程死亡信号作为第一层限制；systemd 提供 MemoryMax、TasksMax、CPUQuota 和文件访问保护。Windows 支持任务子进程超时终止，但 Windows Job Object 级内存/CPU 限制仍待补充。

**风险仍在：** 多个租户的 Worker 子进程当前运行在同一系统 UID 下，共享宿主文件权限；进程级限制不等于安全沙箱。对任意不可信 PDF，应继续使用单独容器、无特权 UID、只读根文件系统、禁外网/限制网络和每租户数据挂载。发布前必须在目标 Linux 系统上做恶意 PDF、内存耗尽、僵尸进程和主机文件越权测试。

## 5. 数据库与回滚

`portal_auth` 添加 v5 迁移，保留原账号、会话和已有队列，新增 `portal_workers` 状态表与任务租约列。升级前停止 Web 和所有 Worker，并使用原有 `tools/portal_backup.py` 做 SQLite 一致性备份及 PDF 摘要检查；数据库迁移仅前向追加，若旧程序不识别新 schema 不得直接启动。回滚必须连同数据库备份一起恢复。

## 6. 真实公开论文种子基准

- FNO（arXiv:2010.08895v3）：Navier–Stokes 2D 测试 FNO-3D、FNO-2D 实验值。
- U-FNO（arXiv:2109.03697v3）：CO₂ 多相流前沿预测误差。
- PINO（arXiv:2111.03794v4）：Darcy Flow 表格中有/无数据约束的误差。

固定版本的出处、页码、原文局部文本和六条人工标注位于 `benchmarks/real_papers_v1/`。评分入口 `tools/eval_real_papers.py`：

1. `fetch` 下载原 PDF（仅供合法评估；不随源码分发）。
2. `score` 调用当前受限 PDF 解析器核对数值和方法词所在物理页；缺 PDF 时将 PDF 相关指标标记为 null / 未测量。
3. 生成 `real_paper_synthesis.md` 与 `real_paper_eval.json`，使用真论文人工标注做结构化报告，且把证据标为“人工导入、尚未服务器 PDF 校验”。
4. `--predictions <json>` 计算独立模型抽取的精确匹配 P/R/F1；没有预测文件时不产生假分数。

本轮人工数据指标为：3 篇论文、6 条实验结果、5 个不可比负例，输出报告保留 6/6 条原文引文与页码、没有把 5 个负例错误归成跨论文直接比较候选。由于不存在真实可比正例，这项负例检查不能用于推断 SynthesisAgent 的召回率或整体学术可靠性。真实 PDF 解析和 LLM 实验抽取尚未在线运行。

## 7. 科研质量正式验收计划

建议建立至少 30 篇真实论文、每篇 3—5 条人工标注的第二阶段评估集，并引入两位审阅者独立标注，报告 Cohen's kappa/一致率。必须分别覆盖：文献版本和 DOI 一致性、PDF 解析页码定位、实验表格字段抽取、误差数值归属、指标单位与方向、数据集/划分/训练预算可比性、跨论文结论是否有证据、引用链接可用性。

至少需要 20 对真正可比的正例与 20 对不可比负例，以构成比较任务的最小可检验集；正例应在人类专家核对完全一致的协议后确定。输出单独报告 Precision、Recall、F1、误比较率、可引用结论占比、模型单位成本，以及复杂 PDF 解析失败原因，禁止把“未生成错误比较”作为唯一准确性指标。

## 8. 部署验收、风险与限制

- 运行 Web 但不运行 Worker：任务保持 queued。启动两个 Worker：用两个账号提交任务，应分别领取并完成，租约不重复。
- 网络/SQLite/Worker 失联：只恢复已过期任务；付费任务提示需人工确认。
- 终止任务子进程：CPU、RSS 和打开文件资源最终回收；用户可看到明确失败原因。
- 运行压力：按 1、2、4 个 Worker 逐步负载测试，监控 P95 HTTP 延迟、排队等待、SQLite busy、资源峰值、完成率与重试次数。
- 正式公网 TLS、Web 认证压力、安全扫描、Windows Job Object 与自动更新验收均应在目标环境完成。当前不作生产上线已验收的声明。
- 自动升级必须使用原有可信 Ed25519 生产私钥签署发布附件；本轮源码 ZIP 没有这把签名，不能作为直接自动安装的生产更新包。
