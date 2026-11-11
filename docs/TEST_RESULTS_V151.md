# v1.51.0 测试结果与边界

## 新增测试

- `tests/test_v151_abstract_first_inquiry.py`：无 PDF 与无 AI 的摘要分析，按原文追踪研究线索、模型引用片段绑定、阻止无依据数字、摘要补全避免覆盖、审计不保存密钥、简化 UI 和授权。
- `tests/test_v151_abstract_http.py`：本地 HTTP/SQLite 摘要质量接口、后台任务创建、CSRF 保护、非法参数拒绝、试点评估导出及空分母 `null`。
- `tests/test_v1501_real_pilot_audit.py` 和 `tests/test_v1501_pilot_http.py`：人工字段标签、计时、证据版本、可比性组、ZIP 导出。
- `tools/check_inquiry_v151_ui.py`：Chromium 在 1280px 与 390px 下检查研究分析和文献卡片，无脚本异常或横向溢出。

## 真实论文与本地数据

- 本次上传的代码包 `papers` 表为 0 条，不包含用户当前终端的 60 篇真实论文。
- `pilot_references/` 记录 11 个公开 PDF 的网页可读性状态和两个论文的人工表格参考数据。这些可作为评测素材，但**不进入“软件实测 PDF 下载率”或“自动字段准确率”**。
- 用户人工审核耗时与有效对照组数必须在当前数据库上经人工确认。系统的空值属于未取得真实测量。

## 最终回归结果

- 完整源码测试：`python -m pytest -q`，**923 passed in 177.59s**（一个连续运行过程）；失败 0。
- 启动入口：根目录仅 `start_macos_linux.sh` 和 `start_windows.bat`；试点评估命令放置于 `tools/`。
- HTTP/SQLite 摘要补全/CSRF/试点统计：新增联动测试通过；空分母保持 `null`。
- Chromium 1280/390px：研究分析和卡片共 4 份截图，无横向溢出，数学排版测试通过。
- `node --check`：前端 JavaScript 语法检查通过；`python -m compileall`：通过。
- `data/papers.sqlite3` 当前论文 0 条；无法在本环境统计用户 60 篇论文的真实下载/人工准确率。

### 回归中曾发现并修复的两处问题

1. 旧版规定根目录仅保留两个正式启动脚本。将试点导出脚本迁移到 `tools/`，更新相对路径后对应测试通过。
2. 精简后的研究页面曾遗漏“可靠的实验比较结论须核对 PDF”提示。已经恢复该科学证据边界，相关测试通过。
