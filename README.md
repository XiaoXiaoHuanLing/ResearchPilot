# ResearchPilot

> AI 驱动的专题研究助手平台 —— 主动追踪 · 知识沉淀 · 问答分析 · 报告生成 · Agent 自动化

## ✨ 功能概览

ResearchPilot 是一个面向公开专题研究的全链路 AI 助手平台，用户定义研究专题后，系统自动采集互联网资讯，收藏后进入向量知识库，支持智能问答和报告生成，Copilot Agent 可用自然语言驱动全流程。

### 五阶段闭环

| 阶段 | 功能 | 说明 |
|------|------|------|
| 🔍 搜索采集 | 专题定时采集 / 手动 URL 入库 / Agent 联网搜索 | Tavily + Serper 搜索，httpx + readability 抓取 |
| 📚 知识入库 | 收藏自动入向量库 / 手动上传文档 | ChromaDB 向量索引，支持元数据过滤 |
| 💬 RAG 问答 | 知识库问答 / 联网搜索 / 混合智能 | Agentic RAG 反思检索循环，BM25+向量混合+RRF 融合 |
| 📄 报告生成 | 选文章+提示词生成研究报告 | LangGraph 流水线 + LLM 生成 |
| 🤖 Agent 自动化 | 自然语言驱动全流程 | DeepAgents 主-子 Agent 架构，5 个 Sub Agent 并行协作 |

### 前端页面

| 页面 | 路由 | 功能 |
|------|------|------|
| 仪表盘 | `/` | 系统状态、统计卡片、最近动态 |
| 专题管理 | `/topics` | CRUD、关键词标签、调度计划 |
| 资讯中心 | `/articles` | 搜索、采集、收藏/取消、详情弹窗 |
| 智能对话 | `/qa` | 三种模式（hybrid/search/knowledge）、会话侧边栏 |
| 报告中心 | `/reports` | 选文章+提示词生成、导出 MD |
| 知识库管理 | `/knowledge` | 新建/删除 KB、上传文档、文档列表 |
| 智能助手 | `/copilot` | Agent 对话、SSE 流式、工具日志、模式切换 |

---

## 🏗️ 技术架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    Vue 3 Frontend (Vite)                        │
│  Dashboard │ Topics │ Articles │ QA │ Reports │ KB │ Copilot  │
│  端口 5173 │ Vite proxy /api → :8000 │ SSE 独立代理           │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                   FastAPI Backend (端口 8000)                    │
│                                                                 │
│  🤖 Copilot Agent (三模式)        📄 Report Generator           │
│  ├─ single_agent (24 工具)        LangGraph StateGraph          │
│  ├─ supervisor (Supervisor+Workers)                              │
│  └─ deep_agent (主+5 Sub Agent)   💬 Chat Service (三模式)      │
│     ├─ search_subagent            hybrid / search / knowledge   │
│     ├─ collect_subagent                                          │
│     ├─ rag_subagent (反思循环)     🔍 Ingestion Pipeline         │
│     ├─ report_subagent            Tavily/Serper → httpx 抓取    │
│     └─ manager_subagent           → readability 提取 → 入库    │
│                                                                 │
│  📚 RAG Engine                    ⏰ APScheduler                │
│  LlamaIndex + ChromaDB            专题定时采集                   │
│  Agentic RAG + BM25 混合检索                                     │
└─────────────────────────────────────────────────────────────────┘
```

### 技术栈

| 层 | 技术 | 说明 |
|---|------|------|
| 前端 | Vue 3 + TypeScript + Vite + Naive UI + Pinia + Tailwind CSS | 暗色主题 |
| 后端 | FastAPI + SQLAlchemy + SQLite + pydantic-settings | Python 3.12 |
| RAG | LlamaIndex + ChromaDB（单集合+metadata） | CompatibleOpenAIEmbedding |
| LLM | LangChain ChatOpenAI + LangGraph | DashScope（qwen3.5 系列） |
| Agent | DeepAgents create_deep_agent + create_react_agent | 主-子 Agent + 中间件栈 |
| 搜索 | Tavily / Serper API | 联网搜索 |
| 抓取 | httpx + readability + BeautifulSoup | 网页内容提取 |
| 调度 | APScheduler | 专题定时采集 |

---

## 📁 项目结构

```
ResearchPilot/
├── backend/
│   ├── app/
│   │   ├── main.py                          # FastAPI 入口
│   │   ├── core/
│   │   │   └── config.py                    # 环境配置（DashScope/搜索/ChromaDB）
│   │   ├── api/routes/
│   │   │   ├── copilot.py                   # Copilot SSE 流式路由
│   │   │   ├── qa.py                        # 智能对话路由
│   │   │   ├── topics.py                    # 专题管理路由
│   │   │   ├── articles.py                  # 资讯中心路由
│   │   │   ├── reports.py                   # 报告路由
│   │   │   ├── knowledge_base.py            # 知识库路由
│   │   │   └── health.py                    # 健康检查
│   │   ├── services/
│   │   │   ├── copilot/
│   │   │   │   ├── agent/
│   │   │   │   │   ├── deep_agent.py        # Deep Agent 主入口
│   │   │   │   │   ├── middleware.py        # 自定义中间件（容错/归档）
│   │   │   │   │   ├── memory_tools.py      # 长期记忆工具
│   │   │   │   │   ├── supervisor.py        # Supervisor 模式
│   │   │   │   │   ├── workers.py           # Worker 子图
│   │   │   │   │   └── __init__.py          # 三模式入口
│   │   │   │   ├── tools/                   # 24 个业务工具（8 文件）
│   │   │   │   │   ├── search.py            #   search_web, ingest_url, collect_topic
│   │   │   │   │   ├── rag.py               #   rag_query, rag_stats, rag_reindex
│   │   │   │   │   ├── topic.py             #   专题 CRUD
│   │   │   │   │   ├── article.py           #   文章 CRUD
│   │   │   │   │   ├── knowledge_base.py   #   知识库 CRUD + 文档上传
│   │   │   │   │   ├── report.py            #   报告生成
│   │   │   │   │   ├── system.py            #   系统状态 + 调度查询
│   │   │   │   │   └── context.py           #   上下文文件读写
│   │   │   │   └── llm/
│   │   │   │       └── __init__.py          #   get_chat_llm() + with_fallbacks
│   │   │   ├── rag/
│   │   │   │   └── engine.py                # RAG 引擎（LlamaIndex+ChromaDB）
│   │   │   ├── chat.py                      # 智能对话服务（三模式）
│   │   │   ├── ingestion.py                 # 采集管道（搜索→抓取→提取→入库）
│   │   │   ├── report_generator.py          # 报告生成器（LangGraph）
│   │   │   ├── scheduler.py                 # APScheduler 定时采集
│   │   │   └── tasks.py                     # 任务进度追踪
│   │   ├── db/models/                       # SQLAlchemy 数据模型
│   │   ├── schemas/                         # Pydantic 请求/响应模型
│   │   └── vector_store/                    # ChromaDB 向量库管理
│   └── .env                                 # 环境变量（需创建）
│
├── frontend/
│   ├── src/
│   │   ├── views/                           # 7 个页面组件
│   │   │   ├── DashboardView.vue
│   │   │   ├── TopicsView.vue
│   │   │   ├── ArticlesView.vue
│   │   │   ├── QaView.vue
│   │   │   ├── ReportsView.vue
│   │   │   ├── KnowledgeBaseView.vue
│   │   │   └── CopilotView.vue
│   │   ├── router/                          # Vue Router
│   │   ├── stores/                          # Pinia 状态管理
│   │   ├── App.vue
│   │   └── main.ts
│   ├── package.json
│   └── vite.config.ts
│
└── docs/                                    # 项目文档
    ├── ONBOARDING.md                        # 新人引导
    ├── TECH_ARCHITECTURE_V1.md              # 技术架构详解
    ├── MULTI_AGENT_REDESIGN_V2.md           # 多智能体重构设计
    ├── SYSTEM_DESIGN_ANALYSIS.md            # 系统设计深度分析
    └── PROGRESS.md                          # 项目进度
```

---

## 🚀 快速部署

### 前置要求

- Python 3.12+
- Node.js 18+
- 阿里云 DashScope API Key（[申请地址](https://dashscope.console.aliyun.com/)）
- Tavily API Key（可选，[申请地址](https://tavily.com/)）
- Serper API Key（可选，[申请地址](https://serper.dev/)）

### 1. 克隆项目

```bash
git clone <repo-url>
cd ResearchPilot
```

### 2. 后端配置与启动

```bash
cd backend

# 创建虚拟环境
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 创建 .env 文件
cp .env.example .env
```

编辑 `backend/.env`，填入必要配置：

```env
# 阿里云 DashScope（必填）
DASHSCOPE_API_KEY=sk-your-dashscope-api-key
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL_NAME=qwen3.5-35b-a3b
DASHSCOPE_MODEL_NAME_FALLBACK=qwen3.5-flash
DASHSCOPE_MODEL_EMBEDDING_NAME=text-embedding-v3

# 搜索 API（至少配一个）
TAVILY_API_KEY=tvly-your-tavily-key
SERPER_API_KEY=your-serper-key
```

启动后端：

```bash
uvicorn app.main:app --reload --port 8000
```

验证：访问 http://localhost:8000/health 返回 `{"status": "ok"}`

### 3. 前端配置与启动

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

访问 http://localhost:5173 即可使用。

### 4. 生产构建

```bash
# 后端
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 前端
npm run build
# 构建产物在 frontend/dist/，可用 nginx 代理
```

---

## 🤖 多智能体架构

ResearchPilot 的 Copilot 采用 DeepAgents 主-子 Agent"想做分离"架构：

```
用户消息
   │
   ▼
┌──────────────────────────────────────┐
│          主 Agent（大脑）              │
│  意图识别 → 任务规划 → 委托执行       │
│  → 反思评估 → 动态 replan             │
│                                      │
│  中间件栈:                            │
│  TodoList / Filesystem / SubAgent    │
│  Summarization / Memory / HITL       │
│  Permission / Resilience / Archive   │
└──────────┬───────────────────────────┘
           │ task 工具
   ┌───────┼───────┬──────────┐
   ▼       ▼       ▼          ▼
 search  collect   rag     report   manager
 (搜索)  (采集)   (问答)   (报告)   (管理)
```

| Sub Agent | 单一职责 | 工具集 |
|-----------|---------|--------|
| search_subagent | 联网搜索 | search_web |
| collect_subagent | 内容采集与入库 | ingest_url, collect_topic |
| rag_subagent | 知识库检索问答（反思循环） | search_kb, search_hybrid, rewrite_query, decompose_query, evaluate_results |
| report_subagent | 生成结构化报告 | generate_report, list_reports |
| manager_subagent | 系统资源 CRUD | 专题/文章/知识库 CRUD, 系统状态 |

### Agentic RAG 反思检索循环

rag_subagent 不再是单次检索，而是跑受最大迭代次数限制的反思循环：

```
初始检索 → 反思评估 → 改写/分解/换策略 → 再检索 → ... → 汇总返回
         ↑                                           │
         └───────────── max 3 轮 ─────────────────────┘
```

- **HyDE 改写**：模糊短查询生成假设性回答后再检索
- **子问题分解**：复杂问题拆为子问题逐一检索
- **混合检索**：BM25+向量双路召回 + RRF 融合排序
- **质量评估**：每轮检索后自评结果质量
- **retrieval_lists 蒸馏**：多轮检索结果去重排序摘要后返回

---

## 🔧 关键设计

### 上下文工程

三层防线保障长对话窗口不溢出：

| 层 | 机制 | 触发条件 |
|----|------|---------|
| 第1层 | 大工具结果自动卸载到虚拟文件系统 | 单个结果 > 20k tokens |
| 第2层 | 旧消息自动摘要压缩 | token 超窗口 85% |
| 第3层 | 超长工具参数截断 | 消息 ≥ 50 或 token ≥ 50% |

Sub Agent 上下文隔离：仅收 1 条任务消息，中间过程不回传，大结果走文件系统，确保主 Agent 零污染。

### 记忆分层

| 层级 | 名称 | 存储 | 生命周期 |
|------|------|------|---------|
| L0 | 工作记忆 | LLM prompt（动态重建） | 单次调用 |
| L1 | 会话记忆 | Checkpointer 快照 | 单次会话 |
| L2 | 长期记忆 | ChromaDB 语义召回 | 永久 |
| L3 | 关键记忆 | System Prompt 自动加载 | 永久 |

### 容错与安全

- **三层容错**：工具级友好错误 → Sub Agent 级失败报告 → LLM 级 Fallback + 溢出摘要重试
- **人工审批**：敏感操作（报告生成/专题删除）interrupt_on 暂停等用户确认
- **权限控制**：FilesystemPermission 禁止写入系统路径，Sub Agent 只能在授权范围操作

---

## 📄 License

MIT
