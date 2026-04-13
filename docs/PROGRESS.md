# ResearchPilot 项目进度文档

> 最后更新：2026-04-13 18:20
> 项目路径：`E:\workspace\openclaw_project\ResearchPilot`

---

## 一、项目概述

ResearchPilot 是一个面向公开专题研究的 AI 助手平台。

**核心理念**：**主动追踪 · 知识沉淀 · 问答分析 · 报告生成 · Agent 自动化**

用户定义研究专题（关键词+调度计划），系统自动采集互联网资讯，收藏后进入向量知识库，可智能问答和报告生成，Copilot Agent 可用自然语言驱动全流程。

---

## 二、技术栈

| 层 | 技术 | 说明 |
|---|------|------|
| 前端 | Vue 3 + Naive UI + Pinia + Vite + Tailwind CSS | 暗色主题，7页面 |
| 后端 | FastAPI + SQLAlchemy + SQLite + pydantic-settings | Python 3.12 |
| RAG | LlamaIndex + ChromaDB（单集合+metadata） | CompatibleOpenAIEmbedding |
| LLM | LangChain ChatOpenAI + LangGraph | streaming=True |
| Agent | LangGraph create_react_agent + MemorySaver | 24 工具 + Supervisor 多Agent |
| 搜索 | Tavily / Serper API | 联网搜索 |
| 抓取 | httpx + readability + BeautifulSoup | 网页内容提取 |
| 调度 | APScheduler | 专题定时采集 |

### 当前生效配置（2026-04-13）
- **Chat LLM**: `glm-5.1` via `s7.tunnelfrp.com:10022` 代理（OPENAI_* 环境变量）
- **Embedding**: `text-embedding-v3` via 阿里云 DashScope（ALIBABA_* 环境变量）
- **搜索**: Tavily(basic) + Serper
- **向量库**: ChromaDB `researchpilot_all` 集合

---

## 三、已完成功能

### 3.1 前端页面（7个）

| 页面 | 路由 | 功能 |
|------|------|------|
| 仪表盘 | `/` | 系统状态、统计卡片、最近资讯/专题/报告概览 |
| 专题管理 | `/topics` | CRUD、关键词标签、调度计划 |
| 资讯中心 | `/articles` | 搜索、采集、收藏/取消、详情弹窗 |
| 智能对话 | `/qa` | 三种模式（hybrid/search/knowledge）、会话侧边栏 |
| 报告中心 | `/reports` | 选文章+提示词生成、导出MD |
| 知识库管理 | `/knowledge` | 新建/删除KB、上传文档、文档列表 |
| 🤖 智能助手 | `/copilot` | Agent对话、SSE流式、工具日志、Supervisor开关 |

### 3.2 Copilot 架构（`services/copilot/`）

```
copilot/
├── tools/                    # 工具层（8文件，24工具）
│   ├── search.py             #   search_web, ingest_url, collect_topic
│   ├── rag.py                #   rag_query, rag_stats, rag_reindex
│   ├── topic.py              #   list/create/update/delete_topic
│   ├── article.py            #   list/bookmark/delete_article
│   ├── knowledge_base.py     #   list/create/delete_kb, upload/list_documents
│   ├── report.py             #   generate/list_reports
│   ├── system.py             #   get_system_status, get_scheduler_jobs
│   └── context.py            #   write/read_context_file
├── llm/                      # LLM 配置层
│   └── __init__.py           #   get_chat_llm() — with_fallbacks
└── agent/                    # Agent 层（双模式）
    ├── __init__.py           #   单Agent入口 + Supervisor入口
    ├── supervisor.py         #   Supervisor Graph + JSON任务解析 + 并行调度
    └── workers.py            #   3 Worker: Researcher/Analyst/Manager
```

**双模式**：
- 单Agent（默认）：`create_react_agent` + 24工具 + MemorySaver
- Supervisor（`use_supervisor=True`）：LLM分解意图 → JSON任务 → Researcher(并行采集)/Analyst(分析)/Manager(管理) → 综合

### 3.3 核心服务

| 服务 | 文件 | 关键设计 |
|------|------|---------|
| RAG引擎 | `rag/engine.py` | 单集合+metadata、CompatibleOpenAIEmbedding、as_retriever+LangChain合成 |
| 智能对话 | `chat.py` | 三模式、hybrid并行(RAG+搜索)、retrieval_only优化 |
| 采集管道 | `ingestion.py` | Tavily/Serper搜索→httpx抓取→readability提取→LLM摘要→入库 |
| 报告生成 | `report_generator.py` | LangGraph StateGraph、LLM链+聚合fallback |
| 定时调度 | `scheduler.py` | APScheduler异步采集、task进度追踪 |

---

## 四、验证结果

| 功能 | 结果 |
|------|------|
| Tavily/Serper 搜索 | ✅ |
| URL抓取入库 | ✅ |
| 收藏→RAG索引自动同步 | ✅ |
| RAG问答(knowledge) | ✅ 2806字 |
| 联网搜索(search) | ✅ 2335字 |
| 混合模式(hybrid) | ✅ 14s(优化后) |
| 报告生成 | ✅ 4343字 |
| 聊天会话持久化 | ✅ |
| Copilot 非流式 | ✅ 1098字 |
| Copilot SSE 流式 | ✅ token级打字机 |
| 24工具端到端 | ✅ 24/24 |
| APScheduler定时采集 | ✅ |
| Supervisor 多Agent | ✅ 代码完成，需前端实测 |
| 模型统一(04-13改造) | ✅ 全部接口200 OK |

---

## 五、已修复 Bug 汇总（04-10~04-13）

| Bug | 根因 | 修复 |
|-----|------|------|
| LLM返回空 | 代理API非流式content=None | streaming=True |
| RAG answer空 | LlamaIndex白名单+代理空 | as_retriever+LangChain合成 |
| .env空值覆盖 | `KEY=` 空行覆盖默认 | 注释掉空行 |
| Tavily中文400 | search_depth="advanced" | 改"basic" |
| 采集403/418 | 爬虫UA | 浏览器UA |
| 报告content空 | gpt-5.4不稳定 | LLM链+聚合fallback |
| async @tool func=None | LangChain已知问题 | FUNC_MAP模式 |
| Copilot回复空 | 条件边没处理ToolMessage | 修复should_continue |
| SSE不打字机 | Vite proxy缓冲+Vue响应式 | 独立代理+splice重渲染 |
| QA hybrid 34s | 串行+LLM两次+ingest阻塞 | 并行+retrieval_only+ingest后台 |
| update_topic 500 | session脱离后访问属性 | commit前保存变量 |
| 递归超限 | recursion_limit=25不够 | 提升到50 |
| LLM 403无fallback | 额度耗尽直接报错 | with_fallbacks |
| 阿里云chat耦合 | qwen优先逻辑散布6处 | 统一OPENAI_*配置(04-13) |
| workers.py编码损坏 | PowerShell Set-Content GBK | Python重写(04-13) |

---

## 六、当前已知问题

- [ ] 部分代理API不支持streaming（返回空），需确认API兼容性
- [ ] 报告PDF导出需安装weasyprint，否则回退HTML
- [ ] PowerShell终端中文显示乱码（数据正确，编码问题）
- [ ] Supervisor集成测试待前端实测验证

---

## 七、工作计划

### P0 — 已完成 ✅
| 任务 | 状态 |
|------|------|
| QA hybrid优化 34s→14s | ✅ |
| 24工具端到端测试 | ✅ |
| APScheduler定时触发 | ✅ |
| SSE流式实测 | ✅ |
| LLM fallback链 | ✅ |
| 代码提交归档 | ✅ |
| Supervisor多Agent | ✅ 代码完成 |
| 模型统一改造 | ✅ 04-13 |

### P1 — 待推进
| 任务 | 说明 | 状态 |
|------|------|------|
| Supervisor前端实测 | 确认Worker切换/并行/SSE | 🔲 |
| 前端citations适配 | published_at/topic字段 | 🔲 |
| 上下文压缩 | 长对话自动摘要 | 🔲 |
| Human-in-the-loop | 确认破坏性操作 | 🔲 |
| 虚拟文件系统 | Agent工作空间 | 🔲 |

### P2 — 增强
| 任务 | 状态 |
|------|------|
| PDF导出 | 🔲 |
| 用户认证 | 🔲 |
| 知识库文档预览 | 🔲 |
| 资讯去重 | 🔲 |
| 前端全局错误处理 | 🔲 |

---

## 八、关键决策记录

| 日期 | 决策 | 原因 |
|------|------|------|
| 04-09 | CompatibleOpenAIEmbedding | 绕过LlamaIndex白名单 |
| 04-09 | LLM和Embedding分离配置 | 不同base_url |
| 04-09 | 单集合+metadata | 跨库统一排序更准确 |
| 04-10 | as_retriever+LangChain合成 | LlamaIndex白名单+代理空回答 |
| 04-10 | 全部streaming=True | 代理非流式返回None |
| 04-11 | tools/llm/agent解耦重构 | 单文件1100行不可维护 |
| 04-11 | FUNC_MAP模式 | async @tool .func=None |
| 04-11 | create_react_agent替换自定义Graph | 原生streaming/checkpoint |
| 04-12 | with_fallbacks | 额度耗尽自动切换 |
| 04-12 | Supervisor JSON prompt | LLM自由文本不可靠 |
| 04-12 | Researcher asyncio.gather | 并行采集 |
| **04-13** | **Chat LLM统一OPENAI_*/Embedding统一ALIBABA_*** | **去除qwen chat依赖，简化代码，灵活替换** |
| 04-13 | 删除模拟流式 | comi要求原生streaming |
| 04-13 | get_chat_llm默认streaming=True | 原生流式输出 |

---

## 九、Git 提交历史

```
2dc29c8 docs: update HANDOVER git status to reflect committed 04-13 changes
77284ae docs: update all docs for 04-13 LLM unification + fix PROGRESS duplicate sections + complete HANDOVER
0c4a229 refactor: unify Chat LLM to OPENAI_* config, remove Alibaba qwen chat dependency
8193ddf fix: supervisor never self-answers (no tools=fabricated data), always delegate to workers
5d65944 docs: update TODO with supervisor V2 performance optimization
f8b1b2b perf: supervisor V2 — 5x faster, fix worker failures
52475a0 refactor: with_fallbacks LLM chain + fix supervisor SSE worker_switch
d5a3ada feat: Supervisor multi-agent SSE streaming + parallel researcher + frontend toggle
aec4fa6 feat: frontend Copilot & KB views, update existing views
1e0121f refactor: backend core improvements
620388d feat: add Copilot, KnowledgeBase, ChatSessions, Tasks modules
e9d72b0 feat: ResearchPilot MVP v0.1 - RAG + LangGraph + APScheduler + Ingestion
```

---

## 十、2026-04-13 工作记录

### LLM 模型统一改造

**背景**：之前 Chat LLM 分散在阿里云 qwen（优先）和 OpenAI 代理（fallback），配置混乱、fallback 逻辑散布各处。comi 修改了 .env 配置，去除所有阿里云 qwen 系列 chat 模型，全面切换到 OpenAI 兼容 API。

**改造清单**：

| 文件 | 改动 |
|------|------|
| `core/config.py` | 删除 `alibaba_model_name`/`alibaba_model_name_fallback` 等 chat 字段；新增 `openai_model_name_fallback`；Chat 走 `OPENAI_*`，Embedding 走 `ALIBABA_*` |
| `copilot/llm/__init__.py` | 删除 `_build_alibaba_llm`/双链逻辑；简化为单一 LLM + 可选 fallback（`with_fallbacks`） |
| `chat.py` `_get_llm()` | 去掉阿里云优先逻辑，直用 `settings.llm_model` |
| `rag/engine.py` | RAG answer + HyDE 去掉阿里云优先，统一用 OPENAI_* |
| `report_generator.py` | 去掉阿里云二级 fallback，改为 primary + optional fallback model |
| `copilot.py` SSE | 删除模拟流式逻辑和 `on_chat_model_end` 兜底；恢复纯原生 `on_chat_model_stream` 流式 |
| `workers.py` / `supervisor.py` / `agent/__init__.py` | `get_chat_llm(streaming=True)` 统一为 `get_chat_llm()` |
| `.env.example` | 更新模板，明确 OPENAI=聊天 / ALIBABA=嵌入 |
| `workers.py` | PowerShell Set-Content 编码损坏，用 Python 重写恢复 |

**发现的问题**：
1. Fireworks glm-5.1 streaming 返回空（非流式正常）
2. comi 换代理 URL（s7.tunnelfrp.com:10022 → glm-5.1）后 streaming 正常
3. comi 坚持要原生流式 → 改回 streaming=True，删除所有模拟流式代码

**验证结果**：
- Config: LLM=glm-5.1, Embed=text-embedding-v3 ✅
- Copilot Agent: 24 tools, SSE streaming ✅
- QA/Copilot/Report 全部 200 OK ✅
- 端到端: 224 tokens + 2 tool events ✅

### 关键决策
- Chat LLM 统一 OPENAI_BASE_URL + OPENAI_API_KEY + OPENAI_MODEL_NAME
- Embedding 保留 ALIBABA_BASE_URL + ALIBABA_API_KEY + ALIBABA_MODEL_EMBEDDING_NAME
- SSE 全部原生 streaming，不使用模拟流式
- with_fallbacks 支持 OPENAI_MODEL_NAME_FALLBACK 可选后备模型

### 当前 .env 配置
```
OPENAI_BASE_URL=http://s7.tunnelfrp.com:10022/v1
OPENAI_API_KEY=sk-octopus-...
OPENAI_MODEL_NAME=glm-5.1
ALIBABA_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ALIBABA_API_KEY=sk-9b06...
ALIBABA_MODEL_EMBEDDING_NAME=text-embedding-v3
```
