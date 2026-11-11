# 当前版本 v1.52.1

- [本版研究分析范围、批次、操作与72篇实机验收](INQUIRY_SCOPE_V1521.md)
- [本版测试结果](TEST_RESULTS_V1521.md)

- [摘要优先研究分析、摘要补取及真实文献库试点](ABSTRACT_FIRST_RESEARCH_V151.md)
- [本版测试与边界](TEST_RESULTS_V151.md)

## v1.50.0 历史研发版本

- [PDF 批量证据流程：操作方式、模型授权、页码追踪和科研边界](EVIDENCE_PIPELINE_V150.md)
- [测试记录与待验证事项](TEST_RESULTS_V150.md)

## v1.49.3 历史文档


- [研究分析结果分层、论文卡片与分阶段日志审阅](UX_AUDIT_V1493.md)
- [测试与验证边界](TEST_RESULTS_V1493.md)
- [研究分析分阶段诊断日志说明](RUNTIME_DIAGNOSTICS_V1493.md)

## v1.49.2 历史文档

- [本次阅读卡片、摘要、模型检索与研究分析的修改及审阅](UX_AUDIT_V1492.md)
- [新增错误类型、日志汇总与导出](RUNTIME_DIAGNOSTICS_V1492.md)
- [本轮代码及验证记录](TEST_RESULTS_V1492.md)
- [面向使用者的完整操作说明](USER_GUIDE.md)
- [真人可用性试用任务（仍需实施）](USABILITY_PROTOCOL_V1490.md)

## v1.49.0–v1.49.1 历史文档

- [模型向导与论文阅读改版](UX_AUDIT_V1490.md)
- [旧版测试结果](TEST_RESULTS_V1490.md)
- [v1.49.1 运行日志和异常诊断](RUNTIME_DIAGNOSTICS_V1491.md)

# Paper-Radar 文档总览

## v1.26.0 历史版本（安装与发行参考）

- [桌面安装和运行手册](DESKTOP_PACKAGING_V1.26.0.md)：Windows、Ubuntu、macOS 双击启动目标、用户目录、管理器和构建验收边界。
- [真实论文评估方法](REAL_PAPER_EVALUATION_V1.26.0.md)：固定论文、双人标注模板、指标定义与未测量原因。
- [发行说明](RELEASE_NOTES_V1.26.0.md) · [测试验收报告](TEST_RESULTS_V1.26.0.md)。

## v1.20.0 历史版本

- [完整功能架构与 UX 审查](DESIGN_V1.20.0.md)：公网五入口页面、桌面任务中心、按主题阅读状态、账号边界、浏览器审阅、遗留项。
- [五入口工作台操作手册](PORTAL_GUIDE_V1.20.0.md)：从登录到论文筛选、任务执行、报告阅读的完整流程。
- [升级与数据库迁移](UPDATING_V1.20.0.md)：停机备份、工作区 v3→v4、回滚和签名发布限制。
- [自动化与多视口测试](TEST_RESULTS_V1.20.0.md)、[发布说明](RELEASE_NOTES_V1.20.0.md)。

- [v1.19.0 独立 Worker 与科研评估完整审查](DESIGN_V1.19.0.md)
## v1.19.0 历史文档

- [独立科研 Worker 运行与上线验收](WORKER_DEPLOYMENT_V1.19.0.md)
- [真实学术论文 PDF 评估集与质量指标](REAL_PAPER_EVALUATION_V1.19.0.md)
- [v1.19.0 发布说明](RELEASE_NOTES_V1.19.0.md)
- [v1.19.0 测试说明](TEST_RESULTS_V1.19.0.md)

## v1.18.0 历史文档

- [完整安全审查及多用户工作流设计](DESIGN_V1.18.0.md)
- [公网部署、任务操作、备份与安全检查](PUBLIC_DEPLOYMENT_V1.18.0.md)
- [v1.18.0 发布说明](RELEASE_NOTES_V1.18.0.md)
- [本轮自动化及系统检查](TEST_RESULTS_V1.18.0.md)
- [历史版本更新记录](../CHANGELOG.md)

---

## v1.17.0 历史版本

- [公网持久化任务、科研比较诊断与独立 Agent 边界](DESIGN_V1.17.0.md)
- [公网账号、端口、Nginx、任务队列及升级使用说明](PUBLIC_DEPLOYMENT_V1.17.0.md)
- [版本发布说明](RELEASE_NOTES_V1.17.0.md)
- [完整变更记录](../CHANGELOG.md)

---

## v1.16.0 历史版本

- [完整设计、实现审查与风险边界](DESIGN_V1.16.0.md)
- [公网账号系统、可配置端口及 Nginx 生成](PUBLIC_DEPLOYMENT_V1.16.0.md)
- [数据库迁移及备份恢复](MIGRATIONS_V1.16.0.md)
- [Windows 更新真机验收脚本使用方法](WINDOWS_UPDATE_ACCEPTANCE_V1.16.0.md)
- [v1.16.0 发布说明](RELEASE_NOTES_V1.16.0.md)
- [v1.16.0 测试报告](TEST_RESULTS_V1.16.0.md)
- [Release 发布与离线 Ed25519 签名](PUBLISH_GITHUB.md)
- [全版本更新日志](../CHANGELOG.md)

---

> **新版启动自动更新：** 请先阅读 [v1.15.0 启动、备份与回滚](UPDATING.md) 和 [发布者附件规范](PUBLISH_GITHUB.md)。

## 其他文档索引

## 版本与升级

- [v1.14.0 科研实验分析 Agent 设计与验收](AGENT_ARCHITECTURE_V1.14.0.md)：四阶段按需分析、结构化实验指标、逐字引文核验、PDF 版本指纹及数据导出。
- [v1.14.0 发布说明](RELEASE_NOTES_V1.14.0.md)：功能变化、升级兼容与真实环境验收边界。
- [v1.14.0 自动化测试与浏览器检查](TEST_RESULTS_V1.14.0.md)：详细验证方法与未覆盖场景。

- [v1.13.0 Agent 模块化完整设计](AGENT_ARCHITECTURE_V1.13.0.md)：九个实际 Agent、固定依赖编排、失败隔离、运行轨迹和后续计划。
- [Agent 独立研发清单](agents/README.md)：每个 Agent 的下一阶段研发任务、负责模块与验收指标。
- [v1.13.0 发布说明](RELEASE_NOTES_V1.13.0.md)：面向普通用户的功能变化与兼容性说明。

- [v1.12.0 全文证据精读与评估完整设计](DESIGN_V1.12.0.md)：实际功能、数据流、证据边界、评价方案和路线。
- [v1.12.0 实施审阅与验收](REVIEW_V1.12.0.md)：实际修改、限制和验收记录。
- [完整更新日志（CHANGELOG）](../CHANGELOG.md)：v1.12.0、v1.11.0 及历史版本的逐项变更记录。
- [软件更新检查与安全升级](UPDATING.md)：自动检测、手动检查、官方 GitHub Release、备份和迁移。
- [v1.11.0 完整设计方案](DESIGN_V1.11.0.md)：现状、技术架构、后续研发与量化验收。
- [v1.11.0 审查与验收](REVIEW_V1.11.0.md)：实施修改、测试与外部验证边界。
- [v1.10.0 历史审查与验收](REVIEW_V1.10.0.md)：功能说明、测试记录、局限性和后续路线。

## 入门

- [完整操作手册](USAGE.md)：安装、检索、进度、分类、下载、报告、后台任务、排错。
- [Zotero 连接图文教程](ZOTERO_QUICKSTART.md)：开启本地通讯、读取收藏夹、关键词。
- [出版社来源和权限](SOURCES_AND_ACCESS.md)：arXiv、OpenAlex、Crossref、Springer、Scopus、ScienceDirect。
- [模型 API 配置](API_PROVIDERS.md)：以 DeepSeek 为主，介绍豆包、OpenAI、Claude 等。
- [研究报告](REPORTS.md)：综述、优先阅读、Markdown 预览和导出字段。

## 开发与发布

- [模块架构](ARCHITECTURE.md)
- [新增检索来源](ADDING_SOURCES.md)
- [新增 PDF 下载器](ADDING_DOWNLOADERS.md)
- [本地 API](API.md)
- [GitHub 发布方法](PUBLISH_GITHUB.md)
- [发布检查清单](RELEASE_CHECKLIST.md)
- [迭代审阅与下一步](IMPROVEMENT_PLAN.md)
- [公众号文章](PUBLIC_ARTICLE.md)

项目主页：https://github.com/QiQiWan/Paper-Radar · © 2026 @EatRice

- [v1.9.0 全部优化审阅项](IMPROVEMENT_PLAN.md)
- [真实界面截图补拍清单](REAL_SCREENSHOTS.md)

- [v1.15.0 启动自动更新架构](STARTUP_UPDATE_ARCHITECTURE_V1.15.0.md)
- [v1.15.0 发布说明](RELEASE_NOTES_V1.15.0.md)
- [v1.15.0 测试记录](TEST_RESULTS_V1.15.0.md)
