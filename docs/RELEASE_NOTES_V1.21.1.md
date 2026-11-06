# Paper-Radar v1.21.1 — 已确认缺陷修复

本次版本保留 v1.21.0 数据库结构和现有端点，新增只读配额接口。

- `deploy/nginx-paper-radar.conf`：仅 `/api/public/pdf` 上传路径允许 13m 请求体，后端对 PDF 的 12 MiB 限制不变；其他请求继续限制 400k。前端对反代返回 HTML/413 提示友好错误。生产环境仍需执行 `nginx -t` 并重载。
- `synthesis.py`：明确区分“原文引文经过当前 PDF 校验”“文件来源”“出版身份已核实”。公开 PDF 与用户上传 PDF 的准确引文都计为已校验，但上传者的出版身份依然需要人工确认。CSV 导入仍不可信。
- `portal_jobs.py`：检索与完整科研任务共享检索额度，单篇 PDF 分析及研究报告分别限额。新增鉴权只读 `/api/public/quotas`，并保持云模型实际调用的独立额度。
- `portal_research.py` 与 `portal.js`：云分析异常保留已建立的 PDF 索引；任务结果显式标记 `state=partial`，附带分析失败原因和可重试建议，不误称“没有实验结果”。旧有外层 SQLite `success` 枚举保持兼容，UI 以业务结果 `partial` 为准。

新增测试：`tests/test_v1211_fixes.py`。本次不涉及生产签名、真实云 API 或 Windows 目标机验收。
