# Paper-Radar：30 篇真实论文实验评估集

这份数据包面向 v1.44.0 科研综合智能体和 v1.45.0 持续科研智能体。覆盖单篇实验证据抽取、跨论文方法比较、冲突识别和研究问答四类任务。

## 一、数据来源

使用原版 Paper-Radar 已收录并记录 arXiv ID 的真实预印本清单作为候选池。按照论文固定版本下载官方 PDF，逐份校验 PDF 文件结构、可提取文本、页数、SHA-256 和实验/结果相关页面。`manifest.json` 保存版本和来源，`quality.json` 保存技术检查结果。

**数据的真实性与科学标注的正确性分别核验。** PDF 结构正常和出现实验相关文本仅证明“这份论文适合进入实验标注流程”，不能证明任一候选数值已与方法、数据集、指标正确对应。

## 二、目录说明

- `pdfs/`：本地研究用的原始预印本 PDF。公开分发前必须核对每份 arXiv 的版本许可；部分分发包可能省略 PDF，可运行 `scripts/download_official_pdfs.py` 按固定 URL 下载。
- `manifest.json`：论文 ID、版本、原始下载地址、页数、SHA-256、PDF 文件路径、审核状态。
- `quality.json`：PDF 技术校验、页数、候选证据数量；模型准确率保留为 `null`。
- `annotations/candidate_evidence.csv`：自动预标注候选，包含物理页码、原始文字片段、候选指标与数值。
- `annotations/review_A.csv`、`review_B.csv`：供两名研究者独立检查字段的空白人工标注表。
- `annotations/adjudicated_gold.csv`：意见不一致时的仲裁表。当前初版为空，不得标注为“已完成人工金标准”。
- `tasks/single_and_cross_paper_qa.jsonl`：面向单篇证据、跨论文研究方法、冲突识别的问答任务。Gold answer 待人工填写。
- `tasks/method_and_conflict_pairs.csv`：候选论文配对和可比性/矛盾判定字段。
- `tasks/research_qa_review.csv`：可直接在 Excel 或表格软件中审核的问题集。
- `scripts/build_evaluation.py`：从本地 PDF 重新生成全部任务与候选。
- `scripts/validate_and_score.py`：核验双人标注、统计仲裁覆盖；人工金标准不完整时不输出模型 F1。
- `docs/ANNOTATION_GUIDE.md`：双人标注协议。

## 三、用途及边界

实验相关页面由程序自动定位，候选数值仅来自真实 PDF 文本，且保留 SHA 及物理页码。机器提取无法单凭词语相邻就确定表格行、列及单位关系；审稿级定量结论仍需对照页面图像人工核实。

跨论文方法比较优先同一研究类别，候选配对不自动视为可直接比较的实验。冲突候选必须在数据划分、测量定义、单位和实验设置一致后才能确认真实冲突。

模型运行指标 `Precision/Recall/F1` 只有在人工双人复核完成且有实际预测文件后计算。当前空白表不含伪造的人工标注与模型成绩。

## 四、复现

```bash
python -m pip install "pymupdf>=1.24,<2"
python scripts/download_official_pdfs.py --manifest manifest.json --dest pdfs
python scripts/build_evaluation.py --download-manifest acquisition_manifest.json --pdf-dir pdfs --out . --count 30
python scripts/validate_and_score.py --dataset . --out annotation_status.json
```

数据文件需要使用 UTF-8 保存。PDF 文件仅保存在研究者个人目录，仓库源码只建议提交 JSON/CSV、脚本和小体量的审阅说明。每次 PDF 版本变化都必须重新提取并使原有标注失效。

## 五、复现及评分细节

`build_evaluation.py` 输出原始候选后，运行 `python scripts/prepare_review_splits.py` 自动固定论文级划分并生成两份独立问答/配对审核表。当前参考划分为 train 16 / validation 7 / test 7，跨论文配对不允许跨划分。

实验抽取评分接收 `MODEL_PREDICTIONS_TEMPLATE.json` 中的 `records` 格式，按论文 ID、物理页、方法、数据集、指标和数值六字段严格匹配；重复输出计入精度分母。只有 A/B 人工标注与必要仲裁完成、且金标准六字段完整，才会输出 Precision / Recall / F1。

`python scripts/score_four_tasks.py --dataset . --out review_coverage.json` 显示单篇问答和跨论文配对的双人标注覆盖率。评分器不会把空模板、历史单人审核种子或模型自生成的候选作为正式金标准。
