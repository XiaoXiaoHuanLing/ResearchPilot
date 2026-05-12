# RAG评估三模式对比报告：Dense vs Hybrid vs Agent

**日期**: 2026-05-11  
**数据集**: 20260511_134156 (135条, keyword=39/negation=39/fact=29/vague=28, easy=56/medium=72/hard=7)  
**知识库**: KB#2 (AIOps文档), top_k=8  

---

## 一、总览

| 指标 | Dense | Hybrid | Agent | 最优 |
|------|-------|--------|-------|------|
| **Context Precision** | 0.692 | null | 0.767 | Agent ✅ |
| **Context Recall** | 0.667 | 0.756 | 0.859 | Agent ✅✅ |
| **Faithfulness** | 0.678 | 0.697 | 0.825 | Agent ✅✅ |
| Factual Correctness | null | 0.250 | null | Hybrid(仅它有) |

**结论**: Agent在3个可比指标上全面领先。Hybrid介于Dense和Agent之间。  
⚠️ CP和FC的null问题与评估模型兼容性有关（Dense/Agent用qwen3-235b-thinking, Hybrid用qwen3.6-plus），跨模型指标不可直接对比。

---

## 二、跨模式可比指标 (CR + Faithfulness, 两模型均有值)

| 指标 | Dense | Hybrid | Agent | H vs D | A vs D | A vs H |
|------|-------|--------|-------|--------|--------|--------|
| **CR** | 0.667 | 0.756 | 0.859 | +0.089 | +0.193 | +0.103 |
| **Faith** | 0.678 | 0.697 | 0.825 | +0.019 | +0.147 | +0.128 |

趋势清晰：**Dense < Hybrid < Agent**，每级递进都有提升。

---

## 三、分类型详细对比

### 3.1 Keyword型 (n=39) — Agent最大赢家

| 指标 | Dense | Hybrid | Agent |
|------|-------|--------|-------|
| CP | 0.417 | null | 0.608 |
| CR | 0.375 | 0.750 | **0.782** |
| Faith | null | 0.439 | 0.746 |
| FC | null | 0.195 | null |

**分析**: Keyword型是三种模式差距最明显的。Dense CR=0.375（最差），Hybrid加入BM25后CR翻倍到0.750，Agent进一步到0.782。BM25对关键词精确匹配贡献巨大。但Hybrid Faithfulness仅0.439（低），可能因为hybrid检索返回更多片段但answer生成质量不够好。

### 3.2 Negation型 (n=39) — Dense意外地好

| 指标 | Dense | Hybrid | Agent |
|------|-------|--------|-------|
| CP | 0.761 | null | 0.792 |
| CR | **1.000** | **1.000** | 0.905 |
| Faith | 0.833 | **0.972** | 0.851 |
| FC | null | 0.555 | null |

**分析**: Negation型三种模式CR都接近完美。Dense CR=1.0说明向量语义对否定型查询捕捉得很好。Hybrid Faith=0.972最高！但Agent CR略降（0.905），多轮检索引入了噪声。

### 3.3 Fact型 (n=29) — Agent精度最优

| 指标 | Dense | Hybrid | Agent |
|------|-------|--------|-------|
| CP | 0.708 | null | **0.896** |
| CR | **1.000** | 0.722 | **1.000** |
| Faith | 0.600 | 0.300 | 0.799 |
| FC | null | 0.000 | null |

**分析**: Dense和Agent的CR都=1.0（事实型检索容易），但Hybrid CR=0.722反而低——可能BM25对事实型短查询匹配不佳，拉低了RRF融合结果。Hybrid Faith=0.300极低，answer生成质量问题。

### 3.4 Vague型 (n=28) — Agent召回率碾压

| 指标 | Dense | Hybrid | Agent |
|------|-------|--------|-------|
| CP | 1.000 | null | 0.843 |
| CR | 0.250 | 0.333 | **0.783** |
| Faith | null | 0.909 | 0.910 |
| FC | null | 0.000 | null |

**分析**: Vague型是Agent优势最大的场景。Dense CR=0.25（只能找到1/4相关内容），Hybrid略升到0.333，Agent直接到0.783——**3倍于Dense**！模糊查询需要多轮理解+扩展检索，只有Agent能做到。

---

## 四、分难度对比

### Easy (n=56)

| 指标 | Dense | Hybrid | Agent |
|------|-------|--------|-------|
| CR | 0.700 | 0.667 | **0.889** |
| Faith | 0.600 | 0.569 | **0.822** |

### Medium (n=72)

| 指标 | Dense | Hybrid | Agent |
|------|-------|--------|-------|
| CR | 0.750 | 0.806 | **0.826** |
| Faith | 0.833 | **0.963** | 0.845 |

⚠️ Hybrid Medium Faith=0.963最高！但这是qwen3.6-plus评估的，可能评估模型偏乐观。

### Hard (n=7)

| 指标 | Dense | Hybrid | Agent |
|------|-------|--------|-------|
| CR | 0.000 | **1.000** | **1.000** |
| Faith | null | 0.545 | 0.429 |

⚠️ 7条样本统计意义有限。Dense CR=0说明纯向量检索对hard题完全失效，Hybrid/Agent通过BM25或多轮检索可完美召回。

---

## 五、评估配置对比

| 项目 | Dense | Hybrid | Agent |
|------|-------|--------|-------|
| 检索方式 | Dense+AutoMerging | Dense+BM25→RRF→AutoMerging | ReAct Agent + search_knowledge + rerank |
| Answer模型 | qwen3-235b-thinking | qwen3.6-plus / deepseek-v3.2 | qwen3-32b→qwen3-14b |
| RAGAS评估模型 | qwen3-235b-thinking | qwen3.6-plus | qwen3-max |
| RAG耗时 | 2473s (18s/条) | 6284s (47s/条) | 4792s (36s/条) |
| RAGAS耗时 | 413s | 456s | 1132s |
| 总耗时 | 47min | 112min | 99min |

⚠️ Hybrid RAG耗时最长（6284s），原因：前3个模型403后探测开销 + 答案生成需要额外LLM调用。

---

## 六、核心结论

### 1. Agent > Hybrid > Dense（检索质量维度）

Agent的多轮检索+Reranker架构在所有可比指标上最优，尤其：
- **Keyword型**: Agent CR=0.782，是Dense(0.375)的2倍以上
- **Vague型**: Agent CR=0.783，是Dense(0.250)的3倍以上
- **Hard题**: Agent CR=1.0，Dense CR=0（完全失效）

### 2. Hybrid为什么不如Agent？

- Hybrid是"Dense+BM25一次性融合"，无法根据查询反馈调整策略
- Agent可ReAct：第一次检索不够→调整关键词→再次检索→rerank，这是质的区别
- Hybrid的Answer生成用了qwen3.6-plus（Faithfulness偏低），而Agent用qwen3-14b反而更忠实——模型选择也有影响

### 3. Hybrid相对Dense的优势

- **Keyword型CR翻倍**: 0.375→0.750，BM25精确匹配对关键词型贡献巨大
- **Hard题CR从0→1.0**: BM25弥补了Dense向量检索对困难题的不足
- 但Fact型CR反而下降（1.0→0.722），BM25对短事实查询匹配不佳

### 4. 实际选择建议

| 场景 | 推荐模式 | 理由 |
|------|----------|------|
| 对延迟敏感 | Dense | 最快(18s/条)，简单问题够用 |
| 关键词精确查询 | Hybrid | BM25精确匹配+向量语义 |
| 模糊/复杂/多步查询 | Agent | 多轮检索+rerank，召回率碾压 |
| 通用场景 | Agent | 综合最优，代价是速度慢2倍 |

### 5. 待改进

1. **跨评估模型指标不可比**: Dense/Agent用qwen3-235b-thinking，Hybrid用qwen3.6-plus，FC只在Hybrid有值——应统一评估模型重跑
2. **Factual Correctness兼容性**: FC在Dense/Agent中null，Hybrid中0.25（低），可能是RAGAS版本或模型配置问题
3. **Hybrid Answer生成质量低**: Hybrid Faithfulness在keyword(0.439)/fact(0.300)偏低，可能需要优化answer生成prompt或换更强的模型
4. **Hard题样本太少**(7条)，需要补充
5. **Hybrid耗时最长**(6284s)，主要是模型fallback探测开销，应优化dead cache
