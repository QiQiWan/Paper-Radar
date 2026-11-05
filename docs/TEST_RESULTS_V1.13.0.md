# Paper-Radar v1.13.0 测试与审查记录

**检查日期：2026-11-04**  
**基线：v1.12.0 源码**  
**目标版本：v1.13.0**

## 1. 自动化测试

- v1.12.0 原有测试：155 项通过，作为无回归基线。
- 新增 `tests/test_v113_agents.py`：16 项，包含注册表字段验证、依赖循环、输出冲突、失败注入、继续执行、运行轨迹、单/批量任务状态、HTTP 接口与 UI 标识。
- 最终全库：**171 项通过**。
- Python 编译与 JavaScript `node --check`：通过。
- `python -m paper_radar.agents list/graph`：九个流程 Agent 可列出，依赖图可输出。

## 2. 端到端验证范围

- 使用模拟来源数据，通过旧版 `core.run_cycle` 入口检索并实际生成 Word、Markdown 和 CSV；确认 `agent_run` 状态与 SQLite 轨迹记录。
- 当文献来源报错时，不能导出“本次成功”的报告；轨迹记录 `failed`。
- 允许 AI 排序等非关键 Agent 失败时，Storage / Report Agent 仍可继续；轨迹记录 `partial`，上层 Web 和批量任务能够读取。
- `GET /api/agents` 通过真实本地 HTTP Handler 测试，不要求模型 API Key。
- 使用实际前端 HTML/CSS/JavaScript 和模拟接口响应在 Chromium 进行桌面 1400px、手机 390px 页面检查：九个 Agent 显示和刷新通过、无脚本异常、无横向溢出。

**浏览器验证边界：** 当前执行环境禁止 Chromium 直接连接 localhost，因此浏览器测试使用模拟 HTTP 接口响应；真实 HTTP Handler 另以 Python 的本地 HTTP 测试验证。两项测试共同覆盖代码路径，但不能替代 Windows 工作站和真实外部 API 的最终验收。

## 3. 仍需补做的验证

1. 实际 Windows 定时任务及 Zotero Local API 在不同桌面版本下的运行效果。
2. 真实 DeepSeek / OpenAI 等模型在网络超时、Token 额度不足及费用限制下的行为。
3. 官方 arXiv、OpenAlex、OpenReview 与出版社接口的真实覆盖率、配额、权限及失败场景。
4. 大规模文献库、多主题任务长时间运行的耗时、磁盘增长率与 Agent Trace 清理。
5. 模型推荐的科研质量；尚无真实人工标注数据支持准确率改善声明。

## 4. 发布要求

升级之前应完整备份 `settings.json`、`data/`、`downloads/`、`reports/` 与 `logs/`。源码包应排除这些用户数据目录。创建 GitHub 正式 Release 后，旧版本客户端的更新提醒才能显示 v1.13.0。
