# Paper-Radar v1.49.2 验证记录

日期：2026-11-10。版本：1.49.2。

## 1. 测试范围

- 已有历史回归测试按文件分组执行。具体结果以本轮最终记录为准，若历史测试与后续启动保护不一致，更新断言的同时增加拒绝旧版本连接的测试。
- 新增 `tests/test_v1492_scientific_ux_and_reliability.py`：覆盖模型 JSON 容错、自由文本恢复、已知中文研究术语回退、模型认证错误保留、中英题录召回、摘要补取、固定 API 域名、研究候选事实边界、日志脱敏与 HTTP 摘要接口。
- 本地浏览器数学可视化使用 Chromium，合成材料含 `$E=mc^2$`、`\\(R^2\\)`、上下标、分式、根号及偏微分表达；动态卡片全部通过 DOM MathML 检测并留存页面截图。
- HTTP 诊断接口验证 `summary.json` 可下载，接口返回字段不泄露密钥和请求内容。

## 2. 本轮实际通过结果

| 测试范围 | 结果 |
|---|---|
| 历史及新增项目测试分片 A | **442 passed**（85.05 秒） |
| 历史及新增项目测试分片 B | **441 passed**（98.53 秒） |
| 30 篇真实 PDF 基准约束测试 | **4 passed**（固定元数据与防泄漏检查） |
| 上述互不重叠范围合计 | **887 passed / 887 collected** |
| 本轮核心模块再次运行 | **27 passed**（模型规划、科研分析、HTTP 诊断及源数据补取） |
| Chromium 本地阅读卡片数学排版 | 6 个 MathML 公式显示检查通过 |
| Chromium 公网文献卡片数学排版 | 2 个 MathML 公式显示检查通过 |
| Python 编译、前端 JS 和 Shell 语法 | 全部检查通过 |

说明：887 项用例按照文件分片及基准目录分次完成，未在同一 Python 进程中连续运行全部测试。首次测试发现一项历史启动器用例仍以旧版服务可连接为前提，现已按当前的版本保护安全要求更新该断言，额外补充拒绝旧版本连接的测试；版本号并发更新时产生的旧值断言也已排除。最终分片未再出现失败。

## 2. 仍需真实环境验证

- 本轮未使用用户真实模型 API Key，也没有发送真实计费模型请求。
- 本轮未对用户原始 34 篇论文的实际数据库开展自动补摘要，因为用户上传的是诊断包和研究简报，没有论文数据库。
- 未开展正式的 LaTeX 完整语法覆盖率评估、跨浏览器字体兼容测试或多平台打包验收。
- 未完成真人可用性研究或独立的科学事实准确率测量。

## 3. 推荐的本机验证命令

```bash
python -m pytest -q tests/test_v1492_scientific_ux_and_reliability.py tests/test_v191_http_ai_plan.py tests/test_v140_research_inquiry.py tests/test_v1491_runtime_diagnostics.py
python -m compileall -q paper_radar
```

以上不会调用真实收费模型。Linux/macOS 可使用 `./start_macos_linux.sh`，Windows 使用 `start_windows.bat`；本机正式使用仍按 README 提供的正常启动步骤执行。
