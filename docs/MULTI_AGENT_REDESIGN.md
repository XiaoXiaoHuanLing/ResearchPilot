# ResearchPilot 多智能体编排重构设计

> 创建时间：2026-04-14
> 状态：设计评审中
> 前置参考：Open Deep Research / DeepAgents / LangGraph multi_agent examples

---

## 一、设计哲学

### 1.1 核心原则

1. **任务决定模式，而非模式决定任务**
   - 简单任务走 ReAct（快，1-2 步完成）
   - 复杂任务走 Plan-then-Execute（准，先规划再执行）
   - 混合任务走 Macro-Plan + Micro-ReAct（宏观规划，局部反思）

2. **规则优先，LLM 兜底**
   - 意图识别：关键词/规则匹配 → 快速路由（0ms LLM）
   - 规则匹配不了 → LLM 兜底（准确但慢）
   - 绝不让 LLM 做规则能做的事

3. **成本与质量可配置**
   - 不同任务类型可用不同模型
   - 研究深度可调（迭代次数、搜索次数上限）
   - 快速模式 vs 深度模式用户可选

4. **反思贯穿始终**
   - Supervisor 有 think_tool（战略反思：我该派谁？够了没？）
   - Worker 有执行反思（搜索结果够不够？要不要再搜？）
   - Plan 有动态调整（执行中发现规划不对，可修正）

### 1.2 与现有架构的关系

| 现有 | 重构后 | 变化 |
|------|--------|------|
| 3 固定 Worker 角色 | N 个可注册 Worker | 角色可扩展 |
| JSON prompt 分解 | Tool calling 派发 | 稳定性质变 |
| 固定顺序串行路由 | 动态并行调度 | 效率质变 |
| Worker 单次执行 | Worker 可多轮反思 | 质量质变 |
| result[:500] 截断 | 语义压缩 | 信息完整性 |
| 同一模型干所有事 | 按任务分化模型 | 成本+质量双优 |
| 无澄清机制 | 可选澄清节点 | 体验提升 |

---

## 二、整体架构

### 2.1 端到端流程

```
用户消息
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│  ① Router — 意图识别 + 模式选择                          │
│                                                         │
│  规则引擎（关键词/模式匹配，0ms）                          │
│    ├─ "列出专题" / "系统状态" → 直接路由到 Worker          │
│    ├─ "搜索XXX" → ReAct 模式 + researcher               │
│    └─ 复杂/模糊 → LLM 意图分类（兜底）                    │
│                                                         │
│  输出: { mode, task_type, workers_needed, clarification }│
└──────┬──────────────────────────────────────────────────┘
       │
       ├─ 需要澄清? → 返回追问，等用户补充后重新进入
       │
       ▼
┌─────────────────────────────────────────────────────────┐
│  ② Planner — 任务规划（仅 Plan 模式触发）                 │
│                                                         │
│  LLM.with_structured_output(Plan) → 生成任务列表          │
│  每个任务: { id, description, worker, depends_on[],      │
│            priority, estimated_steps }                   │
│                                                         │
│  规划原则:                                               │
│  - 默认 1 个 Worker 够用的不拆分                         │
│  - 对比/分区类任务才并行拆分                              │
│  - 依赖关系用 depends_on 表达                            │
│  - 有 think_tool 允许 Planner 反思                       │
└──────┬──────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────┐
│  ③ Supervisor — 执行调度（核心循环）                      │
│                                                         │
│  ┌──────── Supervisor Loop ────────┐                     │
│  │                                │                     │
│  │  检查待执行任务                  │                     │
│  │    ├─ 无依赖 → 并行派发          │                     │
│  │    └─ 有依赖 → 等前置完成         │                     │
│  │                                │                     │
│  │  执行 Worker（子图）             │                     │
│  │    → 收集结果                    │                     │
│  │    → think_tool 反思:           │                     │
│  │       "结果够了吗？需要调整规划吗？"│                    │
│  │                                │                     │
│  │  决策:                          │                     │
│  │    ├─ ReplanTask → 修改后续规划  │                     │
│  │    ├─ ConductTask → 派发新任务   │                     │
│  │    └─ TaskComplete → 标记完成    │                     │
│  │                                │                     │
│  │  所有任务完成? → 退出循环         │                     │
│  └────────────────────────────────┘                     │
│                                                         │
│  关键: Supervisor 通过 tool calling 派发和决策            │
│  工具: ConductTask / ReplanTask / TaskComplete / think   │
└──────┬──────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────┐
│  ④ Synthesizer — 结果综合                                │
│                                                         │
│  单任务 → 直接透传 Worker 结果                             │
│  多任务 → LLM 综合摘要                                    │
│  含报告需求 → 生成结构化报告                               │
└──────┬──────────────────────────────────────────────────┘
       │
       ▼
     返回用户（SSE 流式推送全程）
```

### 2.2 两种执行模式详解

#### ReAct 模式（简单任务）

```
用户: "搜索最新AI新闻"
  │
  ▼ Router: { mode: "react", worker: "researcher" }
  │
  ▼ Supervisor: 直接派发 researcher
  │
  ▼ Researcher 子图（ReAct 循环）:
  │   search_web("最新AI新闻")
  │   → think: "结果3条，够了"
  │   → 返回发现
  │
  ▼ Synthesizer: 直接透传
  │
  ▼ 用户收到回答
```

**特点**: 不经过 Planner，Supervisor 只做一次派发，Worker 内部 ReAct 循环自决。

#### Plan-then-Execute 模式（复杂任务）

```
用户: "研究中国舰船发展现状，对比055大驱和052D，生成报告"
  │
  ▼ Router: { mode: "plan", complexity: "high" }
  │
  ▼ Planner: 生成任务列表
  │   Task 1: researcher - "搜索中国舰船最新发展动态"      (priority: 1)
  │   Task 2: researcher - "搜索055大驱技术参数和部署情况"  (priority: 1, parallel with 3)
  │   Task 3: researcher - "搜索052D技术参数和部署情况"    (priority: 1, parallel with 2)
  │   Task 4: analyst   - "对比055和052D的技术差异"        (depends_on: [2, 3])
  │   Task 5: manager   - "查询本地知识库相关资料"          (priority: 1, parallel with 1)
  │   Task 6: analyst   - "生成综合研究报告"               (depends_on: [1, 4, 5])
  │
  ▼ Supervisor: 调度执行
  │   Round 1: 并行派发 Task 1, 2, 3, 5 (无依赖)
  │   Round 2: Task 2,3 完成 → 派发 Task 4 (依赖满足)
  │            Task 1 完成 → think: "舰船动态已覆盖"
  │   Round 3: Task 4,5 完成 → 派发 Task 6 (依赖满足)
  │   Round 4: Task 6 完成 → think: "所有任务完成"
  │
  ▼ Synthesizer: Task 6 的报告直接透传
  │
  ▼ 用户收到报告
```

**特点**: 宏观 Plan（任务分解+依赖+并行），微观 ReAct（每个 Worker 内部反思循环）。

### 2.3 Plan 动态调整

Supervisor 在执行循环中可通过 `ReplanTask` 调整规划：

```
场景: Task 1 researcher 返回"AI新闻搜索结果很少"
  │
  ▼ Supervisor think: "信息不足，需要换个角度搜"
  │
  ▼ ReplanTask:
     - 新增 Task 7: researcher - "搜索AI行业报告和白皮书"
     - 修改 Task 4: 增加新搜索结果作为输入
  │
  ▼ 继续执行调整后的计划
```

---

## 三、核心组件设计

### 3.1 Router（意图识别 + 模式选择）

```python
# ─── 规则引擎（0ms，不走 LLM）───

ROUTING_RULES = {
    # 直接路由：关键词匹配 → 单 Worker ReAct
    "list_topics|列出专题|所有专题": {"mode": "react", "worker": "manager"},
    "system_status|系统状态": {"mode": "react", "worker": "manager"},
    "create_topic|新建专题": {"mode": "react", "worker": "manager"},
    "search|搜索|查找|最新|最近": {"mode": "react", "worker": "researcher"},
    "rag_query|知识库问答|查知识库": {"mode": "react", "worker": "analyst"},
    "generate_report|生成报告|写报告": {"mode": "react", "worker": "analyst"},
    "bookmark|收藏|入库": {"mode": "react", "worker": "manager"},
    "upload|上传文档": {"mode": "react", "worker": "manager"},
    
    # Plan 模式触发词
    "对比|比较|区别|分析.*并.*|研究.*并.*": {"mode": "plan"},
    "深入.*研究|全面.*分析|综合.*报告": {"mode": "plan"},
}

# ─── LLM 兜底（规则匹配失败时）───

class IntentResult(BaseModel):
    """LLM 意图识别结构化输出"""
    mode: Literal["react", "plan"]
    task_type: str  # search / analysis / management / report / mixed
    workers_needed: list[str]  # ["researcher", "analyst"]
    needs_clarification: bool
    clarification_question: str | None = None
    complexity: Literal["simple", "moderate", "complex"]
```

**规则匹配优先级**: 精确匹配 > 关键词包含 > LLM 兜底

### 3.2 Planner（任务规划）

```python
class TaskPlan(BaseModel):
    """结构化任务规划"""
    research_brief: str  # 研究简报（用户意图的精炼描述）
    tasks: list[TaskItem]

class TaskItem(BaseModel):
    """单个任务项"""
    id: str              # "task_1"
    description: str      # "搜索055大驱技术参数"
    worker: Literal["researcher", "analyst", "manager"]
    depends_on: list[str] # ["task_2", "task_3"] 依赖的任务 ID
    priority: int         # 1=高, 2=中, 3=低
```

**Planner 原则**（来自 DeepAgents 最佳实践）:
- **默认 1 个 Worker**：大多数问题一个 researcher 就够了
- **只在明确对比/分区时才并行拆分**：如"比较 A vs B"→ 2 个 researcher
- **不要过早分解**："研究 X" 不要拆成 "研究 X 概述" + "研究 X 技术" + "研究 X 应用"
- **依赖最小化**：尽量让任务独立可并行

### 3.3 Supervisor（调度循环）

**核心: 通过 tool calling 派发，而非 JSON prompt**

```python
from langchain_core.tools import tool

@tool
def ConductTask(task_id: str, worker: str, description: str) -> dict:
    """派发任务给指定 Worker 执行。
    
    Args:
        task_id: 任务 ID
        worker: Worker 名称 (researcher/analyst/manager)
        description: 任务描述
    """
    ...

@tool  
def ReplanTask(reason: str, new_tasks: list[dict], modify_tasks: list[dict]) -> dict:
    """调整规划：新增或修改任务。
    
    在执行中发现原规划不够或方向有误时使用。
    
    Args:
        reason: 调整原因
        new_tasks: 新增的任务列表
        modify_tasks: 需要修改的任务列表
    """
    ...

@tool
def think(reflection: str) -> str:
    """战略反思：评估当前进展，决定下一步。
    
    每次收到 Worker 结果后使用，思考：
    - 信息是否充分？
    - 是否需要调整规划？
    - 还需要补充什么？
    """
    return f"反思记录: {reflection}"

@tool
def TaskComplete(summary: str) -> dict:
    """所有任务完成，准备综合结果返回用户。
    
    Args:
        summary: 对最终结果的简要概述
    """
    ...
```

**Supervisor Graph**:

```
START → supervisor → supervisor_tools → supervisor (循环)
                              │
                              ├─ ConductTask → 执行 Worker → ToolMessage 返回
                              ├─ ReplanTask → 修改 plan state → 继续
                              ├─ think → 记录反思 → 继续
                              └─ TaskComplete → END → Synthesizer
```

**关键设计**:
- Supervisor 只用 4 个 tool，没有 24 个工具
- `ConductTask` 可一次调用多次（并行派发），Supervisor 决定哪些并行
- Worker 结果以 `ToolMessage` 形式自然回到 supervisor 的消息流
- `think` 不执行操作，只记录反思，让 Supervisor "慢思考"
- 退出条件: `TaskComplete` / 超过 `max_supervisor_iterations` / 无待执行任务

### 3.4 Worker 子图（微观 ReAct）

每个 Worker 是一个独立的子图，有自己的 ReAct 循环：

```
researcher 子图:
  ┌── researcher ──→ researcher_tools ──┐
  │         ▲                          │
  │         └──── 还需继续? ───────────┘
  │                                     
  └── 够了 / 超限 → compress → 返回
     
researcher_tools:
  - search_web / ingest_url / collect_topic / list_topics
  - think_tool（搜索后反思：够了没？）
  
analyst 子图:
  ┌── analyst ──→ analyst_tools ──┐
  │       ▲                       │
  │       └── 还需继续? ──────────┘
  │                                
  └── 够了 → compress → 返回
  
analyst_tools:
  - rag_query / rag_stats / generate_report / list_reports
  - think_tool
  
manager 子图:
  单次执行（CRUD 操作无需多轮反思）
  - topic/article/KB CRUD + system status + context files
```

**Researcher 停止条件**（来自 Open Deep Research 最佳实践）:
1. 能完整回答用户问题
2. 已有 3+ 个相关来源
3. 最近 2 次搜索返回类似信息（信息收敛）
4. 达到 `max_search_calls` 上限（简单=3，复杂=5）
5. 达到 `max_researcher_iterations` 上限

### 3.5 Compress（语义压缩）

```python
async def compress_research(findings: list[str], topic: str) -> str:
    """将研究发现压缩为精炼摘要（不是截断）。
    
    保留: 关键发现、数据点、引用来源
    去除: 重复内容、无关细节、原始网页噪音
    """
    llm = get_chat_llm(model=config.compression_model, max_tokens=1000)
    result = await llm.ainvoke([
        SystemMessage(content="将以下研究发现压缩为精炼摘要。保留关键发现和数据，去除重复和噪音。"),
        HumanMessage(content=f"研究主题: {topic}\n\n原始发现:\n{chr(10).join(findings)}"),
    ])
    return result.content
```

**为什么不是截断**: 
- 截断 `result[:500]` 丢掉的可能正好是最关键的结论
- 压缩保留语义核心，体积减少 60-80% 但信息密度高
- Supervisor 收到压缩结果后能做出更好的调度决策

---

## 四、State 设计

```python
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    """顶层 Agent 状态"""
    messages: Annotated[list, add_messages]    # 用户消息 + 最终回复
    mode: str                                   # "react" | "plan"
    research_brief: str                         # 研究简报
    tasks: list[dict]                           # 任务列表 (TaskItem)
    completed_tasks: list[dict]                 # 已完成任务 (含 result)
    supervisor_messages: Annotated[list, add_messages]  # Supervisor 内部消息
    research_iterations: int                    # Supervisor 循环计数
    notes: list[str]                            # 研究笔记 (所有 Worker 发现)

class SupervisorState(TypedDict):
    """Supervisor 子图状态"""
    supervisor_messages: Annotated[list, add_messages]
    tasks: list[dict]
    completed_tasks: list[dict]
    research_iterations: int
    notes: list[str]

class ResearcherState(TypedDict):
    """Researcher 子图状态"""
    researcher_messages: Annotated[list, add_messages]
    research_topic: str
    tool_call_iterations: int
    raw_notes: list[str]

class AnalystState(TypedDict):
    """Analyst 子图状态"""
    analyst_messages: Annotated[list, add_messages]
    task_description: str
    tool_call_iterations: int
```

---

## 五、SSE 流式设计

### 5.1 事件类型

```typescript
// 前端接收的 SSE 事件
type SSEEvent = 
  | { type: "router", mode: "react" | "plan", task_type: string }
  | { type: "plan", tasks: TaskItem[] }
  | { type: "worker_start", worker: string, task_id: string }
  | { type: "token", content: string, worker?: string }
  | { type: "tool_start", tool: string, worker?: string }
  | { type: "tool_end", tool: string, result_preview: string, worker?: string }
  | { type: "worker_done", worker: string, task_id: string }
  | { type: "think", reflection: string }  // Supervisor 反思
  | { type: "replan", reason: string, new_tasks: TaskItem[] }
  | { type: "progress", completed: number, total: number }
  | { type: "done", thread_id: string, mode: string }
```

### 5.2 关键改进

1. **过滤 Supervisor 内部 token**: supervisor 节点的 LLM 输出（tool calling 的参数）不推给前端，只推送结构化事件
2. **think 事件**: Supervisor 反思时推送 "正在评估进展..." 类进度提示
3. **replan 事件**: 规划调整时推送 "发现信息不足，补充搜索任务"
4. **progress 事件**: 前端可展示进度条 "3/6 任务完成"

---

## 六、配置化

```python
class AgentConfiguration(BaseModel):
    """Agent 行为可配置项"""
    
    # ─── 模型选择 ───
    supervisor_model: str = "qwen3.5-35b-a3b"          # 调度：轻量快
    researcher_model: str = "qwen3.5-plus-2026-02-15"  # 研究：质量优先
    analyst_model: str = "qwen3.5-35b-a3b"             # 分析：中等
    compression_model: str = "qwen3.5-flash-2026-02-23" # 压缩：快且便宜
    final_report_model: str = "qwen3.5-35b-a3b"        # 报告：中等
    
    # ─── 行为参数 ───
    max_concurrent_workers: int = 3       # 最多并行 Worker 数
    max_supervisor_iterations: int = 6    # Supervisor 最大循环次数
    max_researcher_iterations: int = 3    # Researcher 最大搜索反思轮数
    max_react_tool_calls: int = 10        # Worker 单轮最大工具调用
    allow_clarification: bool = True      # 是否允许澄清追问
    allow_replan: bool = True             # 是否允许动态调整规划
    
    # ─── 快速/深度模式 ───
    research_depth: Literal["quick", "standard", "deep"] = "standard"
    # quick: 1 轮搜索, max_search_calls=2
    # standard: 2-3 轮, max_search_calls=5
    # deep: 3-5 轮, max_search_calls=8
```

---

## 七、与现有代码的兼容性

### 7.1 单 Agent 模式保留

现有的 `create_react_agent` + 24 工具单 Agent 模式**完全保留**，作为快速模式：
- 简单对话/工具调用 → 走单 Agent（最快）
- 复杂研究任务 → 走 Supervisor 多 Agent（更准）

### 7.2 前端适配

- 前端已有 `useSupervisor` 开关，改为 `agentMode: "single" | "supervisor"`
- SSE 事件类型扩展，前端按 type 分发渲染
- 前端可展示: 任务列表/进度/当前 Worker/反思日志

### 7.3 工具层不变

24 个工具（tools/ 下 8 个文件）完全不动，Worker 按角色选择子集：
- researcher: search + topic(list_only)
- analyst: rag + report
- manager: topic + article + KB + system + context

---

## 八、改造路线

### Step 1: Supervisor Tool Calling 派发（1-2 天）

**改什么**: supervisor.py 重写
- 去掉 JSON prompt + _parse_tasks
- Supervisor 用 bind_tools([ConductTask, ReplanTask, think, TaskComplete])
- supervisor_tools 节点执行 tool calls
- ConductTask 触发 Worker 子图

**不改什么**: Worker 暂时保持 create_react_agent，工具层不动

**验证**: 复杂任务（如对比搜索）能正确分解+并行执行

### Step 2: Router + Planner（1 天）

**改什么**: agent/__init__.py 加 Router + Planner 逻辑
- 规则引擎关键词匹配
- LLM 兜底意图识别
- Plan 模式触发 Planner 生成任务列表

**验证**: 简单任务走 ReAct 快速通道，复杂任务走 Plan

### Step 3: Worker 子图改造（1-2 天）

**改什么**: workers.py 从 create_react_agent 改为 StateGraph 子图
- 加 think_tool 反思
- 加停止条件判断
- 加 compress_research 压缩
- researcher 多轮搜索循环

**验证**: Researcher 能多轮搜索+反思+停止

### Step 4: SSE 优化 + 前端适配（1 天）

**改什么**: copilot.py SSE 事件
- 过滤 Supervisor 内部 token
- 加 think/replan/progress 事件
- 前端渲染适配

**验证**: 前端能看到任务进度和 Worker 切换

### Step 5: 配置化 + 模型分化（0.5 天）

**改什么**: 加 AgentConfiguration
- 不同 Worker 可配置不同模型
- research_depth 可调
- 前端可切换快速/标准/深度模式

---

## 九、关键决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| Supervisor 派发方式 | Tool calling | 比 JSON prompt 稳定 10 倍，原生支持多轮循环 |
| 简单任务路由 | 规则引擎优先 | 0ms vs ~2s LLM 调用，90% 请求可规则匹配 |
| Worker 结果返回 | 语义压缩 | 截断丢信息，压缩保留核心 |
| 并行调度 | Supervisor 自主决定 | 比固定顺序灵活，比全并行可控 |
| Plan 动态调整 | ReplanTask tool | 执行中发现问题可修正，不是一条路走到黑 |
| 混合模式 | Macro-Plan + Micro-ReAct | 复杂任务需要规划，但规划内部需要反思 |
| 单 Agent 保留 | 作为快速模式 | 简单问题不需要过 Supervisor，省 2-3 次 LLM 调用 |
