# ResearchPilot 项目进度文档

> 最后更新：2026-04-12 00:20
> 项目路径：`E:\workspace\openclaw_project\ResearchPilot`

---

## 一、项目概述

ResearchPilot 是一个面向公开专题研究的 AI 助手平台，核心理念：**主动追踪 · 知识沉淀 · 问答分析 · 报告生成**。

用户定义研究专题（关键词+调度计划），系统自动采集互联网资讯，用户收藏后进入知识库，可进行智能问答和报告生成。

---

## 二、技术栈

| 层 | 技术 | 说明 |
|---|------|------|
| 前端 | Vue 3 + Naive UI + Pinia + Vue Router + Vite + Tailwind CSS | 暗色主题 |
| 后端 | FastAPI + SQLAlchemy + SQLite | 端口 8000 |
| RAG | LlamaIndex + ChromaDB（单集合+元数据） | 自定义 CompatibleOpenAIEmbedding |
| LLM | LangChain + LangGraph | ChatOpenAI，支持阿里云/代理 |
| Copilot Agent | LangGraph `create_react_agent` + MemorySaver | 24工具，SSE流式 |
| 搜索 | Tavily / Serper API | 联网搜索采集 |
| 调度 | APScheduler | 专题定时采集 |
| 抓取 | httpx + readability + BeautifulSoup | 网页内容提取 |

### 当前生效配置
- **Copilot LLM**: `qwen3.5-flash` via 阿里云 DashScope（优先，快速可靠，支持 tool calling）
- **QA/Chat LLM**: `qwen3.5-flash` via 阿里云（优先）→ gpt-5.4 via 代理（fallback）
- **RAG answer LLM**: `qwen3.5-flash` via 阿里云（LangChain ChatOpenAI 合成）
- **报告 LLM**: gpt-5.4(via 代理) → qwen3.5-flash(阿里云 fallback)
- **Embedding**: `text-embedding-v3` via 阿里云 DashScope
- **搜索**: Tavily(basic模式) + Serper，均可用

---

## 三、已完成功能模块

### 3.1 前端页面（7个）

| 页面 | 路由 | 功能 |
|------|------|------|
| 仪表盘 | `/` | 系统能力状态、统计卡片、最近资讯/专题/报告概览 |
| 专题管理 | `/topics` | CRUD 专题、关键词标签、调度计划、资讯统计 |
| 资讯中心 | `/articles` | 搜索过滤、专题采集、手动采集URL、收藏/取消收藏、删除（仅未收藏）、详情弹窗 |
| 智能对话 | `/qa` | 三种模式（混合智能/联网搜索/知识库问答）、会话侧边栏、推荐问题 |
| 报告中心 | `/reports` | 选择资讯+提示词生成报告、删除、导出MD/PDF、详情弹窗 |
| 知识库管理 | `/knowledge` | 新建/删除知识库、查看文档列表、上传文档/文件、取消收藏（收藏型）、删除文档（上传型） |
| 🤖 智能助手 | `/copilot` | Agent 自主对话、SSE 流式输出、工具调用日志、建议提示词、对话持久化(localStorage) |

### 3.2 后端 API（同前，略）

### 3.3 核心服务实现

#### RAG 引擎 (`services/rag/engine.py`)
- **向量存储**：ChromaDB 单集合 `researchpilot_all`
- **嵌入模型**：`CompatibleOpenAIEmbedding`，绕过白名单，支持阿里云
- **检索+合成分离**：`as_retriever()` 只做向量检索 → LangChain ChatOpenAI 生成答案
- **LLM 选择**：RAG answer 优先阿里云 qwen（LlamaIndex 白名单+代理空回答双重问题）

#### Copilot 架构（`services/copilot/` — 2026-04-11 重构）

```
copilot/
├── __init__.py
├── tools/                    # 工具层（按功能域拆分）
│   ├── __init__.py           # 延迟加载，汇总 ALL_TOOLS + TOOL_FUNC_MAP
│   ├── search.py             # search_web, ingest_url, collect_topic
│   ├── rag.py                # rag_query, rag_stats, rag_reindex
│   ├── topic.py              # list/create/update/delete_topic
│   ├── article.py            # list/bookmark/delete_article
│   ├── knowledge_base.py     # list/create/delete_kb, upload/list_documents
│   ├── report.py             # generate/list_reports
│   ├── system.py             # get_system_status, get_scheduler_jobs
│   └── context.py            # write/read_context_file
├── llm/                      # LLM 配置层
│   └── __init__.py           # get_chat_llm(), get_rag_llm() — 模型选择解耦
└── agent/                    # Agent 层（使用 LangGraph prebuilt）
    └── __init__.py           # create_react_agent + MemorySaver checkpoint
```

**设计原则**：
- **Tool/LLM/Agent 解耦**：工具不依赖 agent，LLM 选择独立于 agent，agent 只编排流程
- **FUNC_MAP 模式**：每个工具文件提供 `FUNC_MAP = {name: impl_func}`，解决 `@tool` async 函数 `.func=None` 问题
- **延迟加载**：`tools/__init__.py` 用 `get_all_tools()` / `get_tool_func_map()` 延迟导入，避免循环依赖
- **create_react_agent**：替换自定义 StateGraph，原生支持 streaming + checkpoint + tool calling

#### 智能对话 (`services/chat.py`)
- **LLM 优先级**：阿里云 qwen(快) → 代理 gpt(慢)
- **三种模式**：search / knowledge / hybrid
- **streaming=True**：所有 ChatOpenAI 加 streaming（代理非流式返回空）

#### 报告生成 (`services/report_generator.py`)
- **双层 fallback**：proxy gpt → alibaba qwen → 纯聚合
- **gpt-5.4 空 content 自动切阿里云**，验证 4343 字正常

### 3.4 数据模型（同前，略）

---

## 四、已验证的功能

| 功能 | 验证结果 |
|------|---------|
| Tavily 搜索 | ✅ basic 模式，中文查询正常 |
| Serper 搜索 | ✅ 备选引擎，正常返回 |
| URL 抓取入库 | ✅ 浏览器 UA 减少反爬 |
| 收藏→RAG索引 | ✅ 写入 ChromaDB + metadata |
| RAG 问答(knowledge) | ✅ as_retriever+LangChain 合成，2806字 |
| 联网搜索对话(search) | ✅ 2335字 |
| 混合模式(hybrid) | ✅ 3428字 |
| 报告生成(fallback) | ✅ gpt空→自动切qwen，4343字 |
| 聊天会话持久化 | ✅ 创建/消息保存/历史加载/删除 |
| Copilot 非流式 | ✅ astream_events 收集，返回1098字 |
| 专题手动采集 | ✅ topic_id=3，new_articles=3 |
| 24工具全部注册 | ✅ tools/ 重构后全部加载 |
| 切换模式不新建对话 | ✅ 修复 watch chatMode |
| Copilot 对话持久化 | ✅ localStorage 保存/恢复 |
| 侧边栏滚动条美化 | ✅ 自定义细滚动条 |
| **Copilot SSE 流式修复** | ✅ Vue响应式+Vite proxy+SSE解析，打字机效果正常 |
| **QA Hybrid 模式优化** | ✅ RAG+搜索并行 + retrieval_only 省LLM调用 + ingest后台非阻塞，14s(vs 34s) |
| **Copilot 24工具端到端测试** | ✅ 24/24 全部通过（修3 bug：update_topic session脱离、upload_document描述优化、generate_report递归超限） |
| **Copilot SSE 流式实测** | ✅ token级打字机效果 + 工具调用日志完整 + SSE通道稳定 |
| **APScheduler 异步采集** | ✅ async_mode=True 后台采集，task进度追踪正常 |
| **LLM 请求级 Fallback** | ✅ 首选qwen3.5-flash耗尽→qwen3.5-plus→proxy，自动切换+友好提示 |
| **LLM 模型切换** | ✅ 主力模型从 qwen3.5-flash 切换到 qwen3.5-plus（额度可用） |

---

## 五、修复的 Bug 汇总（2026-04-10/11）

| Bug | 根因 | 修复 |
|-----|------|------|
| LLM 返回空回答 | 代理 gpt-5.4 非流式返回 content=None | 所有 ChatOpenAI 加 streaming=True |
| RAG knowledge 空 | LlamaIndex 模型白名单拒绝 qwen + 代理空回答 | as_retriever()+LangChain 合成 |
| .env 配置丢失 | RESEARCHPILOT_OPENAI_API_KEY= 空值覆盖 | 注释掉空行 |
| Tavily 中文 400 | search_depth="advanced" 不兼容中文 | 改为 "basic" |
| 采集反爬 403/418 | UA 标识为爬虫 | 改为浏览器 UA |
| 报告 content 空 | gpt-5.4 不稳定 | 双层 fallback: proxy→alibaba→聚合 |
| Copilot 工具调不到 | async @tool 的 .func=None | FUNC_MAP 模式绕过 |
| Copilot 回复空 | LangGraph 条件边没处理 ToolMessage | 修复 should_continue：ToolMessage→executor |
| Copilot 非流式空 | ainvoke+streaming=True 不兼容 | 改用 astream_events 收集 |
| list_topics JSONDecodeError | keywords 是逗号字符串不是 JSON | 直接 split(",") |
| uvicorn reload 不生效 | --reload 没正确重载模块 | 不用 reload，完全重启 |
| QA chat 慢(12-16s) | gpt-5.4 代理太慢 | QA/Copilot 优先阿里云 qwen |
| **Copilot SSE 不打字机** | Vue 响应式不触发 + Vite proxy 缓冲 | splice 强制重渲染 + SSE 独立代理规则 |
| **Copilot 工具调用后不渲染** | chunk.content 可能是 list 格式 + tool_end 换行 | 后端兼容 str/list + 折叠换行 |
| **QA hybrid 模式超时(34s)** | RAG+搜索串行 + LLM调两次 + ingest阻塞 | 并行gather + retrieval_only + ingest后台 + TOP_K降为10 |
| **update_topic 返回500** | session关闭后访问 topic.name 脱离 | commit前保存name变量 |
| **upload_document 调错工具** | 描述模糊，Agent不知道填什么参数 | 丰富工具描述+示例参数 |
| **generate_report 递归超限** | recursion_limit=25不够 | 提升到50+优化工具描述 |
| **LLM 403 无 fallback** | 额度耗尽后直接报错，无备选 | get_all_chat_llms 多LLM列表+请求级fallback+reset_agent |
| **SSE 502/断连** | qwen3.5-flash 额度耗尽 | 切换主模型为 qwen3.5-plus |

---

## 六、已知问题与待改进

### 6.1 当前缺陷
- [ ] 报告导出PDF需安装 weasyprint，否则回退HTML
- [ ] 数据库中文在 PowerShell 中显示乱码（实际数据正确，编码问题）

### 6.2 待完善
- [ ] 采集异步化 + 前端进度反馈
- [ ] 前端全局错误处理和加载状态统一管理
- [ ] Copilot 子 Agent 协作（并行采集+分析）
- [ ] Copilot 上下文压缩（长对话自动摘要）
- [ ] Copilot Human-in-the-loop（确认破坏性操作）
- [ ] 前端 citations 适配新字段（published_at, topic）
- [ ] 前端 QaView 适配 session_id + 聊天历史
- [ ] 采集反爬 403 长期方案

---

## 七、下一步工作计划

### 优先级 P0
| # | 任务 | 说明 | 状态 |
|---|------|------|------|
| 1 | QA hybrid 模式优化 | RAG+搜索并行 + retrieval_only + ingest后台 | ✅ 34s→14s |
| 2 | Copilot 工具逐个端到端测试 | 24/24 通过 | ✅ 修3 bug |
| 3 | APScheduler 定时触发验证 | 异步采集+task进度追踪 | ✅ async_mode |
| 4 | Copilot SSE 流式实测 | token级打字机+工具日志 | ✅ qwen3.5-plus |
| 5 | LLM 请求级 Fallback | 额度耗尽自动切换备选 | ✅ 3层fallback |
| 6 | 代码提交 & 变更归档 | 清理后待 commit | 🔲 待开始 |

### 优先级 P1
| # | 任务 | 说明 | 状态 |
|---|------|------|------|
| 5 | 前端 citations 适配新字段 | published_at, topic 字段 | 🔲 待开始 |
| 6 | 前端 QaView 适配 session_id | 聊天历史 + 会话切换 | 🔲 待开始 |
| 7 | Copilot 子 Agent 协作 | 并行采集+分析 | 🔲 待设计 |
| 8 | 上下文压缩 | 长对话自动摘要 | 🔲 待设计 |
| 9 | PDF 导出 | weasyprint 或截图方案 | 🔲 待开始 |
| 10 | 前端全局错误处理 | 统一 loading/error 状态 | 🔲 待开始 |

---

## 八、关键决策记录

| 日期 | 决策 | 原因 |
|------|------|------|
| 04-09 | CompatibleOpenAIEmbedding 绕过白名单 | 阿里云 embedding 不在白名单 |
| 04-09 | LLM 和 Embedding 分离配置 | 不同 base_url |
| 04-09 | 单集合+元数据 vs 分集合 | 跨库检索统一排序更准确 |
| 04-10 | Copilot 与 QA 完全隔离 | 不同定位，避免耦合 |
| 04-10 | as_retriever + LangChain 合成 | LlamaIndex 白名单+代理空回答 |
| 04-10 | 所有 ChatOpenAI 加 streaming=True | 代理非流式返回 content=None |
| 04-10 | RAG/Copilot/Chat LLM 优先阿里云 qwen | 代理太慢(12-16s)，qwen 3-4s |
| 04-11 | Copilot tools/llm/agent 解耦重构 | 单文件1100行不可维护，按职责拆分 |
| 04-11 | FUNC_MAP 模式 | async @tool 的 .func=None，需独立函数映射 |
| 04-11 | executor_node async化 | 避免同步嵌套 event loop |
| 04-11 | 非流式用 astream_events | ainvoke+streaming=True 不兼容 |
| 04-11 | uvicorn 不用 --reload | 热更新不生效，需完全重启 |
| 04-11 | **create_react_agent 替换自定义 StateGraph** | 原生支持 streaming/checkpoint/tool calling |
| 04-11 | **SSE Vite 独立代理 + splice 重渲染** | Vite proxy 缓冲 + Vue 响应式不触发 |
| 04-12 | **LLM 三层 Fallback** | flash额度耗尽→plus→proxy，请求级自动切换 |
| 04-12 | **主模型切换 qwen3.5-flash→qwen3.5-plus** | flash 免费额度耗尽，plus 可用 |
| 04-12 | **recursion_limit=50** | generate_report 嵌套LLM调用超默认25限制 |

---

## 九、架构图（简化）

```
┌─────────────────────────────────────────────────────────────┐
│                    Vue 3 Frontend                             │
│  Dashboard │ Topics │ Articles │ QA │ Reports │ KB │ Copilot │
│  (Vite proxy: /api/copilot/chat/stream 独立SSE代理)          │
└──────────────────────┬──────────────────────────────────────┘
                       │ Vite Proxy /api → :8000
┌──────────────────────▼──────────────────────────────────────┐
│                  FastAPI Backend                               │
│  ┌─────────┐ ┌──────────┐ ┌────────┐ ┌──────────┐          │
│  │ Topics  │ │ Articles │ │  QA    │ │ Reports  │          │
│  └─────────┘ └──────────┘ └───┬────┘ └────┬─────┘          │
│       │            │          │           │                 │
│  ┌────▼────────────▼──────────▼───────────▼──────┐          │
│  │              Core Services                      │          │
│  │  ┌──────────┐ ┌────────┐ ┌────────────────┐   │          │
│  │  │ Ingestion│ │  RAG   │ │ ReportGenerator│   │          │
│  │  │ Pipeline │ │ Engine │ │  (LangGraph)   │   │          │
│  │  └────┬─────┘ └───┬────┘ └───────┬────────┘   │          │
│  │       │           │              │             │          │
│  │  ┌────▼────┐ ┌────▼────┐ ┌──────▼──────┐      │          │
│  │  │ Tavily  │ │ChromaDB │ │ ChatOpenAI  │      │          │
│  │  │ Serper  │ │(单集合) │ │(qwen优先)   │      │          │
│  │  │ httpx   │ │LlamaIdx │ │             │      │          │
│  │  └─────────┘ └─────────┘ └─────────────┘      │          │
│  └─────────────────────────────────────────────────┘         │
│  ┌───────────────────────────────────────────────┐           │
│  │  🤖 Copilot (解耦架构)                         │           │
│  │  tools/ (8文件,24工具,FUNC_MAP)                │           │
│  │  llm/   (模型选择,qwen优先)                    │           │
│  │  agent/ (create_react_agent+MemorySaver)       │           │
│  │  SSE流式 ✅ (打字机+工具日志) │ 非流式 ✅        │           │
│  └───────────────────────────────────────────────┘           │
│  ┌──────────────┐  ┌──────────────────┐                      │
│  │ SQLite (ORM) │  │ APScheduler      │                      │
│  │ 7 tables     │  │ (定时采集,已激活) │                      │
│  └──────────────┘  └──────────────────┘                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 十、未提交变更清单（截至 2026-04-11 14:04）

> 自 `e9d72b0` (MVP v0.1) 以来的所有未提交变更

### 修改文件
- 后端：routes/(articles,health,qa,reports,copilot)、config.py、models/(report,chat)、main.py、schemas/report、services/(ingestion,rag/engine,report_generator,scheduler,seed)、pyproject.toml、.env.example
- 前端：App.vue、api.ts、router、stores、types、views/(Articles,Dashboard,QA,Reports,Topics,CopilotView)、vite.config.ts
- 文档：PRODUCT_OVERVIEW.md、TECH_ARCHITECTURE_V1.md

### 新增文件
- 后端：routes/(chat_sessions, copilot, knowledge_base, tasks)、models/(chat, kb_document, knowledge_base)、schemas/knowledge_base、services/(chat, copilot/, tasks)
- 前端：views/(CopilotView, KnowledgeBaseView)
- 文档：AGENT_MODULE_DESIGN.md、ONBOARDING.md、PROGRESS.md、RAG_OPTIMIZATION_LOG.md、TODO.md

### 删除文件
- docs/(MVP_PROGRESS_REVIEW_2026-04-09.md, NEXT_STEPS.md, PROJECT_INIT_PLAN.md, WORK_LOG.md) — 内容已合并到 PROGRESS.md
