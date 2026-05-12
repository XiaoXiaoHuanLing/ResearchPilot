# RAG 评估模块 — 设计文档

> 模块: `backend/rag_eval/`  
> 更新: 2026-05-06  
> 框架: RAGAS 0.4.3  

---

## 一、模块定位

对 ResearchPilot 的 RAG 检索+生成管线进行系统性评估，量化检索质量和生成质量，驱动迭代优化。

核心能力：
1. **数据集构建**：从 KB 文档自动生成多类型、多难度的问题对
2. **评估指标计算**：通过 RAGAS 框架计算最具代表性的 6 个指标
3. **报告生成**：按类型/难度分组分析薄弱环节，生成改进建议

---

## 二、模块结构

```
backend/rag_eval/
├── DESIGN.md              # 本设计文档
├── ROADMAP.md             # 调优路线图
├── __init__.py             # 模块入口
├── __main__.py             # CLI 入口 (generate / evaluate / e2e / report)
├── dataset.py             # 数据集生成
├── evaluate.py             # 评估执行
├── report.py               # 报告生成
├── ollama_embeddings.py    # Ollama Embedding wrapper（绕过 LangChain tiktoken 兼容问题）
└── data/
    ├── datasets/           # 生成的数据集 (JSONL + meta.json)
    └── results/            # 评估结果归档
```

---

## 三、数据集构建 (`dataset.py`)

### 核心思路

对原文档进行大 chunk 切分，通过滑动窗口分组，LLM 生成覆盖多难度和多类型的问题对。

### 流程

1. **文档加载**：支持本地 `.docx` 目录 或 已入库 KB ID 两种输入
2. **切分 + 分组**：
   - `RecursiveCharacterTextSplitter(1000字, 150重叠, 中文分隔符)`
   - 过滤低价值 chunk（极短且无高密度内容）
   - 按文档分组：5 chunk/组, overlap=1，保证上下文连贯
3. **LLM 生成 Q&A**：
   - 4 种问题类型：`fact` / `keyword` / `vague` / `negation`，强制比例
   - 3 种难度：`easy`(1节点) / `medium`(2-3节点) / `hard`(3+节点)
   - JSON 结构化输出（容忍 markdown 代码块包裹）
   - 6 模型 fallback 链，403/429 自动切换
4. **质量筛选（三层）**：字段完整性 → 常识过滤 → 去重
5. **后处理标注**：`retrieval_suitability`（keyword/semantic/hybrid）
6. **持久化**：JSONL + meta.json

### 数据集记录格式

```json
{
  "id": "q_001",
  "user_input": "GPU显存溢出时如何快速判断是模型问题还是驱动问题？",
  "reference": "当GPU显存溢出时，应先通过nvidia-smi确认显存占用分布...",
  "reference_contexts": ["节点A全文...", "节点B全文..."],
  "question_type": "keyword",
  "difficulty": "hard",
  "key_phrases": ["GPU显存", "nvidia-smi", "驱动故障"],
  "dependency_count": 2,
  "retrieval_suitability": "keyword"
}
```

RAGAS `evaluate()` 使用字段：`user_input`, `reference`, `reference_contexts`（+ 评估时填充的 `response`, `retrieved_contexts`）。

分组统计字段：`question_type`, `difficulty`。

---

## 四、评估执行 (`evaluate.py`)

### 流程

```
1. 加载数据集 (JSONL)
2. 逐条走 RAG 主链路 (rag_query) → 获取 answer + citations
3. 构建 RAGAS EvaluationDataset (SingleTurnSample)
4. RAGAS evaluate() — 6 指标评分
5. 按 question_type + difficulty 分组统计
6. 汇总保存 (summary.json + samples.json + config.json)
```

### RAGAS 6 指标

| 指标 | 评估维度 | 计算方式 |
|------|---------|---------|
| ContextPrecision | 检索排序质量 | LLM 逐位判断检索 chunk 相关性 + 位置加权精度 |
| ContextRecall | 召回完整度 | 参考答案拆成 claim，逐条检查是否被检索 chunk 支持 |
| Faithfulness | 忠实度（无幻觉）| 答案拆成 claim，逐条检查是否有检索 chunk 作为依据 |
| AnswerRelevancy | 答案相关度 | 从答案反向生成问题，算与原问题的语义相似度 |
| FactualCorrectness | 事实正确性 | 答案与参考答案拆成 claim 集合，算 F1 |
| NoiseSensitivity | 噪声敏感度 | 有噪声 vs 无噪声时的回答质量差异 |

### 关于经典检索指标的移除

经典检索指标（P@K / R@K / MRR / HitRate / NDCG）已移除，原因：
- 匹配粒度是文档文件名级别（`expected_doc_paths` vs citation source 子串匹配）
- 不判断 chunk 内容是否与 ground truth 原句相关
- 导致指标系统性偏高（如 R@5=0.91 vs RAGAS ContextRecall=0.63），与 RAGAS 的内容级语义评判不可比

**替代方案**：如果后续需要 chunk 级检索质量指标，应在 RAGAS 框架内补充，而非使用文件名模糊匹配。

---

## 五、报告生成 (`report.py`)

读取评估结果 → 生成 Markdown 报告，包含：
- 基本信息表（数据集大小、耗时、指标列表）
- RAGAS 指标表（含评估维度说明）
- 按问题类型 + 难度分组表
- 改进建议（基于阈值规则自动生成）

---

## 六、模型配置

### 可用评估模型（百炼平台，2026-05-06）

| 模型 | 适用场景 | 备注 |
|------|---------|------|
| glm-5 | 数据集生成 / 评估评分 | 强推理 |
| glm-5.1 | 数据集生成 / 评估评分 | 当前主模型 |
| qwen3.6-flash-2026-04-16 | 快速 fallback | 免费，速度快 |
| qwen3.6-flash | 快速 fallback | 同上 |
| qwen3.6-max-preview | 高质量生成 | 免费额度有限 |
| qwen3.6-27b | 中等质量 fallback | |
| kimi-k2.6 | 评估评分 | 支持 n>1 |
| deepseek-v4-flash | 快速评估 | |
| deepseek-v4-pro | 高质量评估 | |
| qvq-max-2025-03-25 | 视觉/推理 | 不适用当前场景 |
| qwen-math-turbo | 数学推理 | 不适用 |
| qwen-coder-turbo-0919 | 代码 | 不适用 |
| qwen3-vl-235b-a22b-thinking | 视觉推理 | 不适用 |
| qwen2.5-math-7b-instruct | 数学 | 不适用 |
| qwen3-vl-30b-a3b-thinking | 视觉推理 | 不适用 |

### 推荐组合

- **数据集生成**：glm-5.1 / glm-5（强推理，JSON 输出质量高）
- **RAGAS 评估评分**：glm-5.1（需支持 n=3）或 kimi-k2.6
- **RAGAS 评估 Embedding**：ollama `qwen3-embedding:4b`（2560维，本地无限制）

### 关键兼容性问题

- LangChain `OpenAIEmbeddings` 与 ollama 不兼容（tiktoken tokenize 为 token ID → ollama 400），需使用 `ollama_embeddings.py` 自定义 wrapper
- RAGAS AnswerRelevancy 要求 LLM 支持 `n>1`（生成多个反向问题），部分模型不支持

---

## 七、CLI

```bash
# 数据集生成
python -m rag_eval generate --kb-id 2 --size 200

# 评估执行
python -m rag_eval evaluate --dataset-dir data/datasets/xxx --kb-id 2

# 端到端（生成 + 评估）
python -m rag_eval e2e --kb-id 2 --size 200

# 报告生成
python -m rag_eval report --results-dir data/results/xxx
```

---

## 八、依赖

```
ragas >= 0.4.3
langchain-openai
langchain-text-splitters
openai
dashscope
rapidfuzz
```
