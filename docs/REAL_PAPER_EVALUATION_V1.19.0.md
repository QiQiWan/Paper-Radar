# Paper-Radar v1.19.0｜真实论文全文解析与跨论文实验比较基准

## 1. 样本与证据来源

**本轮建立了真实出版论文的可复现种子评估集。** 论文身份、公开 PDF 链接、版本、人工核查页码及短引文保存在 `benchmarks/real_papers_v1/`。实际论文 PDF 不随项目重新分发，需要通过原文链接自行获取。

| 原始论文 | 固定版本 | 已核查位置 | 核查内容 |
|---|---|---|---|
| [Fourier Neural Operator](https://arxiv.org/abs/2010.08895) | v3 | PDF 第 8 页，Table 1 | Navier–Stokes 测试表中的 FNO-3D / FNO-2D 数值 |
| [U-FNO](https://arxiv.org/abs/2109.03697) | v3 | PDF 第 19 页，Table 2 | CO₂ 饱和度与压力前沿预测误差 |
| [Physics-Informed Neural Operator](https://arxiv.org/abs/2111.03794) | v4 | PDF 第 13 页，Table 2 | Darcy Flow 的 PINO 与 DeepONet 对照 |

这三篇论文覆盖了常规科学机器学习、复杂多相流、物理约束算子。当前共六条人工核查数值、五个跨论文不可比关系。原文页码来自公开 PDF 的人工核查；本地 Python PDF 解析器仍须实际下载 PDF 后才可测量。

## 2. 评估命令

准备环境：`python -m pip install -e .`。在可访问 arXiv 的电脑或服务器上手动执行：

```bash
python tools/eval_real_papers.py fetch
python tools/eval_real_papers.py score
```

首条命令在 `benchmarks/real_papers_v1/pdfs/` 保存原始 PDF，仅供本地评估使用；下载时执行 HTTPS 主机检查、响应大小限制、请求间隔控制，不爬取付费站点。第二条命令调用当前 `portal_pdf_worker` 按页抽取文本，核查实验数值和方法名是否在人工标注页码上同时出现，生成 `results/real_paper_eval.json`、`results/real_paper_eval.md` 与 `results/real_paper_synthesis.md`。

可选模型输出评价：

```bash
python tools/eval_real_papers.py score --predictions my_predictions.json
```

其中 `my_predictions.json` 是结构化实验预测数组（至少包含 `paper_id`、`page_index`、`metric`、`value`）。程序输出精确匹配的 Precision、Recall、F1；**不提供预测文件时这些指标应报告为“未测量”**。

## 3. 核心指标定义

- **全文解析覆盖率**：成功解析的原始 PDF 数 / 全部真实 PDF 数（失败或超上限单独计数）。
- **证据页码召回率**：在人工标注物理 PDF 页中同时找到指标数值和方法关键词的条目数 / 有效待测标注数。该指标检测漏字、页码错位、表格分列导致的信息丢失。
- **实验抽取 P/R/F1**：以 `(paper_id, PDF 物理页, metric, value)` 精确匹配人工标签。由于同义表达与指标解释尚未全部标注，后续应增加标准化及人工复核。
- **错误跨论文比较率**：明确不可比的论文配对被系统误归入同一对比候选的比例。必须单独报告正例数量及召回率，否则“全部不比较”也能获得看似很高的安全准确率。
- **证据完整度**：每条结论是否包含论文 ID、固定版本、原文定位、指标定义、数据集、数值、基线及实验条件。

## 4. 首轮已执行的评估与限制

在当前执行环境中，原论文元数据和公开 PDF 实验表格已通过在线来源和 PDF 页截图人工核对，但当前容器无法访问原始 PDF 下载域名。因此：

- **六条金标准与五项不可比关系测试可以执行。** SynthesisAgent 对这些不可比对照没有生成跨论文定量候选。
- **本地真实 PDF 的解析覆盖、页码召回和模型抽取 F1 均尚未测量。** 评分脚本对缺失 PDF 返回 `pdf_missing`，不会将人工网页查看结果冒充为本地全文解析结果。
- v1.18.0 公网 PDF 单篇上限 12 MiB，PINO 的最新固定版本大于该阈值；评估下载允许 45 MiB，以诊断生产限制。测试工具的容量放宽不意味着公网自动放宽上传限制。
- 种子集只包含不可比配对，当前不能测量跨论文可比性正例召回率，无法形成有统计意义的学术准确率结论。

## 5. 后续正式评估扩展计划

建议首批扩展为至少 30 篇真实论文，覆盖不同算子方法、实验表格样式、两栏排版、页眉页脚干扰、跨页表格、公式、扫描页、附录和预印本版本差异；每篇标注 3–5 条证据，并由两名审阅者交叉复核。对跨论文的“可比”正例必须同时确定数据集版本、数据划分、指标方向/单位、分辨率、训练预算与统计定义。最终报告必须区分模型提取错误、PDF 解析漏字和学术推断错误。

## 6. 本轮新增报告核对

SynthesisAgent 的实际 Markdown 输出写入 `real_paper_synthesis.md`，对人工标注的 6 条记录检查来源链接、页码和引文完整性。此报告明确标注“用户导入，尚未由服务器核对 PDF”；它是方法学测试工件，不可当作真实 PDF 自动解析结果。
