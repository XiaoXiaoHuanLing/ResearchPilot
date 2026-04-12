# ResearchPilot 多智能体协作设计

> 创建时间：2026-04-12
> 状态：设计中

## 一、现状分析

### 当前架构
- **单 Agent**: `create_react_agent` + 24 工具 + MemorySaver
- **工作模式**: 用户发消息 → Agent 自主决定调哪个工具 → 返回结果
- **已验证**: 24/24 工具端到端测试通过，SSE 流式正常

### 痛点
1. **复杂任务无法并行**: "搜索AI新闻 + 搜索军事新闻 + 对比分析" 必须串行执行
2. **长任务阻塞**: 采集+入库+生成报告，一步失败全盘影响
3. **上下文膨胀**: 工具输出过多，token 浪费在中间结果上
4. **无任务规划**: Agent 直接执行，不分解/不规划/不回顾

## 二、多 Agent 架构设计

### 2.1 架构总览：Supervisor + Workers

```
用户消息
    │
    ▼
┌─────────────┐
│  Supervisor  │  — 意图分析 + 任务分解 + 分配 + 结果综合
│  (主 Agent) │
└──────┬──────┘
       │ 分配任务
       ▼
┌──────────────┬──────────────┬──────────────┐
│  Researcher  │   Analyst    │   Manager    │
│  (采集 Agent)│  (分析 Agent)│  (管理 Agent) │
│              │              │              │
│ search_web   │ rag_query    │ list_topics  │
│ ingest_url   │ rag_stats    │ create_topic │
│ collect_topic│ rag_reindex  │ update_topic │
│              │              │ list_articles│
│              │              │ bookmark     │
│              │              │ report       │
│              │              │ system       │
└──────────────┴──────────────┴──────────────┘
       │              │              │
       └──────────────┼──────────────┘
                      ▼
              共享 Tool Layer + Services
```

### 2.2 为什么选 Supervisor 而不是 Swarm？

| 模式 | 优点 | 缺点 | 适合场景 |
|------|------|------|---------|
| **Supervisor** | 集中控制，结果质量高 | 单点瓶颈 | 复杂任务分解、需要综合分析 |
| **Swarm** | 去中心化，自然交接 | 难以追踪，质量不稳定 | 简单交接型任务 |
| **层级式** | 可扩展 | 实现复杂 | 大规模系统 |

ResearchPilot 的场景（研究 → 分析 → 报告）天然是流程型，**Supervisor 模式最合适**：
- Supervisor 负责任务分解和结果综合
- Worker Agent 只做自己的事
- 结果回到 Supervisor 做最终汇总

### 2.3 LangGraph 实现方案

```python
# Supervisor Graph
supervisor_graph = StateGraph(SupervisorState)

supervisor_graph.add_node("supervisor", supervisor_node)
supervisor_graph.add_node("researcher", researcher_node)
supervisor_graph.add_node("analyst", analyst_node)
supervisor_graph.add_node("manager", manager_node)

supervisor_graph.add_edge(START, "supervisor")
supervisor_graph.add_conditional_edges("supervisor", route_to_worker, {
    "researcher": "researcher",
    "analyst": "analyst",
    "manager": "manager",
    "FINISH": END,
})
supervisor_graph.add_edge("researcher", "supervisor")
supervisor_graph.add_edge("analyst", "supervisor")
supervisor_graph.add_edge("manager", "supervisor")
```

### 2.4 State 定义

```python
class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]  # 对话历史
    tasks: list[TaskItem]                     # 任务列表
    results: dict[str, str]                   # 各 worker 返回结果
    next_worker: str | None                   # 下一个要调度的 worker

class TaskItem(TypedDict):
    id: str
    description: str
    worker: str          # "researcher" | "analyst" | "manager"
    status: str          # "pending" | "running" | "done" | "failed"
    result: str | None
```

## 三、三个 Worker Agent 定义

### 3.1 Researcher（采集 Agent）

**职责**: 搜索、采集、入库
**工具**: search_web, ingest_url, collect_topic
**特点**: 可并行执行多个搜索任务

```python
researcher = create_react_agent(
    model=llm,
    tools=[search_web, ingest_url, collect_topic],
    prompt="""你是采集助手。根据任务要求搜索和采集信息。
规则：
1. 只做搜索和采集，不做分析
2. 返回搜索结果摘要和采集状态
3. 如果搜索无结果，明确告知""",
)
```

### 3.2 Analyst（分析 Agent）

**职责**: 知识库问答、数据分析、报告生成
**工具**: rag_query, rag_stats, rag_reindex, generate_report, list_reports
**特点**: 可调用 RAG 引擎做深度分析

```python
analyst = create_react_agent(
    model=llm,
    tools=[rag_query, rag_stats, rag_reindex, generate_report, list_reports],
    prompt="""你是分析助手。根据任务要求进行知识库问答和报告生成。
规则：
1. 先用 rag_query 查知识库，不够再请求补充信息
2. 生成报告时提供关键发现
3. 返回分析结论""",
)
```

### 3.3 Manager（管理 Agent）

**职责**: 数据 CRUD、系统管理、上下文操作
**工具**: topic CRUD, article CRUD, KB CRUD, system, context
**特点**: 管理型操作，安全敏感

```python
manager = create_react_agent(
    model=llm,
    tools=[list_topics, create_topic, update_topic, delete_topic,
           list_articles, bookmark_article, delete_article,
           list_knowledge_bases, create_knowledge_base, delete_knowledge_base,
           upload_document, list_kb_documents,
           get_system_status, get_scheduler_jobs,
           write_context_file, read_context_file],
    prompt="""你是管理助手。根据任务要求管理数据。
规则：
1. 删除操作前确认（Human-in-the-loop）
2. 返回操作结果
3. 批量操作分步执行""",
)
```

## 四、并行执行设计

### 4.1 并行采集场景

用户: "帮我搜索AI和军事两个领域的最新新闻"

```
Supervisor 分解:
  Task 1 → Researcher: "搜索AI最新新闻"
  Task 2 → Researcher: "搜索军事最新新闻"

并行执行:
  researcher_node 处理 Task 1 & 2（Fan-out/Fan-in）
  两个搜索同时进行

Supervisor 综合:
  "AI领域：xxx / 军事领域：yyy / 共采集12篇"
```

### 4.2 LangGraph 并行实现

```python
# Fan-out: 并行发送给同一个 worker 的不同任务
async def researcher_node(state: SupervisorState) -> dict:
    pending = [t for t in state["tasks"] if t["worker"] == "researcher" and t["status"] == "pending"]
    
    # 并行执行所有待处理采集任务
    async def run_task(task):
        result = await researcher.ainvoke(
            {"messages": [HumanMessage(content=task["description"])]},
            config={"configurable": {"thread_id": f"researcher_{task['id']}"}},
        )
        return task["id"], result["messages"][-1].content
    
    results = await asyncio.gather(*[run_task(t) for t in pending])
    
    # 更新 task 状态
    updated_tasks = []
    for t in state["tasks"]:
        for tid, content in results:
            if t["id"] == tid:
                t["status"] = "done"
                t["result"] = content
        updated_tasks.append(t)
    
    return {"tasks": updated_tasks, "next_worker": "supervisor"}
```

## 五、上下文管理

### 5.1 上下文压缩

当对话超过 10 轮，自动压缩早期消息：

```python
async def compress_messages(messages: list, llm: ChatOpenAI) -> list:
    """压缩早期消息，只保留最近5轮 + 摘要"""
    if len(messages) <= 10:
        return messages
    
    # 取前 N-10 条消息做摘要
    old_messages = messages[:-10]
    recent_messages = messages[-10:]
    
    summary_prompt = f"请用3-5句话总结以下对话的关键信息：\n{format_messages(old_messages)}"
    summary = await llm.ainvoke(summary_prompt)
    
    # 替换为摘要
    return [SystemMessage(content=f"[历史对话摘要] {summary.content}")] + recent_messages
```

### 5.2 虚拟文件系统

Worker Agent 的中间结果写入 context_files，避免 token 浪费：

```python
# Researcher 保存搜索结果摘要
context_files["search_results.md"] = "## 搜索结果\n- AI: 5篇\n- 军事: 3篇"

# Analyst 读取后做深度分析
search_data = context_files.get("search_results.md", "")
```

## 六、Human-in-the-loop

### 6.1 破坏性操作确认

删除专题/知识库等破坏性操作，需要用户确认：

```python
# Manager Agent 中使用 LangGraph interrupt
from langgraph.types import interrupt

def _delete_topic_impl(topic_id: int) -> str:
    # 先获取信息
    topic_name = ...
    # 请求确认
    response = interrupt(f"⚠️ 确认删除专题「{topic_name}」？此操作不可恢复。")
    if response.get("confirmed"):
        # 执行删除
        ...
    else:
        return "已取消删除操作"
```

### 6.2 前端确认 UI

```
SSE event: confirmation_needed
data: {"message": "确认删除专题「AI测试专题」？", "action": "delete_topic", "params": {"topic_id": 5}}

前端显示确认对话框 → 用户选择 → POST /api/copilot/confirm
```

## 七、SSE 事件扩展

### 新增事件类型

```javascript
// 任务分解
event: task_plan
data: {"tasks": [{"id": "t1", "description": "搜索AI新闻", "worker": "researcher"}, ...]}

// 任务状态更新
event: task_update
data: {"task_id": "t1", "status": "running"}  // running | done | failed

// Worker 切换
event: worker_switch
data: {"from": "supervisor", "to": "researcher", "reason": "执行采集任务"}

// 确认请求
event: confirmation_needed
data: {"message": "确认删除？", "action": "delete_topic", "params": {...}}
```

## 八、实施路线

### Phase 1: Supervisor 架构（2-3h）
1. 定义 SupervisorState + Worker 节点
2. 实现 supervisor_node（任务分解+分配+综合）
3. 实现3个 worker_node（各自 create_react_agent）
4. 组装 Supervisor Graph
5. SSE 事件扩展（task_plan, task_update, worker_switch）
6. 集成测试

### Phase 2: 并行执行（1-2h）
1. Researcher 并行搜索（asyncio.gather）
2. Fan-out/Fan-in 模式
3. 前端并行状态展示

### Phase 3: 上下文管理（1-2h）
1. 上下文压缩（>10轮自动摘要）
2. 虚拟文件系统（context_files）
3. 长期记忆（用户偏好 + 项目上下文）

### Phase 4: Human-in-the-loop（1h）
1. 破坏性操作确认（LangGraph interrupt）
2. 前端确认 UI
3. 确认 API 端点

## 九、与现有代码的关系

### 保留不变
- `copilot/tools/` — 24 工具照旧，Worker Agent 按需选子集
- `copilot/llm/` — LLM 配置 + 3层 Fallback
- `copilot/agent/__init__.py` — 改造为 Supervisor Graph

### 新增
- `copilot/agent/supervisor.py` — Supervisor Graph + 节点定义
- `copilot/agent/workers.py` — 3个 Worker Agent 定义
- `copilot/agent/context.py` — 上下文压缩 + 虚拟文件系统

### 修改
- `copilot/agent/__init__.py` — 从 create_react_agent 切换到 Supervisor Graph
- `api/routes/copilot.py` — 新增 SSE 事件类型 + 确认端点

## 十、风险评估

| 风险 | 影响 | 缓解 |
|------|------|------|
| Worker Agent 调用过多 LLM | token 消耗增加 | Worker 限 max_tokens + 上下文压缩 |
| 并行采集 API 限流 | Tavily/Serper 限频 | 串行化 burst + 限速 |
| Supervisor 分解不准 | 任务分配错误 | 加 few-shot 示例 + 用户确认 |
| Human-in-the-loop 中断恢复 | 用户不确认卡住 | 超时自动取消（5min） |
