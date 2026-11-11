# Paper-Radar v1.52.1 测试结果

## 代码回归

- 连续全仓库回归：`python -m pytest -q --maxfail=1`，**931 passed in 186.57 s**，退出码 0。该结果包含既有功能及新增 v1.52 系列测试。
- 新增专项测试：`tests/test_v152_scope_jobs_and_acceptance.py`、`tests/test_v152_inquiry_http.py`，共 8 项；覆盖 72 篇模拟论文全部纳入 9 个 8 篇批次、智能推荐/手动选文差异、摘要更新快照失效、模型单批失败隔离、持久化任务续跑、摘要补全冷却，以及无真实数据时的准确空值。
- Python 模块编译：`python -m compileall -q paper_radar` 通过；JavaScript `node --check` 通过。
- 历史源码发布包遗漏 `paper_radar/reports/` 五个文件，已从已验证的 v1.51 源码工作树恢复，`paper_radar.report` 可以正常导入，完整回归通过；最终压缩包重新解压后，`paper_radar.report` 导入正常，15项研究分析及历史报告专项测试通过。

## 浏览器可用性（隔离合成数据）

- `python tools/qa_v152_browser.py /mnt/data/Paper-Radar_v152_UI_QA`
- Chromium 浏览器页面：桌面1280、手机390像素各两种状态（选文、分析结果），共 4 组；JavaScript 异常 0，横向溢出 0。
- 实测 UI 中可切换分析模式，筛选与手动调整论文、提交任务、刷新任务进度并自动跳转显示结果。测试采用隔离 HTTP/SQLite 及合成论文样本，不替代真实72篇的科学结论评估。

## 真实论文数据库：缺失

- 当前交付工作区没有用户当前使用的72篇 SQLite 论文数据库。无真实数据的验收程序返回 `actual_count=0`，PDF 获取率、字段正确率、人工审核耗时等不可测数据为 `null`，不会以 0% 冒充实测率。
- 交付一键实机 CLI `python -m paper_radar.research_acceptance --db PATH --topic default --expected-count 72 --mode all --output pilot.zip`，运行时输出选文覆盖、摘要质量、分批情况、总运行耗时、模型状态与能读取的 PDF/人工审核测量信息。

## 尚未覆盖

1. 用户现有72篇论文数据库的真实全主题分析与科学有效性人工评审。
2. 原机网络环境实际 PDF 下载率；复杂表格字段准确率及双人标注。
3. 有效云模型的完整批次调用、实际 token 用量/账单、限流/断网长周期运行。
4. Windows/macOS/Linux 多平台桌面程序安装与用户使用测试；公网版仍为原有分析范围。

测试状态：**研发验证版**，不得将模拟论文的覆盖测试当作真实科研评价结果。

## 交付包独立复测

- 交付源码 ZIP 包含 **604 个文件**；核对 `paper_radar/reports/` 五个文件齐全。
- 在解压后的 `Paper-Radar_v1.52.1/` 中验证 `__version__=1.52.1`，并执行 `test_v152_scope_jobs_and_acceptance.py`、`test_v152_inquiry_http.py`、`test_new_features.py`，共 **15/15 通过**。
- 解压版 Python 编译和前端 JS 语法检查通过。
