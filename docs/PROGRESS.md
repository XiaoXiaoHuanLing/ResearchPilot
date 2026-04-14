# ResearchPilot 项目进度文档

> 最后更新：2026-04-14
> 项目路径：`E:\workspace\openclaw_project\ResearchPilot`

---

## 一、项目概述

ResearchPilot 是一个面向公开专题研究的 AI 助手平台。

**核心理念**：**主动追踪 · 知识沉淀 · 问答分析 · 报告生成 · Agent 自动化**

用户定义研究专题（关键词+调度计划），系统自动采集互联网资讯，收藏后进入向量知识库，可智能问答和报告生成，Copilot Agent 可用自然语言驱动全流程。

---

## 二、技术栈与当前配置

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

### 当前生效配置
- **Chat LLM**: `glm-5.1` via OPENAI 兼容代理（OPENAI_* 环境变量）
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
| RAG问答(knowledge) | ✅ |
| 联网搜索(search) | ✅ |
| 混合模式(hybrid) | ✅ 14s(优化后) |
| 报告生成 | ✅ |
| 聊天会话持久化 | ✅ |
| Copilot 非流式 + SSE 流式 | ✅ |
| 24工具端到端 | ✅ 24/24 |
| APScheduler定时采集 | ✅ |
| Supervisor 多Agent | ✅ |
| 模型统一(04-13改造) | ✅ 全部接口200 OK |

---

## 五、关键决策记录

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

---

## 六、当前已知问题

- [ ] 部分代理API不支持streaming（返回空），需确认API兼容性
- [ ] 报告PDF导出需安装weasyprint，否则回退HTML
- [ ] PowerShell终端中文显示乱码（数据正确，编码问题）
- [ ] Supervisor集成测试待前端实测验证

---

## 七、工作计划

### P1 — 待推进
| 任务 | 说明 | 状态 |
|------|------|------|
| Supervisor前端实测 | 确认Worker切换/并行/SSE | 🔲 |
| 前端citations适配 | published_at/topic字段 | 🔲 |
| 上下文压缩 | 长对话自动摘要 | 🔲 |
| Human-in-the-loop | 确认破坏性操作 | 🔲 |
| 虚拟文件系统 | Agent工作空间 | 🔲 |

### P2 — 功能完善
| 任务 | 状态 |
|------|------|
| PDF导出 | 🔲 |
| 用户认证系统 | 🔲 |
| 知识库文档预览 | 🔲 |
| 资讯去重（URL+title相似度） | 🔲 |
| 多轮对话上下文 | 🔲 |
| 前端全局错误处理 | 🔲 |

### P3 — 增强优化
| 任务 | 状态 |
|------|------|
| 采集反爬403长期方案 | 🔲 |
| Copilot上下文压缩 | 🔲 |
| PDF/DOCX文档解析 | 🔲 |
| PostgreSQL升级 | 🔲 |
| Docker部署 | 🔲 |
| 前端移动端适配 | 🔲 |
