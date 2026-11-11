# Paper-Radar v1.45.2｜真实 PDF 证据定位与正式评估准备

2026 年 11 月 10 日。研发验证版；未创建 GitHub 正式 Release，也没有完成全部科研模型的正式质量验收。

## 1. 修复范围与可复现结果

原有 117 条机器候选引文采用 PyMuPDF 布局排序方式产生，但应用原先使用 pypdf 原生文本顺序建立索引，并利用 `search_for` 直接高亮。双栏、表格和公式邻近文本排序不同，导致 39 条在 pypdf 中不能连续匹配。

在固定的 30 篇 arXiv 原版 PDF、逐个 SHA-256 校验后，v1.45.2 实测：

| 指标 | 结果 | 界限 |
|---|---:|---|
| 上版 pypdf 连续引文 | 78/117 | 文字层不同排序 |
| 本版布局排序连续引文 | 117/117 | 原文存在，仅文本级匹配 |
| 可映射原始 PDF 几何位置 | 114/117 | 仍有 3 条文字定位成功、坐标歧义 |
| 原先 39 条断裂中找到文字 | 39/39 | 恢复全文定位 |
| 原先 39 条中坐标完整映射 | 36/39 | 3 条保留人工核查 |
| 公式上下文候选 | 47 | 规则识别，未证明公式语义 |
| 真实表格页专项测试 | 3 页 6 张规则表 | 32 个单元格位置，未核验数值关系 |

新增 `/api/fulltext/locate` 双解析器降级逻辑、`/api/fulltext/tables` 行列和坐标候选、`/api/fulltext/highlight` 对布局匹配的高亮支持，页面限制对齐 240 页；高亮失败时直接跳转 PDF 原始页并提示人工核查。数值来源、表格行列、公式含义全部保留“未确认”状态。复杂矢量图纸页面的表格检测设限，避免单次解析阻塞应用。

## 2. 一键运行

使用此前已有的 Python 环境：

```powershell
python -m pip install "pymupdf>=1.24,<2"
```

Windows 双击 `start_windows.bat --eval`；Linux / macOS 执行 `sh ./start_macos_linux.sh --eval`。它依次执行 PDF 校验下载、引文几何定位、真实表格行列候选、统一模型评估计划和人工金标准就绪检查。默认 **0 次模型 API 调用、0 美元模型开支**。

详细独立命令：

```powershell
python -m tools.eval_realpdf30_geometry --dataset benchmarks/real_pdf30 --pdf-dir benchmarks/real_pdf30/pdfs --output realpdf30_geometry_v1452
python -m tools.eval_realpdf30_tables --dataset benchmarks/real_pdf30 --pdf-dir benchmarks/real_pdf30/pdfs --output realpdf30_tables_v1452
python -m tools.eval_realpdf30_models --mode plan --pdf-dir benchmarks/real_pdf30/pdfs --geometry realpdf30_geometry_v1452/geometry_candidates.jsonl
python -m tools.eval_realpdf30_models --mode check-gold --pdf-dir benchmarks/real_pdf30/pdfs --geometry realpdf30_geometry_v1452/geometry_candidates.jsonl
```

## 3. 人工标注与仲裁

本评估包含 **117 条实验候选、139 道科研问题、34 组跨论文配对**，模型实际评测组织为 117 抽取 + 88 单篇问答 + 34 方法比较 + 17 冲突检测，共 256 项。

推荐由两名具有论文研读能力的审核者分别独立填写：

- `annotations/review_A.csv` 与 `review_B.csv`：对每条候选是否有真实数值关系、论文原文、方法和数据集进行独立核查，写清改正后的值及引文；不完整填“needs_recheck”。
- `tasks/qa_review_A.csv` 与 `qa_review_B.csv`：确定题目的可回答性、原文引用和人工标准答案。
- `tasks/pair_review_A.csv` 与 `pair_review_B.csv`：对两篇论文的数据集、指标、工况是否可比，以及是否存在真实冲突独立判断。
- 发生分歧时由第三名审核者核查后填入相应 `adjudicated` 文件，避免以模型答案仲裁。
- `annotations/human_review_attestation.json`：填写两位不同的真实人工审核者编号和审核完成时间；默认值为空且标明未审核。

审核应在模型输出前完成；模型运行时锁定以上文件的 SHA-256。一个完整的科研结果还需在模型回答生成后再进行一轮独立回答评分。

目前双人审核完成量为 **0**。自动候选和助手单轮核查不能计入人工金标准。运行 `--mode check-gold` 应返回 `ready=false`，且不调用云模型。

## 4. 统一模型、Prompt、预算

模型协议模板位于 `benchmarks/real_pdf30/model_protocol_v1452.json`，所有任务固定相同：

- 唯一 `api_provider` / `api_base_url` / `api_model`（必须由用户按当前服务商配置填写）。
- 固定 `prompt_version`、System Prompt 和 4 个结构化任务指令的内容 SHA-256。
- 固定 `temperature=0`、`max_output_tokens=900` 和调用超时；实际提供商若不支持会明确失败，不会私自更换模型。
- 固定全任务 Token 上限以及美元预算 `max_cost_usd`，价格必须由用户按所选服务商当期价格填写；不提供默认虚构价格。
- 实际输入输出 Token 和模型返回标识必须可获取，缺失则停止，不猜测开销。
- 单线程顺序执行、断点续跑、原始证据 SHA 和人工审核版本哈希复查。
- `--mode run` 必须人工审核全部完成并显式加 `--allow-cloud` 才可使用联网模型。

**完成真实双人标注以后**再执行：

```powershell
python -m tools.eval_realpdf30_models --mode run --allow-cloud --pdf-dir benchmarks/real_pdf30/pdfs --geometry realpdf30_geometry_v1452/geometry_candidates.jsonl --config benchmarks/real_pdf30/model_protocol_v1452.json
python -m tools.review_realpdf30_model_answers --mode prepare --run-dir realpdf30_model_eval_v1452
```

其中第二步会为实际生成的 88 条研究问答输出建立 `model_qa_review_A/B.csv`，并要求两名真实审核者重新独立评价回答质量与证据支持。审核完成后运行：

```powershell
python -m tools.review_realpdf30_model_answers --mode score --run-dir realpdf30_model_eval_v1452 --attestation benchmarks/real_pdf30/annotations/human_review_attestation.json
python -m tools.eval_realpdf30_models --mode score --pdf-dir benchmarks/real_pdf30/pdfs --geometry realpdf30_geometry_v1452/geometry_candidates.jsonl --config benchmarks/real_pdf30/model_protocol_v1452.json
```

评分分别输出抽取准确率、方法可比性判断、冲突识别准确率以及 QA 可回答性。研究问答的**语义正确率**只有在实际模型回答经过独立人工复审后才会填入，缺少审核则显示空值。所有输出区分候选召回、文本定位、模型预测和人工金标准。

## 5. 当前边界

此次不声称完成 117 条候选的数值关系复核，也未使用真实云 API 或获得正式模型 F1。PDF 表格探测仅可靠识别带规则线的结构，合并单元格内可能同时含多个模型和数值，不擅自拆分；复杂无边框表格仍需人工处理。对跨实验协议差异，不应直接计算相对精度优劣。


## 6. 人工标签规范及运行安全

- 独立审核的 `qa` 每行需填写人工答案、原文引用、可回答性、0–3 评分及判断理由；其中 `answerable` 为 `yes/no`，评分仅允许 0、1、2、3。
- 跨论文 `pair` 每行需要记录同数据集、同指标、同设置三个判断，以及可定量比较性、冲突分类与判断理由。冲突类别为 `confirmed_conflict`、`apparent_conflict_due_to_settings`、`no_conflict` 和 `insufficient_evidence`，模型 JSON 输出使用完全相同的标签集合。
- 缺少行、重复标注编号、任意取值无效、双人审核差异未仲裁均会阻止正式金标准完成。人工审核员身份需真实填写；软件无法通过内容证明独立审核者确实是两名不同的人。
- 所有文献必须匹配冻结 SHA-256；实验自动化只能验证文本和坐标，不能充当实物实验或科学数值人工审核。
- `model_protocol_v1452.json` 初始 API 地址/模型/费用为空且零预算。提供真实审核、模型和当期报价并显式添加 `--allow-cloud` 后，才能执行正式模型推理。
- v1.45.2 属于研发集成测试版，不应视为已经取得真实人工标注和科研模型准确率。


## 7. 优先审核工作清单

`tools.eval_realpdf30_geometry` 同时输出 `HUMAN_REVIEW_WORKLIST.csv`，按原文定位风险自动排序：

- P0：三条“原文文本已找到，但无法可靠映射几何区域”的候选；应先核查 2210.07182 / PDF 第 9 页、2211.02556 / 第 10 页、2305.10157 / 第 6 页。
- P1：包含公式线索的候选。自动提取仅表示附近存在数学上下文，不代表公式符号、变量或推导已正确识别。
- P2：其它可以高亮的候选，需进一步核查其表格行列、实验条件和单位。

这份清单用于分配人工阅读任务，正式金标准仍由 `review_A/B` 和仲裁表负责。所有 `human_*` 评价字段初始为空，不预填人工审核结论。
