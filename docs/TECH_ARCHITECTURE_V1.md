# ResearchPilot 技术架构

> 最后更新：2026-04-13

---

## 一、架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                     Vue 3 Frontend (Vite)                        │
│  Dashboard │ Topics │ Articles │ QA │ Reports │ KB │ Copilot    │
│  端口 5173 │ Vite proxy /api → :8000 │ SSE 独立代理不缓冲      │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                    FastAPI Backend (端口 8000)                    │
│                                                                  │
│  ┌─ API Routes ──────────────────────────────────────────────┐   │
│  │ health │ topics │ articles │ qa │ reports │ kb │ copilot │   │
│  └──────────────────────────┬────────────────────────────────┘   │
│                              │                                   │
│  ┌─ Core Services ──────────▼───────────────────────────────┐   │
│  │                                                            │   │
│  │  🤖 Copilot (双模式)         📄 Report (LangGraph)         │   │
│  │  单Agent: 24工具+MemorySaver  StateGraph: collect→generate│   │
│  │  多Agent: Supervisor          fallback: LLM→聚合          │   │
│  │    → Researcher(并行采集)                                │   │
│  │    → Analyst(分析报告)                                    │   │
│  │    → Manager(数据CRUD)                                   │   │
│  │                                                            │   │
│  │  💬 Chat (三模式)            🔍 Ingestion Pipeline         │   │
│  │  search / knowledge /       Tavily/Serper搜索              │   │
│  │  hybrid(RAG+搜索并行)       httpx抓取→readability提取     │   │
│  │                                                            │   │
│  │  📚 RAG Engine               ⏰ APScheduler               │   │
│  │  LlamaIndex+ChromaDB         专题定时采集                  │   │
│  │  CompatibleOpenAIEmbedding                                │   │
│  │  as_retriever + LangChain合成                            │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─ External APIs ───────────────────────────────────────────┐   │
│  │ Chat LLM (OPENAI_*)      Embedding (ALIBABA_*)           │   │
│  │ 搜索: Tavily + Serper     DB: SQLite + ChromaDB          │   │
│  └────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 二、技术栈

| 层 | 技术 | 说明 |
|---|------|------|
| 前端 | Vue 3 + TypeScript + Vite + Naive UI + Pinia + Tailwind CSS | 暗色主题 |
| 后端 | FastAPI + SQLAlchemy + SQLite + pydantic-settings | Python 3.12 |
| RAG | LlamaIndex + ChromaDB（单集合+metadata） | CompatibleOpenAIEmbedding |
| LLM | LangChain ChatOpenAI + LangGraph | streaming=True |
| Agent | LangGraph create_react_agent + MemorySaver | 24 工具 + Supervisor |
| 搜索 | Tavily / Serper API | 联网搜索 |
| 抓取 | httpx + readability + BeautifulSoup | 网页内容提取 |
| 调度 | APScheduler | 专题定时采集 |

---

## 三、模型配置架构（2026-04-13 统一改造后）

### 设计原则

**聊天模型**和**嵌入模型**彻底分离，走不同 API 提供商：

```
Chat LLM ←── OPENAI_BASE_URL + OPENAI_API_KEY + OPENAI_MODEL_NAME
                │
                └──→ Copilot / QA / Report / HyDE / Supervisor / Workers
                     全部用同一配置，统一管理

Embedding  ←── ALIBABA_BASE_URL + ALIBABA_API_KEY + ALIBABA_MODEL_EMBEDDING_NAME
                │
                └──→ ChromaDB 向量化（文档入库 + 查询嵌入）
```

### 配置解析逻辑（config.py resolve()）

```python
# Chat LLM: llm_model > openai_model_name > 默认 gpt-4o-mini
# Embedding: embedding_api_key > alibaba_api_key
#            embedding_base_url > alibaba_base_url
#            embedding_model > alibaba_model_embedding_name > 默认 text-embedding-3-small
```

### Fallback 机制

- **Chat LLM**: `with_fallbacks()` — 主模型失败自动切 `OPENAI_MODEL_NAME_FALLBACK`（如配置）
- **Embedding**: `ALIBABA_MODEL_EMBEDDING_NAME_FALLBACK` — 备选嵌入模型
- **Report**: 主 LLM → fallback LLM → 纯聚合（无 LLM 时）

### 为什么不用阿里云 qwen 做聊天了

之前用 qwen3.5-plus 做聊天（阿里云 DashScope），但免费额度有限且经常耗尽。
统一改为第三方 OpenAI 兼容 API 后：
- 模型可随时替换（改 .env 即可，不碰代码）
- 不依赖单一云厂商
- 代码大幅简化（删除阿里云聊天 fallback 逻辑）

---

## 四、Copilot 架构详解

### 4.1 目录结构

```
copilot/
├── __init__.py          # 模块入口
├── tools/               # 工具层（按功能域拆分）
│   ├── __init__.py      #   延迟加载 ALL_TOOLS + TOOL_FUNC_MAP
│   ├── search.py        #   search_web, ingest_url, collect_topic
│   ├── rag.py           #   rag_query, rag_stats, rag_reindex
│   ├── topic.py         #   list/create/update/delete_topic
│   ├── article.py       #   list/bookmark/delete_article
│   ├── knowledge_base.py#   list/create/delete_kb, upload/list_documents
│   ├── report.py        #   generate/list_reports
│   ├── system.py        #   get_system_status, get_scheduler_jobs
│   └── context.py       #   write/read_context_file
├── llm/                 # LLM 配置层
│   └── __init__.py      #   get_chat_llm(), get_rag_llm() — with_fallbacks
└── agent/               # Agent 层（双模式）
    ├── __init__.py      #   单Agent + Supervisor 入口
    ├── supervisor.py    #   Supervisor Graph + 节点 + JSON任务解析
    └── workers.py       #   3 Worker: Researcher/Analyst/Manager
```

### 4.2 双模式设计

| 模式 | 适用场景 | 组成 |
|------|---------|------|
| 单 Agent | 简单查询、单步操作 | create_react_agent + 24工具 + MemorySaver |
| Supervisor | 复杂多步任务 | Supervisor(LLM分解) → Researcher(搜索)/Analyst(分析)/Manager(管理) → 综合 |

### 4.3 FUNC_MAP 模式

```python
# 每个 tools/*.py 文件导出：
TOOLS = [search_web, ingest_url, ...]          # LangChain @tool 对象
FUNC_MAP = {"search_web": _search_web_impl}     # name → 实际函数引用

# 原因：LangChain async @tool 的 .func 属性为 None
# 解决：FUNC_MAP 提供独立函数映射，绕过此问题
```

### 4.4 SSE 流式

- **后端**: `astream_events(v2)` → `on_chat_model_stream` 逐 token 推送
- **前端**: EventSource 解析 → splice 触发 Vue 响应式 → 打字机效果
- **Vite 代理**: `/api/copilot/chat/stream` 需独立代理规则（禁缓冲）

---

## 五、RAG 引擎设计

### 5.1 单集合 + 元数据

```
集合: researchpilot_all
文档 metadata:
  - kb_id: 知识库ID（0=收藏默认）
  - kb_type: "bookmarks" | "upload"
  - article_id, title, source, url, published_at, topic
```

优点：跨知识库检索时统一排序，无需多集合 join。

### 5.2 检索+合成分离

```
LlamaIndex as_retriever() ──→ 向量检索（top_k=10）──→ score 过滤
                                                              │
LangChain ChatOpenAI  ←──────────────────────────────────────┘
      │                    合成答案（绕过 LlamaIndex 白名单）
      └──→ streaming=True, 支持 SSE
```

### 5.3 CompatibleOpenAIEmbedding

LlamaIndex 有模型名白名单，阿里云 embedding 不在其中。
解决方案：自定义 `BaseEmbedding`，直接用 `openai.OpenAI` 客户端调用。

---

## 六、智能对话（chat.py）三种模式

| 模式 | 流程 | 适用 |
|------|------|------|
| **search** | 搜索 → LLM综合 | 要最新信息 |
| **knowledge** | RAG检索 → LLM答案 | 查已有资料 |
| **hybrid**（默认）| RAG+搜索 **并行** → 检索结果判断 → LLM综合 | 通用 |

hybrid 优化：`retrieval_only=True` 跳过 RAG 内 LLM 调用，省一次请求，34s→14s。

---

## 七、报告生成（report_generator.py）

```
StateGraph: collect(验证) → generate(LLM) → END

generate 内部:
  1. 主 LLM（OPENAI_MODEL_NAME）
  2. Fallback LLM（OPENAI_MODEL_NAME_FALLBACK，如配置）
  3. 纯聚合 fallback（无 LLM 时直接拼接摘要）
```

---

## 八、数据模型

| 表 | 模型文件 | 说明 |
|----|---------|------|
| topics | db/models/topic.py | 专题（关键词+调度+启用状态） |
| articles | db/models/article.py | 资讯（标题/来源/摘要/收藏状态） |
| reports | db/models/report.py | 报告（标题/专题/内容） |
| knowledge_bases | db/models/knowledge_base.py | 知识库（名称/类型） |
| kb_documents | db/models/kb_document.py | 知识库文档（标题/内容/索引状态） |
| chat_sessions | db/models/chat/ | 聊天会话 |
| chat_messages | db/models/chat/ | 聊天消息 |

---

## 九、目录结构

```
ResearchPilot/
├── docs/                              # 文档
├── backend/
│   ├── app/
│   │   ├── api/routes/                # API 路由（8个）
│   │   │   ├── health.py              #   健康检查+系统状态+RAG统计+调度
│   │   │   ├── topics.py              #   专题 CRUD
│   │   │   ├── articles.py            #   资讯列表/收藏/删除/采集
│   │   │   ├── qa.py                  #   RAG问答+智能对话
│   │   │   ├── reports.py             #   报告生成/删除/导出
│   │   │   ├── knowledge_base.py      #   知识库+文档管理
│   │   │   ├── copilot.py             #   Copilot SSE流式+非流式
│   │   │   ├── tasks.py               #   后台任务状态
│   │   │   └── chat_sessions.py       #   聊天会话CRUD
│   │   ├── core/config.py             # 配置（OPENAI_* + ALIBABA_*）
│   │   ├── db/                        # 数据库模型+会话
│   │   └── services/
│   │       ├── copilot/               #   🤖 Agent 模块
│   │       │   ├── tools/ (8文件)     #     工具层
│   │       │   ├── llm/              #     LLM 配置层
│   │       │   └── agent/ (3文件)    #     Agent 层（单Agent+Supervisor+Workers）
│   │       ├── rag/engine.py          #   RAG引擎
│   │       ├── chat.py               #   智能对话
│   │       ├── ingestion.py          #   采集管道
│   │       ├── report_generator.py   #   报告生成
│   │       └── scheduler.py         #   定时调度
│   ├── .env / .env.example
│   ├── researchpilot.db
│   └── chroma_db/
├── frontend/
│   └── src/
│       ├── views/ (7个页面)
│       ├── stores/index.ts
│       ├── api.ts
│       └── types.ts
└── .gitignore
```
