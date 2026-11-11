# 30 篇真实 PDF 科研评估集 — 实施与审阅报告

生成日期：2026 年 11 月 10 日

## 核心结论

- arXiv 官方预印本 PDF 候选 33 篇全部下载成功；按实验内容关键词及基本技术质量条件选入 30 篇。
- 30 篇文件总计 1,019 页，约 158.9 MB；每篇保留 PDF SHA-256、版本和官方来源 URL。
- 提取 117 条页码定位候选，生成 139 道研究任务与 34 组跨论文对照。
- 历史 3 篇 FNO/U-FNO/PINO 的 6 条种子数据已与当前 PDF 原文页面核对，另提供 8 条人工可复核的示范问答。
- 正式双人独立标注 0 条；本轮不报告模型 Precision、Recall、F1 或人工标注一致率。
- 使用 Paper-Radar v1.45.0 自身的 pypdf 全文索引器进行独立测试：28 篇完整索引，2 篇达到现有 100 页上限，仅建立部分索引；无解析失败。
- 所有测试题的原文结论以 PDF 为准，脚本中的数字相邻关系均属“候选”，不自动证明行列对应。

## 题型与样本量

| 任务 | 候选数量 | 是否已有双人金标准 |
|---|---:|---|
| 单篇证据抽取 | 117 条 | 否 |
| 单篇研究问答 | 88 题 | 否 |
| 跨论文方法比较 | 34 题 | 否 |
| 冲突识别候选 | 17 题 | 否 |
| 跨论文配对 | 34 组 | 否 |
| 视觉页码复核种子 | 6 条 | 单轮示例，仍需独立复核 |
| 视觉页码核查问答 | 8 题 | 单轮示例，仍需独立复核 |

## 取样结构

| 研究类别 | 数量 |
|---|---:|
| 神经算子 / PDE 代理模型 | 10 |
| PINN / 物理约束学习 | 7 |
| 科研 / Agent 框架 | 5 |
| 天气与科学预测 | 4 |
| 语言模型与训练 | 3 |
| 科学计算基准 | 1 |

## 30 篇论文索引

| 论文 ID / 版本 | 类别 | 页数 | 题名 |
|---|---|---:|---|
| [2210.07182v1](https://arxiv.org/pdf/2210.07182v1) | benchmark | 50 | PDEBENCH: An Extensive Benchmark for Scientific Machine Learning |
| [2308.08468v1](https://arxiv.org/pdf/2308.08468v1) | pinn | 36 | An Expert's Guide to Training Physics-informed Neural Networks |
| [2402.02366v1](https://arxiv.org/pdf/2402.02366v1) | operator | 22 | Transolver: A Fast Transformer Solver for PDEs on General Geometries |
| [2203.13181v1](https://arxiv.org/pdf/2203.13181v1) | operator | 25 | The Cost-Accuracy Trade-Off In Operator Learning With Neural Networks |
| [2102.09618v1](https://arxiv.org/pdf/2102.09618v1) | operator | 112 | Error estimates for DeepOnets: A deep learning framework in infinite dimensions |
| [2109.03697v3](https://arxiv.org/pdf/2109.03697v3) | operator | 37 | U-FNO -- an enhanced Fourier neural operator-based deep-learning model for multiphase flow |
| [2211.02556v1](https://arxiv.org/pdf/2211.02556v1) | weather | 19 | Pangu-Weather: A 3D High-Resolution Model for Fast and Accurate Global Weather Forecast |
| [2212.12794v1](https://arxiv.org/pdf/2212.12794v1) | weather | 36 | GraphCast: Learning skillful medium-range global weather forecasting |
| [2208.05419v1](https://arxiv.org/pdf/2208.05419v1) | weather | 12 | FourCastNet: Accelerating Global High-Resolution Weather Forecasting using Adaptive Fourier Neural Operators |
| [2004.01806v1](https://arxiv.org/pdf/2004.01806v1) | pinn | 29 | On the convergence of physics informed neural networks for linear second-order elliptic and parabolic type PDEs |
| [2111.03794v4](https://arxiv.org/pdf/2111.03794v4) | operator | 27 | Physics-Informed Neural Operator for Learning Partial Differential Equations |
| [2206.02016v1](https://arxiv.org/pdf/2206.02016v1) | pinn | 29 | Is $L^2$ Physics-Informed Loss Always Suitable for Training Physics-Informed Neural Network? |
| [2202.11214v1](https://arxiv.org/pdf/2202.11214v1) | weather | 28 | FourCastNet: A Global Data-driven High-resolution Weather Model using Adaptive Fourier Neural Operators |
| [2108.12515v1](https://arxiv.org/pdf/2108.12515v1) | operator | 33 | Convergence Rates for Learning Linear Operators from Noisy Data |
| [2504.13480v1](https://arxiv.org/pdf/2504.13480v1) | operator | 8 | Integrating Locality-Aware Attention with Transformers for General Geometry PDEs |
| [2105.00862v1](https://arxiv.org/pdf/2105.00862v1) | pinn | 17 | Data vs. Physics: The Apparent Pareto Front of Physics-Informed Neural Networks |
| [2010.08895v3](https://arxiv.org/pdf/2010.08895v3) | operator | 16 | Fourier Neural Operator for Parametric Partial Differential Equations |
| [2305.10157v1](https://arxiv.org/pdf/2305.10157v1) | pinn | 28 | Efficient Error Certification for Physics-Informed Neural Networks |
| [2306.03838v1](https://arxiv.org/pdf/2306.03838v1) | operator | 18 | Spherical Fourier Neural Operators: Learning Stable Dynamics on the Sphere |
| [1910.03193v1](https://arxiv.org/pdf/1910.03193v1) | operator | 22 | DeepONet: Learning nonlinear operators for identifying differential equations based on the universal approximation theorem of operators |
| [1711.10561v1](https://arxiv.org/pdf/1711.10561v1) | pinn | 22 | Physics Informed Deep Learning (Part I): Data-driven Solutions of Nonlinear Partial Differential Equations |
| [2104.02741v1](https://arxiv.org/pdf/2104.02741v1) | pinn | 18 | Conditional physics informed neural networks |
| [2408.06292v1](https://arxiv.org/pdf/2408.06292v1) | agent_science | 185 | The AI Scientist: Towards Fully Automated Open-Ended Scientific Discovery |
| [2208.03306v1](https://arxiv.org/pdf/2208.03306v1) | language | 29 | Branch-Train-Merge: Embarrassingly Parallel Training of Expert Language Models |
| [2304.03442v1](https://arxiv.org/pdf/2304.03442v1) | agent_science | 22 | Generative Agents: Interactive Simulacra of Human Behavior |
| [2305.18290v1](https://arxiv.org/pdf/2305.18290v1) | language | 26 | Direct Preference Optimization: Your Language Model is Secretly a Reward Model |
| [2302.13971v1](https://arxiv.org/pdf/2302.13971v1) | language | 27 | LLaMA: Open and Efficient Foundation Language Models |
| [2310.07984v1](https://arxiv.org/pdf/2310.07984v1) | agent_science | 27 | Large Language Models for Scientific Synthesis, Inference and Explanation |
| [2308.08155v1](https://arxiv.org/pdf/2308.08155v1) | agent_science | 28 | AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation |
| [2210.03629v1](https://arxiv.org/pdf/2210.03629v1) | agent_science | 31 | ReAct: Synergizing Reasoning and Acting in Language Models |

## 数据分割与防泄漏

训练、验证、测试分别按论文 ID 固定为 16、7、7 篇。跨论文候选配对只发生在同一子集，避免同一论文全文同时进入训练和测试。

## Paper-Radar 真实 PDF 解析兼容性

| 检查内容 | 数量 |
|---|---:|
| 完整索引 | 28 篇 |
| 因超过 100 页限制而部分索引 | 2 篇 |
| 解析失败或子进程超时 | 0 篇 |
| 位于第 100 页之后的自动证据候选 | 3 条 |

两篇部分索引文献为 `2102.09618v1`（112 页）与 `2408.06292v1`（185 页）。后者有 3 条实验候选位于第 105、143、167 页。当前固定前 100 页解析会漏掉这些附录证据，建议后续改为全文分页索引或采用实验相关页优先调度。

## 具体质量与科学风险

1. 自动标注以 PDF 可提取文本和关键词窗口为输入，尚未验证复杂表格行列、图中数据或扫描附录。
2. 实验内容丰富度判断是程序初筛，个别论文以理论或方法性研究为主，是否适合最终实验抽取评测需人工筛查。
3. 同类任务的实验结果也可能因数据划分、网格、算力预算、误差定义不同而不可比。配对只是人工审阅候选。
4. 当前尚未形成有正例的正式冲突判定金标准，不能宣称冲突召回率。
5. 正式可复现指标必须在第二位审阅者及仲裁完成后，使用固定的未参与调参测试集。

## 使用与运行

1. 解压后使用 `RUN_DOWNLOAD_WINDOWS.bat` 从 arXiv 官方地址取得全部 30 篇固定版本 PDF；已存在的文件仅在 SHA 不匹配时重新下载。
2. 打开 `docs/ANNOTATION_GUIDE.md`，由 A、B 两名研究者独立核对 `annotations/review_A.csv` 和 `review_B.csv`，以及 `tasks/qa_review_A.csv`、`qa_review_B.csv` 等。
3. 审核不一致时填写对应的 `adjudicated.csv`。再次运行 `RUN_REVIEW_STATUS_WINDOWS.bat` 查看可评分的成熟度。
4. 运行 `python scripts/validate_and_score.py --dataset . --predictions model_predictions.json --out model_score.json`。在双人完整标注前，模型指标保持 null。

## 原始 PDF 版权说明

arXiv 对不同版本采取不同许可。原 PDF 已用于本次科研分析和哈希核验；为避免未经授权的再分发，本次公开可下载的评估数据包提供官方版本化 PDF 链接、校验值和私有下载脚本，不将未经逐篇确认许可的 PDF 复制到公开代码仓库。
