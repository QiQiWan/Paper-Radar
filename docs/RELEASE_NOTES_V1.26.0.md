# Paper-Radar v1.26.0 — 桌面发行基础与科研工作流改进

## 新增

- **桌面运行管理器**：`paper_radar/desktop_app.py` 默认启动 Tk 窗口，管理本机服务的启动/停止/重启、自动打开浏览器、健康状态、当前科研阶段、进度百分比及最近日志；同一系统用户重开窗口可以安全接管原服务，受保护的管理请求拒绝非本用户身份。
- **用户数据隔离**：原生 PyInstaller 包将设置/论文库/报告/日志保存到系统个人数据目录，程序文件夹可以保持只读。源码模式继续使用原目录；旧数据迁移需人工确认。
- **三平台构建**：提供 Windows GUI EXE＋控制台 sidecar、Ubuntu `.deb`/便携式目录、macOS ARM/Intel `.app` 的 GitHub Actions 原生构建流水线和 Linux DEB 组包脚本。原生可执行安装包、签名、公证和三个平台真机验收尚未在当前环境完成。
- **公共来源缓存和限流**：arXiv/OpenAlex/Crossref 按规范化检索配置、UTC 日期哈希、来源类型缓存元数据；跨进程共享 SQLite，正结果 TTL 分别为 1/1/2 小时，空结果最多 5 分钟，过期后网络中断允许有标记的 24 小时旧缓存候选。原有来源锁、至少 3—4 秒的公网请求间隔和硬超时保持有效，429/503 遵循有界重试退避。
- **研究工作台精简**：减少首屏统计卡与操作说明，研究主题编辑移至“我的账号”，桌面和手机视口测试覆盖创建/修改主题与任务入口。
- **真实论文评估工具**：增加精确的论文+页面+方法+数据集+指标+数值联合核验，重复预测计入精度分母、PDF 哈希和候选页片段诊断；附双人独立空白标注模板。缺失真实 PDF 时所有涉及真实解析的指标保留未测量。

## 兼容与升级

- 公网账号与工作区数据库迁移版本与 v1.25.0 一致，工作区仍为 v8；没有新增破坏性表结构迁移。
- 原生桌面包的程序更新必须走平台原生包升级，原源码自动更新器不能直接替换原生 EXE/APP。
- 旧源码版数据不会自动迁入用户目录，升级前需停服务备份，按照 [桌面安装手册](DESKTOP_PACKAGING_V1.26.0.md) 搬迁。
- 公网版可继续按原 Web + Worker 的受控部署方式运行，缓存降级会在来源诊断中标记为旧结果。

## 未完成的生产验收

- Windows 10/11、Ubuntu、macOS ARM/Intel 的原生包构建、双击交互、签名/公证、自动更新、卸载保护数据和 GUI 自动化真机验收。
- 真实 arXiv/OpenAlex/Crossref 在线成功率与 429 限流应答的长期实测。
- 真实论文 PDF 获取、复杂表格提取误差、人工双标一致性和跨论文可比正例评估。

详细操作见 [桌面说明](DESKTOP_PACKAGING_V1.26.0.md)、[真实论文评估](REAL_PAPER_EVALUATION_V1.26.0.md) 和 [测试报告](TEST_RESULTS_V1.26.0.md)。

- Native PyInstaller research worker subprocess starts using a bundled binary entry point rather than the CPython `-m` option. GitHub macOS Apple Silicon runner updated to the supported `macos-15` image.
