# RAG 调优路线图

> 基于 2026-05-05 首轮评估 → 2026-05-07 Phase 1 改造 → 2026-05-11 Phase 2 三模式评估
> 更新: 2026-05-11 23:00

---

## 一、最新基线（2026-05-11 三模式评估，135样本，KB#2，top_k=8）

| 指标 | Dense | Hybrid | Agent | 最优 |
|------|-------|--------|-------|------|
| Context Precision | 0.692 | null | **0.767** | Agent |
| Context Recall | 0.667 | 0.756 | **0.859** | Agent |
| Faithfulness | 0.678 | 0.697 | **0.825** | Agent |
| Factual Correctness | null | 0.250 | null | ⚠️ 仅Hybrid有值 |

**结论: Agent > Hybrid > Dense，逐级递进**

### 分类型核心数据

| 类型 | Dense CR | Hybrid CR | Agent CR | Agent最大优势 |
|------|---------|-----------|----------|--------------|
| Keyword | 0.375 | 0.750 | **0.782** | BM25翻倍+多轮 |
| Negation | **1.000** | **1.000** | 0.905 | Dense已完美 |
| Fact | **1.000** | 0.722 | **1.000** | Hybrid反降(BM25干扰) |
| Vague | 0.250 | 0.333 | **0.783** | **3倍于Dense** |

### 与旧基线(05-05)对比

| 指标 | 旧基线(dense,top_k=5) | 新Dense(top_k=8) | 新Agent(top_k=8) |
|------|----------------------|-------------------|------------------|
| CP | 0.36 | **0.692** (+92%) | **0.767** (+113%) |
| CR | 0.63 | **0.667** (+6%) | **0.859** (+36%) |
| Faith | 0.70 | **0.678** (-3%) | **0.825** (+18%) |
| FC | 0.40 | null | null |

**top_k=5→8 + AutoMerging去重后，CP从0.36→0.692，提升92%！**

---

## 二、已完成的改造

### Phase 1 (2026-05-07): 代码层面修复
- [x] P0-1: Agent Prompt重写 → 强制事实点格式输出
- [x] P0-2: Reranker服务 + rerank_recall_pool工具
- [x] P0-3: 评估模块Agent流程修正
- [x] P1-1: top_k宽度提升(5→8)
- [x] P2-2: Prompt忠实度约束

### Phase 2 (2026-05-11): 评估基础设施 + 三模式对比
- [x] Dense模式snippet去重(80字符前缀)，AutoMerging父子重复修复
- [x] 数据集V2重生成: 135条, 自包含+人类可回答性校验
- [x] Agent多模型fallback链(6+模型自动切换, 403/429区分)
- [x] Dead model cache(30分钟TTL, 403永久/429临时)
- [x] 断点续跑(every 10 samples checkpoint)
- [x] enable_thinking参数修复(model_kwargs→extra_body)
- [x] Agent 403 re-raise(fallback链才能检测到quota error)
- [x] logging配置(basicConfig INFO级别)
- [x] 独立RAGAS评估脚本(run_ragas_only.py)
- [x] Dense评估完成: CP=0.692, CR=0.667, Faith=0.678
- [x] Agent评估完成: CP=0.767, CR=0.859, Faith=0.825
- [x] Hybrid评估完成: CR=0.756, Faith=0.697, FC=0.250(首次非null)
- [x] 三模式对比报告: Agent > Hybrid > Dense

---

## 三、待执行（Phase 3-4）

### Phase 3: 评估质量提升

| # | 任务 | 优先级 | 状态 |
|---|------|--------|------|
| 3.1 | 统一评估模型重跑(当前Dense/Agent用qwen3-235b-thinking, Hybrid用qwen3.6-plus) | P0 | ⏳ |
| 3.2 | Factual Correctness兼容性排查(仅Hybrid有值0.25, Dense/Agent null) | P0 | ⏳ |
| 3.3 | Context Precision在Hybrid中null(评估模型指标兼容问题) | P1 | ⏳ |
| 3.4 | Hard题样本扩充(当前仅7/135条, 统计不可靠) | P2 | ⏳ |
| 3.5 | Hybrid Answer生成质量偏低(keyword Faith=0.439, fact Faith=0.300) | P2 | ⏳ |

### Phase 4: 持续优化

| 优先级 | 方案 | 预期效果 | 验证方式 |
|--------|------|---------|---------|
| P3 | 否定查询改写 | negation CR保持1.0前提下提升CP | 改写前后对比 |
| P4 | HyDE改写 | vague CR 0.783→0.85+ | 有/无HyDE对比 |
| P5 | AutoMerging thresh调优 | CP整体提升 | 不同thresh对比 |
| P6 | 数据集扩充+质量提升 | 评估可靠性提升 | 新旧数据集对比 |
| P7 | Hybrid Answer生成优化 | Faithfulness 0.697→0.80+ | 换强模型/prompt优化 |
| P8 | Agent Reranker阈值调优 | Medium CP 0.714→0.80+ | 阈值对比实验 |

---

## 四、成功指标

| 指标 | 旧基线(05-05) | **当前最佳(05-11 Agent)** | 达标? |
|------|---------------|--------------------------|-------|
| context_precision | 0.36 | **0.767** | ✅ 目标0.55+，实际+113% |
| context_recall | 0.63 | **0.859** | ✅ 目标0.75+，实际+36% |
| faithfulness | 0.70 | **0.825** | ✅ 目标0.80+，实际+18% |
| factual_correctness | 0.40 | 0.250 (Hybrid) | ⚠️ 仅Hybrid有值，Agent/Dense null |
| hard CR | 0.00 | **1.000** (7样本) | ✅ 从0到1 |
| negation CP | 0.18 | **0.792** (Agent) | ✅ +340% |

**4/6指标已达标，FC和negation CP(已达标)剩余2项需统一评估模型后确认**

---

## 五、评估配置与结果索引

### 评估结果目录

| 目录 | 模式 | RAG模型 | RAGAS模型 | RAG耗时 | RAGAS耗时 |
|------|------|---------|-----------|---------|-----------|
| `20260511_143909_dense` | Dense | qwen3-235b-thinking | qwen3-235b-thinking | 2473s | 413s |
| `20260511_160614_agent` | Agent | qwen3-32b→14b | qwen3-max | 4792s | 1132s |
| `20260511_205820_hybrid` | Hybrid | qwen3.6-plus/deepseek-v3.2 | qwen3.6-plus | 6284s | 456s |

### 对比报告
- Dense vs Agent: `data/results/comparisons/comparison_20260511_dense_vs_agent.md`
- 三模式对比: `data/results/comparisons/comparison_20260511_three_modes.md`

### 数据集
- V2数据集(135条): `data/datasets/20260511_134156/dataset.jsonl`
  - 类型: keyword=39, negation=39, fact=29, vague=28
  - 难度: easy=56, medium=72, hard=7

### 技术备忘
- 评估Embedding: ollama qwen3-embedding:4b (2560维)
- Reranker: BAAI/bge-reranker-v2-m3 CrossEncoder (本地)
- Agent工具链: search_knowledge + get_recall_nodes + rerank_recall_pool + list_active_kbs
- 必须用venv Python(.venv/Scripts/python.exe, Python 3.12)，系统Python(Anaconda 3.13)缺sentence-transformers
- DashScope免费额度不稳定，需多模型fallback链

### 关键Bug修复记录(05-11)
1. enable_thinking: model_kwargs → extra_body (qwen3-32b/14b/8b)
2. Agent 403 re-raise: 内部except吃掉403 → re-raise让fallback链检测
3. 0.0 or 0 Python bug: reflexive_retriever → proper None check
4. Reranker all-zero score: 保留原始RRF分数不覆盖
5. AutoMerging父子重复: 80字符前缀snippet去重
6. Logging: basicConfig INFO (默认WARNING吞掉所有INFO日志)

### 当前DashScope可用模型(05-11 17:35 comi提供)
qwen3.5-plus-2026-04-20, qwen3.6-27b, qwen3.6-plus-2026-04-02, deepseek-v3.2, qwen3-max-2025-09-23, glm-5.1, gui-plus-2026-02-26, kimi-k2.6

⚠️ 额度不稳定，qwen3.5-plus在RAGAS评估中途耗尽过一次
