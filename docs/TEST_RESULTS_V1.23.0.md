# Paper-Radar v1.23.0 离线测试记录

- 基线：v1.22.1（既有测试 389 项）。
- 本版本新增 `tests/test_v123_pdf_resume.py`：8 项专项测试通过，包括超长 PDF、旧索引迁移、表格行候选、模型多批次恢复、替换 PDF 清理检查点。
- 全量回归：`python -m pytest -q`，**397 passed in 78.08s**；退出码 0。日志由构建环境记录。
- `python -m compileall -q paper_radar tools` 与 `node --check paper_radar/static/portal.js`：通过。

## 尚需实机验证

测试中构造了长篇文档和本地合成表格，但并未下载/运行 FNO、U-FNO、PINO 原始 PDF 来衡量实验页召回率；未完成真实云端模型 API、Windows、正式 systemd+Nginx 负载/故障注入或第三方 OCR 的验收。32 MiB 和 120 页是配置及代码限制，不能视为所有大文档都可在 90 秒解析成功。启用可选 PyMuPDF/Tesseract 前需检查其安装、运行和许可证。
