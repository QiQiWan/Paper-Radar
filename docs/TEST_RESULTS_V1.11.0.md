# Paper-Radar v1.11.0 验收记录

日期：2026-11-04｜基线版本：v1.10.0｜新版本：v1.11.0

## 测试环境

- Python 3.13.5，pytest 9.0.2，Node.js v22.16.0，Chromium 144.0.7559.96（Debian 测试容器）。
- 源代码项目兼容声明为 Python 3.10+，本次没有在 Windows、macOS 和 Python 3.10 环境逐项实机测试。

## 已执行与结论

1. `python -m pytest -q`：**143 项通过**，包含 v1.10.0 原始 123 项及 v1.11.0 新增 20 项专项回归。
2. `python -m compileall -q main.py paper_radar`：通过。
3. `node --check paper_radar/static/app.js`：通过。
4. 启动真实 Python HTTP 服务后请求 `/api/health`：返回应用标记和 `1.11.0` 版本号。
5. 使用真实前端 HTML、CSS 与 JavaScript 加载 Chromium 进行离线渲染：1440px 桌面和 390px 移动端未发现横向溢出、未记录 JavaScript pageerror；检验工作台、设置页及新控件存在。
6. 新专项回归包括旧库迁移、跨主题数据隔离、反馈排序、模型证据/错误类型校验、主题缓存、推荐服务超时、语义来源模拟 HTTP、配额错误、主题批量成功/失败及历史记录内容最小化。

## 界面验证边界

当前运行环境中的 Chromium 被策略禁止直接访问 `127.0.0.1`，因此 HTTP 服务与浏览器渲染分开进行；前端使用离线模拟 API 检查 DOM 与布局。**这不能代替 Windows 电脑上真实 UI 对后端的端到端测试。** 对于模型调用、出版社 API、OpenAlex 实际额度、真实 Zotero 本地 API、Windows 计划任务和 PDF 下载，均应在用户实际设备上完成验收。

## 出版前检查

- 源码包排除 `settings.json`、`data/`、`downloads/`、`reports/`、`logs/` 等用户数据。
- 校对 `pyproject.toml`、`paper_radar/__init__.py`、前端、README 和更新日志版本号。
- 发布 GitHub 正式 Release 后，从旧版客户端核对更新检查提醒。
- 以真实论文样本建立推荐质量标注集，测量 Precision@10、Recall@50 和 NDCG@10，避免将测试通过等同于推荐效果已经提升。
