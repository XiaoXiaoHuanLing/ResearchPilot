# Agent 全链路评估设计

> 日期: 2026-05-17
> 状态: 设计中

## 1. 目标

在已有 RAG 评估（CP/CR/Faith/FC 四指标，覆盖检索+生成质量）基础上，
**补充 Agent 链路维度的量化评估**，形成完整的评估体系：

| 评估维度 | 已有 | 本次新增 |
|---------|------|---------|
| 检索质量 | ✅ CP/CR | - |
| 生成质量 | ✅ Faith/FC | - |
| Agent 效率 | ❌ | ✅ 工具调用/迭代/耗时 |
| Agent 鲁棒性 | ❌ | ✅ 错误恢复/降级率 |
| Agent 用户体验 | ❌ | ✅ 延迟/首token |
| 端到端任务完成 | ❌ | ✅ 完成率/准确性 |

## 2. 评估指标体系

### 2.1 效率指标（Agent Efficiency Metrics）

| 指标 | 英文 | 计算方式 | 说明 |
|------|------|---------|------|
| 工具调用总次数 | `tool_call_count` | 一次对话中工具被调用的总次数 | 越少越高效 |
| 迭代轮数 | `iteration_rounds` | Agent 决策循环的轮数 | search→eval→rerank→distill 算1完整轮 |
| LLM token 消耗 | `llm_tokens_used` | 输入+输出token总和 | 成本指标 |
| 检索→蒸馏转化率 | `retrieval_distill_rate` | 蒸馏事实点数/召回节点数 | 召回节点的有效利用比例 |
| 工具冗余率 | `tool_redundancy_rate` | 无效/重复工具调用/总调用 | 如重复搜同一query |

### 2.2 鲁棒性指标（Agent Robustness Metrics）

| 指标 | 英文 | 计算方式 | 说明 |
|------|------|---------|------|
| 任务完成率 | `task_completion_rate` | 成功完成的样本/总样本 | 非空有意义回答即为完成 |
| 工具错误恢复率 | `tool_error_recovery_rate` | 工具报错后仍完成任务的次数/工具报错总次数 | Agent的自愈能力 |
| 降级触发率 | `fallback_trigger_rate` | 触发fallback(模型切换/降级到dense)/总调用 | 越低越稳定 |
| 超时率 | `timeout_rate` | 超时的样本/总样本 | Agent循环卡死 |
| Reranker 可用率 | `reranker_availability_rate` | rerank成功/需要rerank的总次数 | 重排服务的稳定性 |

### 2.3 用户体验指标（User Experience Metrics）

| 指标 | 英文 | 计算方式 | 说明 |
|------|------|---------|------|
| 首 token 延迟 | `ttft_ms` | 首个输出token的时间戳-请求开始时间 | 感知速度 |
| 总响应延迟 | `e2e_latency_ms` | 最后一个token的时间戳-请求开始时间 | 全链路耗时 |
| 检索阶段耗时 | `retrieval_phase_ms` | 首次search到最后一次rerank的耗时 | 纯检索阶段 |
| 生成阶段耗时 | `generation_phase_ms` | 蒸馏/回答生成的耗时 | 纯生成阶段 |

### 2.4 端到端任务完成指标（End-to-End Task Metrics）

| 指标 | 英文 | 计算方式 | 说明 |
|------|------|---------|------|
| 答案准确性 | `answer_accuracy` | 人工/LLM判断答案是否正确 | 粗粒度 0/1 |
| 信息来源标注率 | `citation_coverage_rate` | 有来源标注的事实点/总事实点 | Agent的溯源能力 |
| KB/搜索路由准确率 | `routing_accuracy` | 正确选择信息源的次数/总样本 | 意图识别质量 |
| 多源综合质量 | `multi_source_quality` | 同时使用KB+搜索时的答案完整性 | 复杂任务能力 |

## 3. 测试数据集设计

### 3.1 场景分类

在现有 RAG 评估数据集（keyword/negation/vague/fact 四类×easy/medium/hard 三级）基础上，
新增 **Agent场景维度**：

| 场景 | 说明 | 预期路由 | 占比 |
|------|------|---------|------|
| **kb_simple** | 简单KB问题，1次检索即可 | retriever | 20% |
| **kb_complex** | 复杂KB问题，需多轮迭代 | retriever(多轮) | 20% |
| **search_simple** | 简单搜索问题 | searcher | 15% |
| **search_deep** | 需要深度搜索+页面抓取 | searcher(多轮) | 10% |
| **hybrid** | 同时需要KB+搜索 | retriever+searcher | 15% |
| **chitchat** | 闲聊/常识，不需要工具 | direct answer | 10% |
| **out_of_scope** | 超出能力范围的问题 | graceful decline | 10% |

### 3.2 数据集构造方式

**路径 A**: 复用现有 RAG 数据集中的 kb_simple/kb_complex 部分
**路径 B**: 新增 search/hybrid/chitchat/out_of_scope 类型的测试样本
  - search类: 手工构造（或LLM辅助生成）需要联网搜索的问题
  - hybrid类: 混合KB+搜索意图的问题
  - chitchat: 日常对话、打招呼、常识问答
  - out_of_scope: 代码调试、数学计算、个人隐私等超出RAG范围的问题

**路径 C**: 构造压力测试样本
  - 超长问题（>1000字）
  - 连续多轮对话
  - 工具错误注入（模拟reranker不可用、搜索超时）

### 3.3 数据格式扩展

```json
{
  "id": "agent_001",
  "user_input": "K8s中GPU显存隔离有哪些方式？",
  "scenario": "kb_complex",
  "expected_routing": ["retriever"],
  "expected_tools": ["search_knowledge", "get_recall_nodes", "rerank_recall_pool"],
  "expected_tool_count_range": [3, 8],
  "expected_iteration_range": [1, 3],
  "reference": "参考答案...",
  "reference_contexts": ["..."],
  "difficulty": "medium",
  "tags": ["multi-hop", "reranker-required"]
}
```

## 4. 评估执行框架

### 4.1 架构设计

```
agent_eval/
├── DESIGN.md          # 本文件
├── metrics.py         # 指标计算（效率+鲁棒性+UX+端到端）
├── runner.py          # 评估执行器（调用Agent，收集trace）
├── trace_collector.py # Agent执行trace收集（工具调用/延迟/错误）
├── report.py          # 报告生成（Markdown+JSON）
├── dataset.py         # Agent测试数据集加载/构造
└── data/
    └── datasets/      # Agent评估数据集
```

### 4.2 Trace 收集机制

在 Agent 执行过程中收集结构化 trace：

```python
@dataclass
class AgentTrace:
    sample_id: str
    scenario: str
    
    # 时间线
    start_time: float
    end_time: float
    first_token_time: float | None  # TTFT
    
    # 工具调用记录
    tool_calls: list[ToolCallRecord]
    
    # 路由决策
    sub_agents_used: list[str]      # ["retriever", "searcher"]
    routing_correct: bool | None    # 与expected_routing对比
    
    # 结果
    final_answer: str
    task_completed: bool            # 非空有意义回答
    error: str | None
    
    # 资源消耗
    total_tokens: int
    
    # 原始事件流（可选，用于深度分析）
    raw_events: list[dict] | None

@dataclass  
class ToolCallRecord:
    tool_name: str
    timestamp: float
    duration_ms: float
    input_preview: str    # 输入参数摘要
    output_preview: str   # 输出摘要(前200字)
    success: bool
    error: str | None
    round: int             # 第几轮迭代
```

**收集方式**: 在现有 `astream_events` 循环中埋点，不侵入 Agent 代码。
具体在 `chat.py` 的 `event_generator()` 中扩展事件处理逻辑。

### 4.3 评估执行流程

```
Phase 1: Trace Collection
  对每个测试样本:
    1. 发送问题到 Agent
    2. 收集 astream_events 中的所有事件
    3. 解析为 AgentTrace 结构
    4. 保存原始 trace JSON

Phase 2: Metric Computation  
  从 AgentTrace 计算:
    - 效率指标 (tool_call_count, iteration_rounds, ...)
    - 鲁棒性指标 (task_completion_rate, fallback_trigger_rate, ...)
    - UX指标 (ttft_ms, e2e_latency_ms, ...)
    - 端到端指标 (answer_accuracy, routing_accuracy, ...)

Phase 3: RAGAS Computation (复用现有)
  对有 reference 的样本，额外跑 RAGAS 四指标

Phase 4: Report Generation
  汇总所有指标，生成:
    - 总体指标表
    - 按场景/难度/类型的分指标
    - 优化建议
    - 对比基线（可选：vs dense/hybrid）
```

### 4.4 与现有 RAG 评估的关系

**不替代，而是扩展**：
- RAG评估: 专注检索+生成质量（CP/CR/Faith/FC）
- Agent评估: 专注链路效率+鲁棒性+UX
- 两者可复用同一数据集，结果可交叉分析

**复用关系**：
- `rag_eval/dataset.py` → 复用数据集加载
- `rag_eval/evaluate.py` → 复用 RAGAS 指标计算
- `rag_eval/report.py` → 复用报告生成框架
- `agent_eval/runner.py` → 新增 trace 收集 + Agent执行

## 5. 对比基线设计

| 基线 | 说明 | 对比维度 |
|------|------|---------|
| **Dense RAG** | 纯向量检索+生成 | 效率 vs 质量权衡 |
| **Hybrid RAG** | Dense+BM25+AutoMerging | Agent额外开销是否值得 |
| **Agent (无Reranker)** | Agent但不调用rerank | Reranker的效率收益 |
| **Agent (不同模型)** | deepseek-v4-flash vs sensenova-6.7-flash-lite | 模型对Agent效率的影响 |

## 6. 优化闭环

评估 → 发现瓶颈 → 优化 → 重评估：

1. **效率瓶颈发现**: 工具冗余率高 → 优化 Prompt 减少不必要调用
2. **鲁棒性瓶颈发现**: 工具错误恢复率低 → 增强容错中间件
3. **UX瓶颈发现**: TTFT高 → 预加载/流式优化/缓存
4. **路由瓶颈发现**: 路由准确率低 → 改进意图识别 Prompt
5. **检索→蒸馏瓶颈**: 转化率低 → 优化蒸馏 Prompt

## 7. 实施计划

### Step 1: 基础框架 (1天)
- `agent_eval/trace_collector.py` — trace收集+结构化
- `agent_eval/metrics.py` — 4类指标计算
- `agent_eval/runner.py` — 评估执行器

### Step 2: 数据集+集成 (0.5天)
- `agent_eval/dataset.py` — 扩展场景类型
- 与 `rag_eval` 集成（复用RAGAS）

### Step 3: 报告+对比 (0.5天)
- `agent_eval/report.py` — Agent评估报告
- 对比基线跑分

### Step 4: LLM路由修复 + 端到端验证 (0.5天)
- 修复 config.py 路由优先级
- 用 sensenova 无限额度跑完整评估

总计: ~2.5天
