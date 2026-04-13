# ResearchPilot 项目协作手册

> **给新合作者的快速上手指南**
> 最后更新：2026-04-13 18:15
> 项目路径：`E:\workspace\openclaw_project\ResearchPilot`

---

## 一、30 秒了解项目

ResearchPilot 是一个**面向公开专题研究的 AI 助手平台**。核心理念：

```
定义专题 → 自动采集 → 收藏入库 → RAG问答 → 报告生成 → Copilot Agent
```

用户定义研究专题（关键词+调度计划），系统定时从互联网采集资讯，用户收藏后进入向量知识库，可进行智能问答和报告生成，也可通过 Copilot Agent 自然语言完成所有操作。

**在线访问**：前端 `http://localhost:5173`，后端 `http://localhost:8000`

---

## 二、技术栈

| 层 | 技术 | 说明 |
|---|------|------|
| 前端 | Vue 3 + Naive UI + Pinia + Vue Router + Vite + Tailwind CSS | 暗色主题，7个页面 |
| 后端 | FastAPI + SQLAlchemy + SQLite | 端口 8000，uvicorn 启动 |
| RAG | LlamaIndex + ChromaDB（单集合+元数据） | 自定义 CompatibleOpenAIEmbedding |
| LLM | LangChain + LangGraph | ChatOpenAI（OpenAI 兼容 API） |
| Copilot Agent | LangGraph `create_react_agent` + MemorySaver | 24工具，SSE 原生流式 |
| Supervisor | LangGraph StateGraph + 3 Worker Agent | 任务分解+并行采集+综合回复 |
| 搜索 | Tavily / Serper API | 联网搜索采集 |
| 调度 | APScheduler | 专题定时采集 |
| 抓取 | httpx + readability + BeautifulSoup | 网页内容提取 |

### LLM/Embedding 配置（重要！）

**所有 Chat LLM 统一走一组 OpenAI 兼容配置**，Embedding 单独走阿里云：

| 用途 | 环境变量 | 当前值 |
|------|---------|--------|
| Chat LLM | `OPENAI_BASE_URL` + `OPENAI_API_KEY` + `OPENAI_MODEL_NAME` | `glm-5.1` via 代理 |
| Embedding | `ALIBABA_BASE_URL` + `ALIBABA_API_KEY` + `ALIBABA_MODEL_EMBEDDING_NAME` | `text-embedding-v3` via DashScope |

**关键决策**：
- 之前用阿里云 qwen 做聊天，04-13 统一切换为 OpenAI 兼容 API
- 所有 `ChatOpenAI` 调用使用 `streaming=True`，通过 `on_chat_model_stream` 实现原生 SSE 打字机效果
- `get_chat_llm()` 支持 `with_fallbacks()` 自动 fallback（配置 `OPENAI_MODEL_NAME_FALLBACK` 即可）

---

## 三、先看这 3 个文档

| # | 文件 | 内容 | 10 分钟后你将了解 |
|---|------|------|-------------------|
| 1 | `docs/PRODUCT_OVERVIEW.md` | 产品定义、使用场景、功能模块 | 项目做什么、给谁用 |
| 2 | `docs/TECH_ARCHITECTURE_V1.md` | 技术架构、目录结构 | 怎么实现的、代码在哪 |
| 3 | 本文档（`ONBOARDING.md`） | 进度、配置、关键决策、如何启动 | 做到哪了、怎么跑起来 |

---

## 四、项目结构速查

```
ResearchPilot/
├── backend/                    # FastAPI 后端
│   ├── .env                    # 环境变量（LLM/Embedding/搜索 API 配置）
│   ├── .env.example            # 配置模板
│   ├── researchpilot.db        # SQLite 数据库
│   ├── chroma_db/              # ChromaDB 向量存储
│   ├── app/
│   │   ├── main.py             # FastAPI 入口
│   │   ├── core/
│   │   │   └── config.py       # pydantic-settings 配置（.env → Settings）
│   │   ├── db/                 # SQLAlchemy 模型 + session
│   │   │   ├── models/         # topic/article/report/knowledge_base/kb_document/chat
│   │   │   └── session.py      # SessionLocal
│   │   ├── api/routes/         # API 路由
│   │   │   ├── health.py       # /api/health, /api/status, /api/rag/stats, /api/rag/reindex
│   │   │   ├── topics.py       # /api/topics CRUD
│   │   │   ├── articles.py     # /api/articles 列表/收藏/删除/采集
│   │   │   ├── qa.py           # /api/qa/query, /api/qa/chat
│   │   │   ├── reports.py      # /api/reports 生成/删除/导出
│   │   │   ├── knowledge_base.py # /api/knowledge-bases CRUD + 上传
│   │   │   ├── tasks.py        # /api/tasks 后台任务查询
│   │   │   ├── chat_sessions.py # /api/chat-sessions 会话管理
│   │   │   └── copilot.py      # /api/copilot/chat, /api/copilot/chat/stream SSE
│   │   └── services/           # 核心业务逻辑
│   │       ├── rag/
│   │       │   └── engine.py   # RAG 引擎（检索+合成+HyDE+重索引）
│   │       ├── chat.py         # 智能对话（search/knowledge/hybrid 三模式）
│   │       ├── ingestion.py    # 采集管道（搜索→抓取→提取→入库）
│   │       ├── report_generator.py  # 报告生成（LangGraph StateGraph）
│   │       ├── scheduler.py    # APScheduler 定时采集
│   │       ├── tasks.py        # 后台任务管理
│   │       └── copilot/        # 🤖 Copilot Agent 模块
│   │           ├── __init__.py
│   │           ├── tools/      # 工具层（8个功能域文件，24工具）
│   │           │   ├── search.py     # search_web, ingest_url, collect_topic
│   │           │   ├── rag.py        # rag_query, rag_stats, rag_reindex
│   │           │   ├── topic.py      # list/create/update/delete_topic
│   │           │   ├── article.py    # list/bookmark/delete_article
│   │           │   ├── knowledge_base.py  # kb CRUD + upload
│   │           │   ├── report.py     # generate/list_reports
│   │           │   ├── system.py     # get_system_status, get_scheduler_jobs
│   │           │   └── context.py    # write/read_context_file
│   │           ├── llm/
│   │           │   └── __init__.py   # get_chat_llm(), get_rag_llm() — with_fallbacks
│   │           └── agent/
│   │               ├── __init__.py   # 单Agent: create_react_agent | Supervisor入口
│   │               ├── supervisor.py # Supervisor Graph + JSON任务解析 + 路由
│   │               └── workers.py   # Researcher(3工具) / Analyst(5工具) / Manager(12工具)
├── frontend/                   # Vue 3 前端
│   ├── src/
│   │   ├── App.vue             # 整体布局 + 左侧导航
│   │   ├── router/index.ts     # 路由定义
│   │   ├── stores/index.ts     # Pinia Store
│   │   ├── api.ts              # 后端 API 调用
│   │   ├── types.ts            # TypeScript 类型
│   │   └── views/
│   │       ├── DashboardView.vue     # 仪表盘
│   │       ├── TopicsView.vue        # 专题管理
│   │       ├── ArticlesView.vue      # 资讯中心
│   │       ├── QaView.vue            # 智能对话
│   │       ├── ReportsView.vue       # 报告中心
│   │       ├── KnowledgeBaseView.vue # 知识库管理
│   │       └── CopilotView.vue       # 🤖 智能助手（SSE流式+工具日志）
│   └── vite.config.ts          # Vite 配置（含 SSE 独立代理规则）
└── docs/                       # 项目文档
    ├── ONBOARDING.md           # 本文档
    ├── PRODUCT_OVERVIEW.md     # 产品概述
    ├── TECH_ARCHITECTURE_V1.md # 技术架构
    ├── PROGRESS.md             # 详细进度
    ├── TODO.md                 # 工作待办
    ├── AGENT_MODULE_DESIGN.md  # Agent 模块设计
    └── MULTI_AGENT_DESIGN.md   # 多 Agent 协作设计
```

---

## 五、当前进度总览（截至 2026-04-13）

### ✅ P0 — 全部完成

| 任务 | 状态 | 备注 |
|------|------|------|
| QA hybrid 模式优化 | ✅ | 34s→14s，RAG+搜索并行 |
| Copilot 24工具端到端测试 | ✅ | 24/24 通过 |
| APScheduler 定时触发验证 | ✅ | async_mode 后台采集 |
| Copilot SSE 流式实测 | ✅ | 原生 streaming 打字机效果 |
| LLM Fallback 机制 | ✅ | with_fallbacks 自动切换 |
| 代码提交归档 | ✅ | 7个语义 commit |
| **LLM 模型统一** | ✅ | 04-13: 去除所有 qwen chat，统一 OPENAI_* 配置 |

### ✅ P1 — 大部分完成

| 任务 | 状态 | 备注 |
|------|------|------|
| Supervisor 多 Agent SSE | ✅ | worker_switch + 并行采集 |
| Researcher 并行采集 | ✅ | asyncio.gather |
| Supervisor JSON 任务解析 | ✅ | prompt→JSON + 双解析器 |
| 前端 Supervisor 开关 | ✅ | useSupervisor toggle |
| Supervisor 集成测试 | ✅ | 简单/复杂任务均通过 |
| **SSE 原生流式** | ✅ | 04-13: streaming=True + on_chat_model_stream |
| 前端 citations 填充真实值 | 🔲 | RAG 返回 published_at/topic，前端待适配 |
| 上下文压缩 | 🔲 | 长对话自动摘要 |
| Human-in-the-loop | 🔲 | 确认破坏性操作 |
| 虚拟文件系统 | 🔲 | Agent 工作空间 |

### 🔲 P2 — 均未启动

PDF导出 / 用户认证 / 知识库文档预览 / 资讯去重 / 前端全局错误处理

---

## 六、关键决策记录（必须知道）

| 日期 | 决策 | 原因 | 影响 |
|------|------|------|------|
| 04-09 | CompatibleOpenAIEmbedding 绕过白名单 | LlamaIndex 拒绝非 OpenAI 模型名 | 用 OpenAI client 直接调 embedding API |
| 04-09 | 单集合+元数据 vs 分集合 | 跨库检索统一排序更准确 | ChromaDB 一个 collection，metadata 区分 KB |
| 04-10 | as_retriever + LangChain 合成答案 | LlamaIndex 白名单+代理空回答 | RAG 分两步：LlamaIndex 检索 + LangChain 生成 |
| 04-11 | Copilot tools/llm/agent 解耦 | 单文件1100行不可维护 | 工具/LLM/Agent 各自独立模块 |
| 04-11 | FUNC_MAP 模式 | async @tool 的 .func=None | 每个工具文件提供 name→impl 映射 |
| 04-11 | create_react_agent 替换自定义 StateGraph | 原生支持 streaming/checkpoint | Copilot 单 Agent 用 LangGraph 预构建 |
| 04-12 | with_fallbacks 替代手写 fallback | 自动切换，无需 try/except | get_chat_llm() 返回带 fallback 链的 LLM |
| **04-13** | **Chat LLM 统一 OPENAI_\* 配置** | **去除阿里云 qwen chat 模型** | **所有 ChatOpenAI 只走 OPENAI_BASE_URL/KEY/MODEL** |
| **04-13** | **Embedding 保留 ALIBABA_\* 配置** | **嵌入模型独立于聊天** | **config.py resolve() 自动映射** |
| **04-13** | **SSE 全部原生 streaming** | **代理 API 需支持 stream=true** | **streaming=True + on_chat_model_stream** |

---

## 七、常见坑（踩过的雷）

1. **uvicorn 不要用 --reload**：热更新不生效，必须完全重启
2. **所有 ChatOpenAI 必须 streaming=True**：某些代理 API 非流式返回 content=None
3. **Copilot Agent 单例缓存**：修改 LLM 配置后需调 `reset_agent()` / `reset_supervisor()` / `reset_all_workers()`
4. **ChromaDB 版本**：用 `chromadb>=0.4.0`，旧版 API 不兼容
5. **Workers.py 不要用 PowerShell Set-Content 修改**：会破坏 UTF-8 中文编码，用 Python 或 edit 工具

---

## 八、如何启动项目

### 8.1 后端

```bash
cd E:\workspace\openclaw_project\ResearchPilot\backend

# 激活虚拟环境（如果需要）
.venv\Scripts\activate

# 启动（不用 --reload）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 8.2 前端

```bash
cd E:\workspace\openclaw_project\ResearchPilot\frontend
npm run dev
```

### 8.3 环境变量

编辑 `backend/.env`，必须配置：

```bash
# Chat LLM（所有聊天/生成任务）
OPENAI_BASE_URL=https://your-api/v1
OPENAI_API_KEY=your-key
OPENAI_MODEL_NAME=glm-5.1

# Embedding（向量嵌入）
ALIBABA_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ALIBABA_API_KEY=your-key
ALIBABA_MODEL_EMBEDDING_NAME=text-embedding-v3

# 搜索（至少配一个）
TAVILY_API_KEY=your-key
```

---

## 九、如何向新合作者介绍（转述要点）

你可以这样说：

> "ResearchPilot 是一个研究情报 AI 助手。用户建专题、设关键词，系统定时抓新闻，收藏后进知识库，可以问答、生成报告，也能用 Copilot 对话完成所有操作。
>
> 前端 Vue3，后端 FastAPI，RAG 用 LlamaIndex+ChromaDB，Agent 用 LangGraph。目前核心功能闭环已全部完成（P0/P1），接下来做体验优化（P2）。
>
> 最大的架构特点是 Copilot 双模式：简单问题走单 Agent 24工具秒回，复杂问题走 Supervisor 分解任务给 3 个 Worker 并行执行。
>
> 配置上注意：聊天 LLM 走 OPENAI_* 变量，Embedding 走 ALIBABA_* 变量，两者是分离的。代码里所有 ChatOpenAI 都是 streaming=True。
>
> 上手的话先看 PRODUCT_OVERVIEW.md 了解产品，再看 TECH_ARCHITECTURE_V1.md 了解架构，然后跑起来前后端试试各个页面。"

---

## 十、下一步工作建议

按优先级：

1. **前端 citations 适配**：RAG 已返回 published_at/topic，前端展示待填充
2. **上下文压缩**：长对话自动摘要，避免 token 爆炸
3. **Human-in-the-loop**：破坏性操作（如删除知识库）需用户确认
4. **PDF 导出**：当前只能导出 MD
5. **资讯去重**：相似 URL/title 自动合并
6. **用户认证**：目前无登录系统
