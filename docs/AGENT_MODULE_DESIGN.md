# ResearchPilot 智能助手模块设计文档

> 最后更新：2026-04-10
> 状态：设计中 → 实施中

## 一、模块概述

**智能助手 (AI Copilot)** 是 ResearchPilot 的核心交互入口。用户全程使用自然语言与 Agent 交流，Agent 自主完成所有数据操作：搜索采集、入库收藏、查数据库、新建知识库、生成报告等。

**核心理念**：用户只说话，Agent 做事。

## 二、技术选型

### 为什么选择 DeepAgents SDK？

经过调研 LangChain 系列框架：

| 框架 | 定位 | 适合场景 |
|------|------|---------|
| **LangChain** | 核心构建块 | 简单 Agent、快速原型 |
| **LangGraph** | 低层编排+运行时 | 复杂确定性与智能体混合工作流 |
| **DeepAgents** | Agent Harness（开箱即用的 Harness 工程） | 复杂多步任务、需要上下文管理、子Agent协作 |

DeepAgents 是 LangChain 官方 2025 年推出的 Agent Harness 框架，**batteries-included**，内置：
- ✅ 自动长对话压缩（Auto-summarization）
- ✅ 虚拟文件系统（可插拔后端）
- ✅ 子 Agent 生成（`task` 工具，上下文隔离）
- ✅ TODO 规划（`write_todos` 工具）
- ✅ 长期记忆（跨线程 Memory Store）
- ✅ Human-in-the-loop（LangGraph interrupt）
- ✅ Skills 渐进式加载

**但** DeepAgents 需要 `pip install deepagents`，且依赖较重。考虑到我们的场景和已有架构，采用**混合方案**：

1. **复用 DeepAgents 的 Harness 设计思想**（虚拟文件系统、子Agent、上下文管理、TODO规划）
2. **基于 LangGraph 直接构建**（项目已用 LangGraph 做报告生成）
3. **将已有功能封装为 LangChain Tools**（复用现有 services 层）
4. **在需要时可选安装 deepagents 作为增强**

这样既能享受 harness 工程的好处，又不过度依赖新框架，且与现有代码无缝融合。

## 三、Agent 架构设计

### 3.1 总体架构

```
┌───────────────────────────────────────────────────────┐
│                    前端 CopilotView                     │
│  自然语言输入 → SSE 流式输出 → 操作日志面板            │
└───────────────────────┬───────────────────────────────┘
                        │ POST /api/copilot/chat (SSE)
┌───────────────────────▼───────────────────────────────┐
│               Copilot API Route (FastAPI)               │
│  - 会话管理 (thread_id)                                  │
│  - SSE StreamingResponse                                │
└───────────────────────┬───────────────────────────────┘
                        │
┌───────────────────────▼───────────────────────────────┐
│            Copilot Agent (LangGraph StateGraph)          │
│                                                          │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │  Router   │───▶│ Executor │───▶│ Responder│          │
│  │ (意图分类)│    │(工具调用) │    │(生成回复) │          │
│  └──────────┘    └──────────┘    └──────────┘          │
│       │               │                                  │
│       │         ┌─────┴─────┐                           │
│       │         │Tool Layer  │                           │
│       │         │(20+ Tools)│                           │
│       │         └─────┬─────┘                           │
│       │               │                                  │
│  ┌────▼───────────────▼──────────────────┐             │
│  │        Existing Services Layer          │             │
│  │  Ingestion │ RAG Engine │ Chat │ Report│             │
│  └───────────────────────────────────────┘             │
│                                                          │
│  ┌────────────────────────────────────────┐             │
│  │   Context & Memory Management           │             │
│  │  - 对话历史 (LangGraph Checkpoint)       │             │
│  │  - 工作记忆 (虚拟文件系统/状态)          │             │
│  │  - 长期记忆 (用户偏好/项目上下文)       │             │
│  └────────────────────────────────────────┘             │
└──────────────────────────────────────────────────────┘
```

### 3.2 Agent 工作流 (LangGraph)

```python
# StateGraph 定义
workflow = StateGraph(CopilotState)

# 节点
workflow.add_node("router", route_intent)       # 意图分类 → 决定走哪个子图
workflow.add_node("planner", plan_tasks)        # 复杂任务分解 (TODO list)
workflow.add_node("executor", execute_tools)    # 工具执行循环
workflow.add_node("responder", generate_response)  # 最终回复生成

# 边
workflow.add_edge(START, "router")
workflow.add_conditional_edges("router", should_plan, {
    True: "planner", False: "executor"
})
workflow.add_edge("planner", "executor")
workflow.add_conditional_edges("executor", should_continue, {
    True: "executor",   # 继续调用工具
    False: "responder",  # 所有工具调用完成
})
workflow.add_edge("responder", END)
```

### 3.3 State 定义

```python
class CopilotState(TypedDict):
    messages: Annotated[list, add_messages]   # LangGraph 标准消息列表
    intent: str | None                        # "search" | "kb" | "report" | "manage" | "chat"
    todos: list[dict]                         # [{"task": str, "status": "pending|in_progress|done"}]
    tool_outputs: list[dict]                  # 工具执行结果摘要
    context_files: dict[str, str]            # 虚拟文件系统（工作记忆）
```

## 四、工具层设计 (20+ Tools)

将现有 services 层能力封装为 LangChain `@tool` 函数：

### 4.1 搜索与采集工具

| 工具名 | 功能 | 对应服务 |
|--------|------|---------|
| `search_web` | 联网搜索 | `ingestion.search_web` |
| `ingest_url` | 采集URL入库 | `ingestion.ingest_url` |
| `collect_topic` | 专题一键采集 | `ingestion.run_topic_collection` |

### 4.2 知识库工具

| 工具名 | 功能 | 对应服务 |
|--------|------|---------|
| `rag_query` | RAG 知识库问答 | `rag.engine.rag_query` |
| `rag_stats` | 查看RAG索引状态 | `rag.engine.get_collection_stats` |
| `rag_reindex` | 重建索引 | `rag.engine.reindex_all_articles` |

### 4.3 数据管理工具

| 工具名 | 功能 | 对应API |
|--------|------|---------|
| `list_topics` | 列出所有专题 | Topics CRUD |
| `create_topic` | 新建专题 | Topics CRUD |
| `update_topic` | 更新专题 | Topics CRUD |
| `delete_topic` | 删除专题 | Topics CRUD |
| `list_articles` | 列出资讯（支持过滤） | Articles CRUD |
| `bookmark_article` | 收藏/取消收藏 | Articles API |
| `delete_article` | 删除资讯 | Articles API |
| `list_knowledge_bases` | 列出知识库 | KB CRUD |
| `create_knowledge_base` | 新建知识库 | KB CRUD |
| `delete_knowledge_base` | 删除知识库 | KB CRUD |
| `upload_document` | 上传文档到知识库 | KB API |
| `delete_document` | 删除知识库文档 | KB API |

### 4.4 报告工具

| 工具名 | 功能 | 对应服务 |
|--------|------|---------|
| `generate_report` | 生成研究报告 | ReportGenerator |
| `list_reports` | 列出报告 | Reports CRUD |
| `export_report` | 导出报告(MD/PDF) | Reports API |

### 4.5 系统工具

| 工具名 | 功能 |
|--------|------|
| `get_system_status` | 查看系统状态 |
| `get_scheduler_jobs` | 查看定时任务 |

## 五、上下文与记忆管理

### 5.1 三层记忆架构

```
┌─────────────────────────────────────────┐
│  L1: 工作记忆 (Working Memory)            │
│  - 当前对话上下文 (LangGraph messages)     │
│  - TODO 列表 (state.todos)               │
│  - 虚拟文件系统 (state.context_files)     │
│  生命周期：单次对话                        │
├─────────────────────────────────────────┤
│  L2: 会话记忆 (Session Memory)            │
│  - LangGraph Checkpointer (SQLite)        │
│  - 对话历史持久化                           │
│  - 支持恢复中断的对话                       │
│  生命周期：跨页面刷新                       │
├─────────────────────────────────────────┤
│  L3: 长期记忆 (Long-term Memory)          │
│  - 用户偏好（语言、默认模式等）             │
│  - 项目上下文（研究主题、关注领域）          │
│  - 存储在 RAG 或专门 memory table          │
│  生命周期：跨会话持久化                     │
└─────────────────────────────────────────┘
```

### 5.2 虚拟文件系统

Agent 在处理复杂任务时，将中间结果写入虚拟文件系统（`state.context_files`），避免上下文窗口溢出：

```python
# Agent 可以：
context_files["research_plan.md"] = "1. 搜索 X\n2. 采集 Y\n3. 生成报告"
context_files["search_results.json"] = json.dumps(results)

# 读取时：
plan = context_files.get("research_plan.md", "")
```

### 5.3 上下文压缩策略

- **工具输出截断**：大结果只保留摘要，原文存入 context_files
- **历史消息摘要**：超过 10 轮对话时，压缩早期消息
- **子 Agent 隔离**：耗时/复杂子任务派给子 Agent，只返回结果

## 六、前端 CopilotView 设计

### 6.1 界面布局

```
┌────────────────────────────────────────────────────┐
│  🤖 ResearchPilot 智能助手                          │
├────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │ 对话区域                                       │   │
│  │                                                 │   │
│  │ 🧑 帮我查查最近AI领域的最新进展，然后收藏3篇     │   │
│  │                                                 │   │
│  │ 🤖 好的，我来帮你处理这个任务：                  │   │
│  │    📋 任务分解：                                 │   │
│  │    1. 搜索"AI最新进展" ✓                        │   │
│  │    2. 筛选3篇优质文章 ✓                         │   │
│  │    3. 收藏入库 ✓                                │   │
│  │                                                 │   │
│  │    🔍 搜索结果：找到8篇相关文章                   │   │
│  │    ⭐ 已收藏：                                   │   │
│  │    - GPT-5发布：多模态推理能力大幅提升 (BBC)      │   │
│  │    - DeepMind AlphaFold 3 预测蛋白质交互 (Nature) │   │
│  │    - 中国AI芯片突破7nm制程 (科技日报)            │   │
│  │                                                 │   │
│  │    ✅ 任务完成！3篇文章已收藏并索引到知识库。      │   │
│  │    你可以问我关于这些文章的任何问题。              │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │ 📝 操作日志（可折叠）                           │   │
│  │  [10:33:01] 🔍 search_web("AI最新进展")        │   │
│  │  [10:33:05] 📥 bookmark_article(id=42)         │   │
│  │  [10:33:05] 📥 bookmark_article(id=43)         │   │
│  │  [10:33:06] 📥 bookmark_article(id=44)         │   │
│  └──────────────────────────────────────────────┘   │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │ 输入区                                          │   │
│  │ [                                       ] [发送]│   │
│  │ 💡 建议：帮我生成AI趋势报告 | 知识库有什么？    │   │
│  └──────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────┘
```

### 6.2 前端核心功能

1. **SSE 流式输出**：逐字/逐块显示 Agent 回复
2. **操作日志面板**：实时显示 Agent 执行的工具调用
3. **Markdown 渲染**：支持代码块、表格、列表
4. **会话管理**：创建/恢复/删除对话
5. **建议操作**：根据上下文推荐下一步

## 七、API 设计

### 7.1 Copilot API 端点

```
POST /api/copilot/chat          # 主对话接口 (SSE streaming)
GET  /api/copilot/sessions      # 列出会话
POST /api/copilot/sessions      # 创建新会话
GET  /api/copilot/sessions/{id} # 获取会话详情+历史
DELETE /api/copilot/sessions/{id} # 删除会话
GET  /api/copilot/tools         # 列出可用工具（调试用）
```

### 7.2 SSE 事件格式

```
event: token
data: {"content": "好的"}

event: tool_start
data: {"tool": "search_web", "args": {"query": "AI最新进展"}}

event: tool_end
data: {"tool": "search_web", "result_summary": "找到8篇相关文章"}

event: todo_update
data: {"todos": [{"task": "搜索AI进展", "status": "done"}, ...]}

event: done
data: {"message_id": 123, "session_id": 1}
```

## 八、实施计划

### Phase 1: 后端核心 (预估 4h)
1. ✅ 安装 deepagents / langgraph 依赖
2. ✅ 定义 CopilotState + LangGraph 工作流
3. ✅ 封装 20+ LangChain Tools
4. ✅ 实现 Copilot Agent Service
5. ✅ 实现 Copilot API Routes (含 SSE)
6. ✅ 集成 Checkpointer (会话持久化)

### Phase 2: 前端页面 (预估 3h)
1. ✅ CopilotView.vue 页面
2. ✅ SSE 流式输出渲染
3. ✅ 操作日志面板
4. ✅ 会话管理 UI
5. ✅ 路由注册

### Phase 3: 增强优化 (预估 3h)
1. ✅ 虚拟文件系统（上下文管理）
2. ✅ 子 Agent 协作（并行采集+分析）
3. ✅ 上下文压缩（长对话自动摘要）
4. ✅ 长期记忆（跨会话偏好）
5. ✅ Human-in-the-loop（确认破坏性操作）

## 九、与现有模块的关系

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ 仪表盘    │  │ 专题管理  │  │ 资讯中心  │  │ 报告中心  │  │ 知识库    │
└─────┬────┘  └─────┬────┘  └─────┬────┘  └─────┬────┘  └─────┬────┘
      │             │             │             │             │
      └─────────────┴─────────────┼─────────────┴─────────────┘
                                  │
                        ┌─────────▼──────────┐
                        │   智能助手 Copilot   │
                        │   (统一切入点)       │
                        │   Agent 自主完成     │
                        │   所有上述功能       │
                        └────────────────────┘
```

Copilot 是**统一入口**，但不是替代。原有页面继续存在，提供更精细的手动控制。
Copilot 适合快速操作、批量任务、研究流程自动化。

## 十、关键设计决策

| 决策 | 选择 | 原因 |
|------|------|------|
| Agent 框架 | LangGraph + DeepAgents 思想 | 已有 LangGraph 依赖；DeepAgents 的 harness 思想指导设计 |
| 工具封装 | LangChain @tool | 与 LangGraph 生态无缝集成 |
| 会话持久化 | LangGraph Checkpointer (SQLite) | 复用项目 SQLite，零额外依赖 |
| 流式输出 | SSE (Server-Sent Events) | 比 WebSocket 简单，单向推送够用 |
| 虚拟文件系统 | State 内 dict | 轻量实现，按需可扩展为真实 FS backend |
| 子 Agent | LangGraph subgraph | 隔离上下文，只返回结果给主 Agent |
