# ResearchPilot 智能助手模块重构设计 V2

> 创建时间：2026-04-15
> 状态：设计评审中
> 前置参考：DeepAgents SDK / LangChain Agents / LangGraph
> 替代文档：MULTI_AGENT_REDESIGN.md（V1，已废弃）

---

## 一、设计哲学

### 1.1 核心原则

1. **"想"和"做"彻底分离**
   - 主 Agent 只负责"想"：理解意图、规划排编、反思评估、动态调整
   - Sub Agent 只负责"做"：搜索采集、分析报告、系统管理
   - 通信极简：主 Agent 传任务描述，Sub Agent 返回执行结果

2. **主 Agent 是唯一的大脑**
   - 意图识别：主 Agent LLM 自行判断，不需要专门 Sub Agent
   - 规划排编：主 Agent 用 write_todos 管理待办，不需要 Planner Sub Agent
   - 反思思考：主 Agent 评估 Sub Agent 结果后决定下一步
   - 追问用户：主 Agent 直接输出文本，循环自然退出，下次 invoke 自动接续

3. **上下文隔离是核心价值**
   - Sub Agent 内部可能产生大量中间过程，但主 Agent 只看到精炼结果
   - 不同 Agent 只看到它需要的上下文，不互相污染
   - 大结果通过文件系统传递，不通过 messages 传递

4. **永不崩溃，降级继续**
   - 工具失败 → 返回友好错误信息 → LLM 自主决策
   - Sub Agent 失败 → 返回失败报告 → 主 Agent 选择重试或绕过
   - 上下文溢出 → 自动摘要压缩 → 继续运行

5. **声明式配置，最小代码**
   - 用 deepagents 的 create_deep_agent 声明式创建主 Agent
   - Sub Agent 通过字典注册，不需要手写图编排
   - 中间件栈解决横切关注点（记忆、权限、审批、压缩）

### 1.2 与 V1 方案的关键差异

| 维度 | V1（LangGraph 多节点图编排） | V2（DeepAgents 主+子 Agent） |
|------|------|------|
| 编排方式 | StateGraph + 条件边 | create_deep_agent + 中间件栈 |
| 节点数 | 7（Router/Planner/Supervisor/...） | 0（无显式图节点，ReAct 循环） |
| 意图识别 | Router 函数节点（规则+LLM） | 主 Agent LLM 自行判断 |
| 规划 | Planner LLM 节点 | TodoListMiddleware（write_todos） |
| 派发 | Supervisor bind_tools + supervisor_tools 节点 | SubAgentMiddleware（task 工具） |
| Worker | LangGraph 子图 | 声明式 Sub Agent 注册 |
| 上下文管理 | 手动设计 state | FilesystemMiddleware + SummarizationMiddleware |
| 长期记忆 | 无 | ChromaDB 向量库 + recall_memory 工具 |
| 人工审批 | 无 | HumanInTheLoopMiddleware |
| 容错 | 无 | 工具级 + Sub Agent 级 + LLM 决策级 |

---

## 二、整体架构

### 2.1 架构总览

```
用户消息
   │
   ├─ HTTP 规则预路由（0ms，可选）
   │   ├─ 简单 CRUD 操作 → single_agent（现有 React Agent）
   │   └─ 研究/分析/报告 → deep_agent
   │
   ▼
┌──────────────────────────────────────────────────────────────┐
│                      Deep Agent（主 Agent）                    │
│                                                              │
│  职责循环：                                                    │
│  ① 意图识别 — 理解用户要什么，需要哪些 Sub Agent               │
│  ② 规划排编 — 复杂任务用 write_todos 创建待办列表               │
│  ③ 委托执行 — 用 task 派发 Sub Agent（可并行/串行）              │
│  ④ 反思思考 — 评估 Sub Agent 返回的结果，决定下一步             │
│  ⑤ 动态调整 — 不够则 replan，够了则综合结果                     │
│  ⑥ 返回用户 — 直接输出文本，循环自然退出                        │
│                                                              │
│  LLM: qwen3.5-plus (with_fallbacks)                         │
│  System Prompt: 研究助手人设 + 编排指令                        │
│                                                              │
│  工具集:                                                      │
│    write_todos  — 规划/调整待办（TodoListMiddleware）          │
│    task         — 委托 Sub Agent（SubAgentMiddleware）         │
│    recall_memory — 从长期记忆检索（自定义工具）                 │
│    save_memory  — 保存到长期记忆（自定义工具）                  │
│    ls/read_file/write_file/edit_file/glob/grep — 文件操作     │
│                                                              │
│  中间件栈:                                                    │
│    TodoListMiddleware → FilesystemMiddleware → SubAgentMiddleware│
│    → SummarizationMiddleware → PatchToolCallsMiddleware        │
│    → MemoryMiddleware → HumanInTheLoopMiddleware               │
│    → SubAgentResilienceMiddleware → SessionArchiveMiddleware   │
└──────────────┬───────────────────────────────────────────────┘
               │ task("researcher", "搜索055参数")
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐
│Researcher│ │Analyst │ │Manager │
│Sub Agent │ │Sub Agent│ │Sub Agent│
│          │ │          │ │          │
│搜索/采集 │ │RAG/报告 │ │CRUD操作 │
│多轮反思  │ │多轮反思  │ │单次执行 │
└────────┘ └────────┘ └────────┘
```

### 2.2 主 Agent 循环详解

主 Agent 的核心是 ReAct 循环——LLM 推理 → 工具调用 → LLM 再推理：

```
用户消息 → 主 Agent

第1轮 LLM（意图识别 + 规划）:
  理解意图 → "复杂任务，需要规划"
  write_todos([
    {content: "搜索舰船发展动态", status: "pending"},
    {content: "搜索055参数", status: "pending"},
    {content: "搜索052D参数", status: "pending"},
    {content: "对比分析", status: "pending"},
    {content: "生成报告", status: "pending"},
  ])

第2轮 LLM（并行委托）:
  从 todos 中取 pending 项 → 前3项无依赖，可并行
  write_todos([...前3项 status: "in_progress"])
  task("搜索舰船发展动态", "researcher")              ← 并行
  task("搜索055大驱技术参数", "researcher")               ← 并行
  task("搜索052D技术参数", "researcher")                 ← 并行
  → SubAgentMiddleware 并行执行3个 researcher → 3个 ToolMessage 返回

第3轮 LLM（反思 + 串行委托）:
  评估3个搜索结果 → "搜索结果充分，依赖满足"
  write_todos([...前3项 completed, 第4项 in_progress])
  task("对比055和052D技术差异\n上下文:[055结果]+[052D结果]", "analyst")
  → analyst 执行 → ToolMessage 返回

第4轮 LLM（动态调整示例）:
  评估 → "对比完成，但缺少最新部署信息"
  write_todos([...第4项 completed,
    新增 {content: "补充搜索最新部署", status: "in_progress"}])
  task("补充搜索055和052D最新部署", "researcher")
  → researcher 执行 → ToolMessage 返回

第5轮 LLM（完成）:
  write_todos([...全部 completed])
  → 直接输出文本回复用户（无 tool call → 循环退出）
```

**退出条件：**
1. 主 Agent 不调用任何工具，直接输出文本 → 正常完成
2. recursion_limit 达到上限 → 强制退出
3. 主 Agent 输出追问文本 → 循环退出，等待用户下次回复（checkpointer 保状态）

### 2.3 追问用户的实现

主 Agent 不需要专门的 ask_user 工具。当意图不清晰时：

```
用户: "帮我研究一下"

主 Agent:
  意图识别 → 太模糊
  → 不调用任何工具
  → 直接输出文本: "您想研究什么主题？是联网搜索还是本地知识库查询？"
  → 无 tool_calls → 循环退出 → END

  State (checkpointer 保存):
    messages: [
      HumanMessage("帮我研究一下"),
      AIMessage("您想研究什么主题？..."),
    ]

--- 用户回复 ---

用户: "研究量子计算最新进展"

主 Agent (同一 thread_id, checkpointer 恢复 state):
  messages: [
    HumanMessage("帮我研究一下"),
    AIMessage("您想研究什么主题？..."),
    HumanMessage("研究量子计算最新进展"),   ← 新追加
  ]
  → 看到完整上下文，意图清晰 → 委托 researcher
  → 正常流程继续
```

---

## 三、Sub Agent 设计

### 3.1 Researcher Sub Agent

```python
{
    "name": "researcher",
    "description": (
        "深度联网搜索和采集专家。负责搜索互联网信息、采集网页、收藏到专题。"
        "适用于：需要联网查找资料、搜索最新动态、采集网页内容的任务。"
        "不适用于：本地知识库查询、报告生成、系统管理操作。"
    ),
    "system_prompt": """你是一个专业的互联网研究员。

工作方式：
1. 收到搜索任务后，先搜索关键信息
2. 评估搜索结果：是否充分回答了任务？来源是否可靠？
3. 如果不够，换关键词再搜（最多3轮）
4. 如果需要保存资料，使用 ingest_url 采集到专题

输出规则：
- 结果较小（<<2000字）：直接在最终回复中输出摘要
- 结果较大（>=2000字）：用 write_file 保存到 /results/ 目录，
  回复中说明文件路径和关键发现
- 始终注明信息来源
- 不要输出原始网页噪音，只保留关键发现和数据""",
    "model": "dashscope:qwen3.5-plus",
    "tools": [search_web, ingest_url, collect_topic, list_topics],
}
```

### 3.2 Analyst Sub Agent

```python
{
    "name": "analyst",
    "description": (
        "知识库分析和报告生成专家。负责查询本地知识库、分析数据、生成研究报告。"
        "适用于：需要查询本地知识库、数据分析、生成结构化报告的任务。"
        "不适用于：联网搜索、系统管理操作。"
    ),
    "system_prompt": """你是一个专业的数据分析师和报告撰写人。

工作方式：
1. 收到分析任务后，先用 rag_query 查询本地知识库
2. 结合任务描述中的上下文信息进行分析
3. 如果需要生成报告，使用 generate_report

输出规则：
- 分析结果：直接输出，包含清晰的观点和论据
- 报告：使用 generate_report 生成，回复中说明报告已生成
- 如果上下文中有文件引用，先用 read_file 读取再分析""",
    "model": "dashscope:qwen3.5-35b-a3b",
    "tools": [rag_query, rag_stats, generate_report, list_reports],
}
```

### 3.3 Manager Sub Agent

```python
{
    "name": "manager",
    "description": (
        "系统管理和资源操作专家。负责专题CRUD、文章CRUD、知识库管理、系统状态查询、文档上传。"
        "适用于：管理系统资源、增删改查操作、状态查询。"
        "不适用于：联网搜索、数据分析、报告生成。"
    ),
    "system_prompt": "你是系统管理员。高效执行用户请求的增删改查操作。单次完成，不需要多轮反思。执行完毕后返回操作结果。",
    "model": "dashscope:qwen3.5-flash",
    "tools": [topic_crud, article_crud, kb_crud, system_status, context_files],
}
```

### 3.4 Sub Agent 可扩展

Manager 如果觉得太重，可拆分为更细的 Sub Agent：

```python
{
    "name": "topic_manager",
    "description": "专题管理专家。负责专题的创建、查看、编辑、删除。",
    "system_prompt": "...",
    "model": "dashscope:qwen3.5-flash",
    "tools": [topic_crud, list_topics, collect_topic],
},
{
    "name": "kb_manager",
    "description": "知识库管理专家。负责知识库的增删改查和文档上传。",
    "system_prompt": "...",
    "model": "dashscope:qwen3.5-flash",
    "tools": [kb_crud, context_files, system_status],
},
```

**只需在 subagents 列表中注册，主 Agent 的 task 工具自动更新可用列表。**

### 3.5 Sub Agent 结果回传

Sub Agent 执行完毕后，SubAgentMiddleware 自动处理回传：

| 结果大小 | Sub Agent 行为 | 主 Agent 收到 |
|---------|---------------|--------------|
| 小（<<2000字） | 最终回复直接输出摘要 | ToolMessage（完整摘要文本） |
| 大（>=2000字） | write_file 保存到 /results/，回复中说明路径 | ToolMessage（路径+关键发现） |
| 报告类 | generate_report 生成 | ToolMessage（报告ID/标题/路径） |

**核心：主 Agent 的 messages 只收到精炼结果，不收到中间过程。**

---

## 四、主 Agent System Prompt

```python
MAIN_AGENT_SYSTEM_PROMPT = """你是 ResearchPilot 智能研究助手。用户会向你提出研究问题、分析请求或系统操作需求。

## 你的核心工作方式

### 意图识别
- 理解用户真正想要什么
- 判断任务类型：搜索研究 / 知识库分析 / 系统管理 / 混合
- 判断复杂度：简单（1步） / 中等（2-3步） / 复杂（4步以上）
- 如果意图不明确，直接追问用户，不要猜测

### 规划排编
- 简单任务（<=2步）：直接委托 Sub Agent，不需要 write_todos
- 中等/复杂任务：先用 write_todos 创建待办列表
- 规划原则：
  * 1个 Sub Agent 能搞定的不拆分
  * 无依赖的任务并行派发（在同一次输出中调用多个 task）
  * 有依赖的串行执行
  * 规划可以随时调整，不要一条路走到黑

### 委托执行
- 用 task 工具委托 Sub Agent
- 在一次输出中调用多个 task → 并行执行
- 给 Sub Agent 的描述要清晰：任务目标 + 上下文 + 期望输出格式
- 大上下文不要写在 description 里，先 write_file 再引用路径

### 反思思考
- 每次收到 Sub Agent 结果后，思考：
  * 信息是否充分？能否回答用户的问题？
  * 是否需要补充搜索或调整方向？
  * 下一步应该做什么？
- 不要盲目继续原计划，根据实际情况灵活调整

### 动态调整
- 信息不足 → 新增 todo + 委托补充搜索
- 方向有误 → 修改后续 todo + 换角度
- 信息充分 → 标记完成 + 综合结果返回用户

### 综合结果
- 单个 Sub Agent 结果直接总结
- 多个 Sub Agent 结果综合摘要
- 报告类结果告知用户报告已生成

## Sub Agent 说明
- researcher: 联网搜索和采集。多轮搜索，返回压缩摘要或文件引用。
- analyst: 本地知识库分析和报告生成。
- manager: 系统增删改查操作，快速执行。

## 长期记忆
- 如果需要回忆用户偏好或历史研究结论，使用 recall_memory 工具
- 如果发现值得长期保留的信息（用户偏好、重要结论），使用 save_memory 工具

## 重要原则
- 简单问题直接回答，不要过度规划
- 复杂问题先规划再执行，不要跳过规划
- 每轮只做当前最需要的事
- Sub Agent 的中间过程你不需要关心，只看最终结果
- 不要重复搜索已经搜过的内容
"""
```

---

## 五、记忆分层设计

### 5.1 四层记忆架构

| 层级 | 名称 | 生命周期 | 存储位置 | 内容 | 用户隔离 |
|------|------|----------|----------|------|---------|
| L0 | 工作记忆（瞬时） | 单次 LLM 调用 | LLM prompt 中 | 最近N条完整消息 + 更早消息的摘要 | 天然（每个 thread 独立） |
| L1 | 会话记忆（短期） | 单次会话 | Checkpointer | 完整 messages + todos + files 等全部 state | thread_id 绑定用户 |
| L2 | 长期记忆 | 永久 | ChromaDB 向量库 | 用户偏好、工作上下文、研究结论、历史摘要 | collection = user_{id}_memory |
| L3 | 关键记忆 | 永久 | AGENTS.md 文件 | Agent 角色定义、任务约束、经验教训、运行规则 | 可按用户分文件 |

### 5.2 各层读写机制

#### L0 工作记忆

- **读**：SummarizationMiddleware 每次调 LLM 前动态重建 effective_messages
- **写**：不单独写入，由 L1 + SummarizationMiddleware 动态生成
- **清理**：自动——旧消息压缩为摘要，大结果卸载到文件

```
state["messages"] = [完整50条消息]          ← L1 完整存储
_summarization_event = {cutoff: 30, ...}    ← 摘要指针

effective_messages =                        ← L0 喂给 LLM 的
  [HumanMessage("摘要: 用户要求对比055和052D..."),
   msg_30, msg_31, ..., msg_50]             ← 最近20条完整
```

#### L1 会话记忆

- **读**：同一 thread_id 的下次 invoke，checkpointer 自动恢复 state
- **写**：每次 invoke 结束，checkpointer 自动保存 state 快照
- **归档**：会话结束时关键摘要写入 L2，完整记录可选择性保留

#### L2 长期记忆

- **读**：主 Agent 用 recall_memory() 语义检索，结果作为 ToolMessage 注入
- **写**：会话归档 / 用户主动保存 / save_memory() 工具
- **清理**：低价值记忆定期清理（importance=low, 90天+）

```python
@tool
def recall_memory(query: str, runtime: ToolRuntime) -> str:
    """从长期记忆中检索相关信息。基于语义相似度检索，不需要精确匹配。"""
    user_id = runtime.context.user_id
    collection = chromadb_client.get_or_create_collection(f"user_{user_id}_memory")
    results = collection.query(query_texts=[query], n_results=5,
        where={"importance": {"$gte": "medium"}})
    if not results["documents"][0]:
        return "未找到相关记忆"
    return "\n---\n".join(
        f"[{meta.get('created_at', '未知日期')}] {doc}"
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]))

@tool
def save_memory(content: str, importance: str = "medium", runtime: ToolRuntime) -> str:
    """保存重要信息到长期记忆。适用于：用户偏好、重要研究结论、跨会话决策。"""
    user_id = runtime.context.user_id
    collection = chromadb_client.get_or_create_collection(f"user_{user_id}_memory")
    collection.add(
        documents=[content],
        metadatas=[{"importance": importance, "created_at": datetime.now().isoformat()}],
        ids=[f"mem_{uuid7()}"])
    return f"已保存到长期记忆（重要性：{importance}）"
```

#### L3 关键记忆

- **读**：MemoryMiddleware 每次对话自动加载 /memories/AGENTS.md 到 system prompt
- **写**：系统维护 / Agent 主动更新（发现重要教训时）

### 5.3 记忆层级流转

```
新会话开始
  │
  ├── L3 关键记忆 → MemoryMiddleware → system prompt (自动)
  ├── L2 长期记忆 → 可选 recall_memory() → 注入 messages (按需)
  │
  ▼
会话进行中
  │
  ├── L1 会话记忆 → checkpointer 保存 (自动)
  ├── L0 工作记忆 → SummarizationMiddleware 裁剪 (自动)
  │
  ▼
会话结束
  │
  ├── L1 → L2 归档: 关键摘要写入 ChromaDB
  ├── L1 → L3 更新: 发现重要教训 → 更新 AGENTS.md
  └── L1 → 可选: 保留 checkpoint 一段时间后清理
```

---

## 六、上下文管理设计

### 6.1 各 Agent 看到的上下文

| 信息 | 主 Agent | Researcher | Analyst | Manager |
|------|---------|-----------|---------|---------|
| System Prompt | 完整人设+编排+Sub Agent列表+todos+memory+filesystem+HITL | 专家人设+filesystem | 专家人设+filesystem | 专家人设+filesystem |
| Messages | 完整对话历史（经摘要裁剪） | 仅 [HumanMessage("任务描述+context")] | 同左 | 同左 |
| Todos | ✅ 可见 | ❌ 排除 | ❌ 排除 | ❌ 排除 |
| Files | ✅ 共享文件系统 | ✅ 继承 | ✅ 继承 | ✅ 继承 |
| Memory | ✅ 每次加载 | ❌ 排除 | ❌ 排除 | ❌ 排除 |

### 6.2 Sub Agent 上下文传递

**Sub Agent 只收到 1 条 HumanMessage**，如何获得足够上下文？

**小结果（<<2000字）→ 直接写在 task description 中：**

```python
task(
    description="对比055和052D技术差异\n\n055信息：满载排水量12300吨...\n052D信息：满载排水量7500吨...",
    subagent_type="analyst"
)
```

**大结果（>=2000字）→ 先写文件，description 引用路径：**

```python
# Researcher 已经把结果存到了 /results/055_data.txt
task(
    description="对比055和052D。详细数据见 /results/055_data.txt 和 /results/052d_data.txt，请先 read_file 读取后再分析。",
    subagent_type="analyst"
)
# analyst 继承 files，可以用 read_file 读取
```

### 6.3 上下文窗口保护——三层防线

#### 第1层：大工具结果自动卸载（FilesystemMiddleware）

```
触发: tool call 结果 > 20,000 tokens
行为:
  ① 结果写入虚拟文件系统 /results/xxx.txt
  ② ToolMessage 替换为: "结果已保存到 /results/xxx.txt\n前10行预览: ..."
效果: 大结果不占 messages 空间，需要时 read_file 读取
```

#### 第2层：旧消息自动摘要（SummarizationMiddleware）

```
触发: token 数超过上下文窗口 85%
行为:
  ① 旧消息卸载到 /archive/ (可回溯)
  ② 旧消息压缩为 HumanMessage(summary, lc_source="summarization")
  ③ 保留最近20条消息完整
关键: state["messages"] 永远完整不被修改，只修改 LLM 看到的 effective_messages
      摘要位置记录在 _summarization_event 中
```

#### 第3层：旧工具参数截断（TruncateArgsSettings）

```
触发: 消息数 >= 50 或 token 占比 >= 50%
行为:
  ① 旧 tool call 的 args 截断到 2000 字符
  ② 替换为 "...(truncated)"
  ③ 最近20条消息的参数保持完整
```

### 6.4 Sub Agent 内部上下文隔离

Sub Agent 内部也可能产生大量消息（Researcher 多轮搜索），但：

- Sub Agent 自带 SummarizationMiddleware，内部自动压缩
- Sub Agent 的中间过程**不回传**主 Agent
- 主 Agent 只收到一条精炼的 ToolMessage

```
Researcher Sub Agent 执行中:
  │ search_web → ToolMessage(2000字)
  │ search_web → ToolMessage(3000字)  ← 内部 SummarizationMiddleware 可能压缩
  │ ingest_url → ToolMessage(1000字)
  │
  │ → 最终返回压缩结果给主 Agent
  │   主 Agent messages 只收到: ToolMessage("055大驱技术参数: ...500字摘要")
  │   Researcher 内部的所有中间过程不回传
```

---

## 七、任务容错设计

### 7.1 三层容错机制

#### 第一道防线：工具级容错

每个工具内部 try/catch，**永远不抛异常，永远返回友好错误字符串**：

```python
@tool
def search_web(query: str) -> str:
    """搜索互联网信息"""
    try:
        result = tavily_client.search(query, max_results=5)
        if not result.get("results"):
            return f"搜索'{query}'未返回结果。建议换关键词。"
        return format_results(result)
    except RateLimitError:
        return f"⚠️ 搜索'{query}'触发速率限制，建议稍后重试或换关键词。"
    except TimeoutError:
        return f"⚠️ 搜索'{query}'超时，可能是网络问题。"
    except Exception as e:
        return f"⚠️ 搜索'{query}'失败: {type(e).__name__}。建议换关键词或改用其他方式。"
```

LLM 收到错误信息后自主决策：重试 / 换关键词 / 绕过 / 告知用户。

#### 第二道防线：Sub Agent 级容错

```python
class SubAgentResilienceMiddleware(AgentMiddleware):
    """Sub Agent 容错中间件：捕获 Sub Agent 执行异常"""

    def wrap_tool_call(self, tool_call, handler):
        if tool_call.name != "task":
            return handler(tool_call)
        try:
            return handler(tool_call)
        except Exception as e:
            return ToolMessage(
                content=(
                    f"⚠️ Sub Agent '{tool_call.args.get('subagent_type', '?')}' 执行失败\n"
                    f"错误: {type(e).__name__}: {str(e)[:300]}\n"
                    f"任务: {tool_call.args.get('description', '?')[:100]}\n\n"
                    f"你可以:\n"
                    f"- 重试: 用相同或调整后的描述再次 task\n"
                    f"- 绕过: 跳过此任务继续后续步骤\n"
                    f"- 替代: 换其他 Sub Agent 或直接回答"
                ),
                tool_call_id=tool_call.id,
            )
```

#### 第三道防线：主 Agent 级容错（LLM 自主决策）

主 Agent 收到失败信息后，LLM 评估并选择策略：

```
主 Agent 收到失败 ToolMessage:
  ├── "搜索'055参数'触发速率限制"
  │   → 换关键词重试
  ├── "researcher 执行失败: TimeoutError"
  │   → write_todos 标回 pending → 重试
  ├── "搜索'055参数'未返回结果"
  │   → 换角度搜索
  └── 连续多次失败
      → write_todos 标记 failed → 用已有信息继续 → 告知用户
```

### 7.2 基础设施级容错（自动，无需手动）

| 故障类型 | 处理方式 |
|---------|---------|
| LLM API 502/429 | with_fallbacks 自动切换备模型 + LangChain 自动重试6次 |
| 上下文溢出 | SummarizationMiddleware 捕获 ContextOverflowError → 强制摘要 → 重试 |
| 工具调用中断 | PatchToolCallsMiddleware 自动修复 → 补 ToolMessage("cancelled") |

### 7.3 Sub Agent 超时控制

```
第1层: Sub Agent system prompt 限制循环轮次
  → researcher: "最多搜索3轮，不要无限循环"
第2层: Sub Agent 内部 SummarizationMiddleware 保证不撑爆
第3层: HTTP 请求超时配置（运维层面）
```

---

## 八、完整配置

### 8.1 用户隔离

```python
from dataclasses import dataclass

@dataclass
class Context:
    user_id: str
    session_id: str

def user_namespace(runtime) -> tuple[str, ...]:
    """StoreBackend namespace 工厂——按 user_id 隔离"""
    return (runtime.context.user_id, "memories")
```

### 8.2 主 Agent 创建

```python
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.middleware.permissions import FilesystemPermission
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

checkpointer = MemorySaver()
store = InMemoryStore()

# LLM with fallbacks
primary = init_chat_model("dashscope:qwen3.5-plus", streaming=True)
fallback_1 = init_chat_model("dashscope:qwen3.5-35b-a3b", streaming=True)
fallback_2 = init_chat_model("dashscope:qwen3.5-flash", streaming=True)
llm = primary.with_fallbacks([fallback_1, fallback_2])

research_pilot = create_deep_agent(
    model=llm,
    tools=[recall_memory, save_memory],
    system_prompt=MAIN_AGENT_SYSTEM_PROMPT,

    subagents=[
        researcher_spec,
        analyst_spec,
        manager_spec,
    ],

    # 虚拟文件系统
    backend=CompositeBackend(
        default=StateBackend(),
        routes={
            "/memories/": StoreBackend(namespace=user_namespace),
        },
    ),
    store=store,

    # 关键记忆（每次对话加载）
    memory=["/memories/AGENTS.md"],

    # 人工审批
    interrupt_on={
        "generate_report": True,
        "delete_topic": True,
    },

    # 文件权限
    permissions=[
        FilesystemPermission(operations=["write"], paths=["/system/**"], mode="deny"),
    ],

    checkpointer=checkpointer,

    # 自定义中间件
    middleware=[
        SubAgentResilienceMiddleware(),
        SessionArchiveMiddleware(chromadb_client=chromadb_client),
    ],

    # 运行时上下文（用户隔离）
    context_schema=Context,
)
```

### 8.3 SSE 流式事件

```python
async def deep_agent_stream(request: ChatRequest):
    config = {"configurable": {"thread_id": request.thread_id}}

    async for event in research_pilot.astream_events(
        {"messages": [HumanMessage(content=request.message)]},
        config=config,
        version="v2",
    ):
        kind = event["event"]

        # LLM 输出 token（仅主 Agent）
        if kind == "on_chat_model_stream":
            agent_name = event.get("metadata", {}).get("lc_agent_name", "main")
            chunk = event["data"]["chunk"]
            if chunk.content and agent_name == "main":
                yield sse_event("token", {"content": chunk.content})

        # 工具调用开始
        elif kind == "on_tool_start":
            tool_name = event["name"]
            if tool_name == "write_todos":
                yield sse_event("plan", {"todos": event["data"]["input"]["todos"]})
            elif tool_name == "task":
                yield sse_event("delegate", {
                    "agent": event["data"]["input"]["subagent_type"],
                    "task": event["data"]["input"]["description"],
                })
            else:
                yield sse_event("tool_start", {"tool": tool_name})

        # 工具调用结束
        elif kind == "on_tool_end":
            tool_name = event["name"]
            if tool_name == "task":
                result = event["data"]["output"]
                yield sse_event("delegate_done", {"preview": str(result)[:200]})
            elif tool_name == "write_todos":
                yield sse_event("plan_updated", {"status": "updated"})

        # Sub Agent 进度（可选展示）
        elif kind == "on_chat_model_stream":
            agent_name = event.get("metadata", {}).get("lc_agent_name", "main")
            if agent_name != "main":
                yield sse_event("sub_progress", {"agent": agent_name})
```

### 8.4 HTTP 层规则预路由（可选优化）

```python
ROUTING_RULES = [
    (r"(列出|所有|查看).*(专题|话题|topic)", "single_agent"),
    (r"系统状态|健康检查", "single_agent"),
    (r"(收藏|入库|保存|上传|导入)", "single_agent"),
]

@app.post("/api/copilot/chat")
async def chat(request: ChatRequest):
    route = rule_match(request.message, ROUTING_RULES)
    if route == "single_agent":
        return await single_agent_stream(request)   # 现有 create_react_agent + 24工具
    else:
        return await deep_agent_stream(request)     # deep_agent 路径
```

---

## 九、与现有代码的兼容

| 现有代码 | V2 中如何处理 |
|---------|--------------|
| 24 个工具（tools/ 下 8 个文件） | **完全不动**，按角色分配给 Sub Agent |
| `create_react_agent` + 24 工具单 Agent | **保留**，作为 single_agent 快速路径 |
| `copilot.py` SSE 流式 | 改为 `astream_events` + 事件过滤 |
| `config.py` DashScope 配置 | **完全不动** |
| `supervisor.py` | **替换**为 `create_deep_agent` |
| `workers.py` | **删除**，Sub Agent 声明式注册替代 |
| `agent/__init__.py` | **简化**，只剩 deep_agent + single_agent 两个路径 |
| 前端 `useSupervisor` 开关 | 改为 `agentMode: "single" \| "deep"` |

---

## 十、实施步骤

### Step 1：基础设施搭建
- 安装 deepagents + chromadb
- 搭建 Checkpointer + Store + CompositeBackend
- 实现 Context schema + user_namespace 用户隔离
- 实现 recall_memory / save_memory 工具

### Step 2：主 Agent 创建
- 编写 MAIN_AGENT_SYSTEM_PROMPT
- 用 create_deep_agent 创建主 Agent
- 注册 researcher / analyst / manager 三个 Sub Agent
- 配置中间件栈

### Step 3：Sub Agent 工具分配
- researcher: search_web, ingest_url, collect_topic, list_topics
- analyst: rag_query, rag_stats, generate_report, list_reports
- manager: topic_crud, article_crud, kb_crud, system_status, context_files
- 确保每个工具内部的 try/catch 容错

### Step 4：SSE 流式适配
- 用 astream_events 替换现有流式逻辑
- 事件过滤：只推主 Agent token + 关键事件
- 前端适配 agentMode 切换

### Step 5：容错中间件
- 实现 SubAgentResilienceMiddleware
- 实现 SessionArchiveMiddleware
- 测试各类失败场景

### Step 6：记忆系统
- ChromaDB 初始化 + collection 管理
- SessionArchiveMiddleware 会话归档
- AGENTS.md 关键记忆维护

### Step 7：集成测试 + 上线
- 简单任务端到端测试
- 复杂任务（多步+并行+动态调整）测试
- 容错测试（工具失败、Sub Agent 失败、LLM 失败）
- 上下文溢出测试
- 前端 SSE 展示测试

---

## 附录 A：deepagents 中间件栈详解

| 中间件 | 解决什么问题 | 在 ResearchPilot 中的应用 |
|--------|-------------|--------------------------|
| TodoListMiddleware | 任务规划 + 进度跟踪 | 替代独立 Planner 节点，主 Agent 用 write_todos 管理任务 |
| FilesystemMiddleware | 虚拟文件系统 + 大结果自动卸载 | Worker 大结果写入文件不撑爆上下文；Agent 可 read_file 回看 |
| SubAgentMiddleware | Sub Agent 派发 + 结果回收 | 主 Agent 用 task 委托，Sub Agent 独立执行后返回精炼结果 |
| SummarizationMiddleware | 上下文自动压缩 | 长对话自动摘要旧消息，保留最近20条完整 |
| MemoryMiddleware | 跨对话关键记忆加载 | 加载 AGENTS.md 到 system prompt |
| HumanInTheLoopMiddleware | 人工审批 | 敏感操作暂停等用户确认 |
| PermissionMiddleware | 文件系统权限控制 | Sub Agent 只能访问授权路径 |
| SkillsMiddleware | 按需加载技能 | 主 Agent 按任务类型自动加载对应技能 |
| PatchToolCallsMiddleware | 工具调用修复 | 中断/取消的工具调用自动修复 |
| SubAgentResilienceMiddleware | Sub Agent 容错 | Sub Agent 异常时返回失败报告不崩溃 |
| SessionArchiveMiddleware | 会话归档 | 会话结束时摘要写入 ChromaDB 长期记忆 |

## 附录 B：deepagents State 合并机制

```
AgentState（基础）                 ← messages, jump_to, structured_response
+ TodoListMiddleware.state_schema  ← todos: list[Todo]
+ FilesystemMiddleware.state_schema ← files: dict[str, FileData]
+ MemoryMiddleware.state_schema    ← memory_contents: dict[str, str]
+ SkillsMiddleware.state_schema    ← skills_metadata: list[SkillMetadata]
+ SummarizationMiddleware.state_schema ← _summarization_event
= 最终合并后的 StateSchema

合并规则: _resolve_schema() 遍历所有 middleware 的 state_schema，
         合并所有字段注解到一个 TypedDict，同名字段后者覆盖前者。
         带 OmitFromInput 的字段不出现在输入 schema 中。
         带 PrivateStateAttr 的字段 LLM 看不到。
```

## 附录 C：Sub Agent 与主 Agent 的 State 传递

```
主 Agent → Sub Agent:
  排除字段: {"messages", "todos", "structured_response", "skills_metadata", "memory_contents"}
  传入字段: files（共享文件系统）、jump_to 等
  messages 替换为: [HumanMessage(content=任务描述)]

Sub Agent → 主 Agent:
  排除字段: 同上（todos/memory/skills 不回传）
  回传字段: files（合并）、messages 的最后一条文本作为 ToolMessage
  效果: Sub Agent 写的文件主 Agent 可 read_file 读取
        Sub Agent 的最终回复精炼后作为 ToolMessage 追加到主 Agent messages
```
