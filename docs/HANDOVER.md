# ResearchPilot 项目交接文档

> 最后更新：2026-04-13 21:48 | 交接人：comi
> 项目路径：`E:\workspace\openclaw_project\ResearchPilot`

---

## 📋 一句话说明

ResearchPilot 是一个**公开专题研究 AI 助手**：定义专题→自动采集→收藏入库→智能问答→生成报告→Copilot Agent 全流程自动化。核心闭环已完成，进入功能完善阶段。

---

## 一、项目背景与定位

### 它解决什么问题

研究人员需要持续追踪特定领域公开信息（如舰船动态、武器装备），传统方式是手动搜索→收藏→整理→写报告，耗时且碎片化。ResearchPilot 把这个流程自动化：

1. **主动获取**：专题定时采集 + 手动URL采集 + 对话式搜索
2. **知识沉淀**：收藏即入向量库，上传文档也入库
3. **智能问答**：RAG检索+联网搜索+LLM生成，三种模式可选
4. **报告输出**：选文章+提示词，一键生成结构化研究报告
5. **Agent 自动化**：Copilot 一条消息完成搜索、采集、问答、报告全流程

### 约束
- 仅公开、非涉密信息
- 本地部署，单机运行（SQLite + ChromaDB）
- 单用户模式，暂无认证

---

## 二、系统架构

```
┌───────────────────────────────────────────────────────────────────┐
│  Vue 3 前端 (localhost:5173)                                       │
│  7页面: 仪表盘│专题│资讯│对话│报告│知识库│🤖Copilot               │
│  Vite代理: /api→:8000 │ SSE独立代理(不缓冲)                      │
└──────────────────────────┬────────────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────────────┐
│                    FastAPI Backend (端口 8000)                     │
│                                                                   │
│  ┌─ API Routes ───────────────────────────────────────────────┐   │
│  │ health│topics│articles│qa│reports│kb│copilot│tasks│chat   │   │
│  └──────────────────────────┬─────────────────────────────────┘   │
│                              │                                    │
│  ┌─ Core Services ──────────▼─────────────────────────────────┐   │
│  │                                                             │   │
│  │  🤖 Copilot (双模式)          📄 Report (LangGraph)          │   │
│  │  单Agent: 24工具+MemorySaver  StateGraph: collect→generate │   │
│  │  多Agent: Supervisor           fallback: LLM→聚合           │   │
│  │    → Researcher(并行采集)                                  │   │
│  │    → Analyst(分析报告)                                      │   │
│  │    → Manager(数据CRUD)                                     │   │
│  │                                                             │   │
│  │  💬 Chat (三模式)             🔍 Ingestion Pipeline          │   │
│  │  search / knowledge /        Tavily/Serper搜索               │   │
│  │  hybrid(RAG+搜索并行)        httpx抓取→readability提取      │   │
│  │                                                             │   │
│  │  📚 RAG Engine                ⏰ APScheduler                │   │
│  │  LlamaIndex+ChromaDB          专题定时采集                  │   │
│  │  CompatibleOpenAIEmbedding                                 │   │
│  │  as_retriever + LangChain合成                              │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─ External APIs ─────────────────────────────────────────────┐   │
│  │ Chat LLM (OPENAI_*)       Embedding (ALIBABA_*)            │   │
│  │ 搜索: Tavily + Serper      DB: SQLite + ChromaDB           │   │
│  └─────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────────┘
```

---

## 三、技术栈

| 层 | 技术 | 说明 |
|---|------|------|
| 前端 | Vue 3 + TypeScript + Vite + Naive UI + Pinia + Tailwind CSS | 暗色主题，7页面 |
| 后端 | FastAPI + SQLAlchemy + SQLite + pydantic-settings | Python 3.12 |
| RAG | LlamaIndex + ChromaDB（单集合+metadata） | CompatibleOpenAIEmbedding |
| LLM | LangChain ChatOpenAI + LangGraph | streaming=True |
| Agent | LangGraph create_react_agent + MemorySaver | 24工具 + Supervisor |
| 搜索 | Tavily / Serper API | 联网搜索 |
| 调度 | APScheduler | 专题定时采集 |

---

## 四、模型配置（04-13 统一改造后）

**聊天模型和嵌入模型彻底分离**：

| 用途 | 环境变量 | 当前值 |
|------|---------|--------|
| Chat LLM | `OPENAI_BASE_URL` + `OPENAI_API_KEY` + `OPENAI_MODEL_NAME` | `glm-5.1` via 代理 |
| Embedding | `ALIBABA_BASE_URL` + `ALIBABA_API_KEY` + `ALIBABA_MODEL_EMBEDDING_NAME` | `text-embedding-v3` via DashScope |
| Fallback | `OPENAI_MODEL_NAME_FALLBACK`（可选） | 未配置 |

**为什么这么分**：之前阿里云 qwen 做聊天，免费额度经常耗尽且配置散布各处。统一为 OpenAI 兼容 API 后，换模型只改 .env 不碰代码。

---

## 五、当前进度（截至 2026-04-13）

### ✅ P0 — 全部完成

| 任务 | 状态 | 备注 |
|------|------|------|
| QA hybrid 模式优化 | ✅ | 34s→14s，RAG+搜索并行 |
| Copilot 24工具端到端测试 | ✅ | 24/24 通过 |
| APScheduler 定时触发验证 | ✅ | async_mode 后台采集 |
| Copilot SSE 流式实测 | ✅ | 原生 streaming 打字机效果 |
| LLM Fallback 机制 | ✅ | with_fallbacks 自动切换 |
| 代码提交归档 | ✅ | 7+语义 commit |
| **LLM 模型统一改造** | ✅ | 去除 qwen chat，统一 OPENAI_* |

### ✅ P1 — 大部分完成

| 任务 | 状态 | 备注 |
|------|------|------|
| Supervisor 多 Agent SSE | ✅ | worker_switch + 并行采集 |
| Supervisor 集成测试 | ✅ | 简单/复杂任务均通过 |
| SSE 原生流式 | ✅ | streaming=True + on_chat_model_stream |
| 前端 citations 填充真实值 | 🔲 | RAG 返回 published_at/topic，前端待适配 |
| 上下文压缩 | 🔲 | 长对话自动摘要 |
| Human-in-the-loop | 🔲 | 确认破坏性操作 |
| 虚拟文件系统 | 🔲 | Agent 工作空间 |

### 🔲 P2 — 均未启动

PDF导出 / 用户认证 / 知识库文档预览 / 资讯去重 / 前端全局错误处理

---

## 六、关键决策记录（必须知道）

| 日期 | 决策 | 原因 |
|------|------|------|
| 04-09 | CompatibleOpenAIEmbedding | LlamaIndex 白名单拒绝非 OpenAI 模型名 |
| 04-09 | 单集合+元数据 | 跨库检索统一排序更准确 |
| 04-10 | as_retriever + LangChain 合成 | LlamaIndex 白名单 + 代理空回答 |
| 04-11 | tools/llm/agent 解耦重构 | 单文件 1100 行不可维护 |
| 04-11 | FUNC_MAP 模式 | async @tool 的 .func=None |
| 04-11 | create_react_agent 替换自定义 Graph | 原生支持 streaming/checkpoint |
| 04-12 | with_fallbacks 替代手写 fallback | 自动切换，无需 try/except |
| 04-12 | Supervisor JSON prompt | LLM 自由文本不可靠，强制 JSON 输出 |
| 04-12 | Researcher asyncio.gather | 并行采集提升效率 |
| **04-13** | **Chat LLM 统一 OPENAI_\*** | **去除阿里云 qwen chat 依赖** |
| **04-13** | **Embedding 保留 ALIBABA_\*** | **嵌入模型独立于聊天** |
| **04-13** | **SSE 全部原生 streaming** | **comi 要求原生流式，删除模拟流式代码** |

---

## 七、已知坑点（踩过的雷）

1. **uvicorn 不要用 --reload**：热更新不生效，必须完全重启
2. **所有 ChatOpenAI 必须 streaming=True**：某些代理 API 非流式返回 content=None
3. **Copilot Agent 单例缓存**：修改 LLM 配置后需调 `reset_agent()` / `reset_supervisor()` / `reset_all_workers()`
4. **ChromaDB 版本**：用 `>=0.4.0`，旧版 API 不兼容
5. **Workers.py 不要用 PowerShell Set-Content 修改**：会破坏 UTF-8 中文编码，用 Python 或编辑器
6. **.env 空值行必须注释掉**：`KEY=` 空行会覆盖默认值
7. **PowerShell 不支持 &&**：用 `;` 代替
8. **Vite SSE 代理必须独立配置**：默认代理会缓冲 SSE 流，导致不打字机

---

## 八、已修复 Bug 汇总（04-10~04-13，共 15 个）

| Bug | 根因 | 修复 |
|-----|------|------|
| LLM 返回空 | 代理 API 非流式 content=None | streaming=True |
| RAG answer 空 | LlamaIndex 白名单+代理空 | as_retriever+LangChain 合成 |
| .env 空值覆盖 | `KEY=` 空行覆盖默认 | 注释掉空行 |
| Tavily 中文 400 | search_depth="advanced" | 改 "basic" |
| 采集 403/418 | 爬虫 UA | 浏览器 UA |
| 报告 content 空 | gpt-5.4 不稳定 | LLM 链+聚合 fallback |
| async @tool func=None | LangChain 已知问题 | FUNC_MAP 模式 |
| Copilot 回复空 | 条件边没处理 ToolMessage | 修复 should_continue |
| SSE 不打字机 | Vite proxy 缓冲+Vue 响应式 | 独立代理+splice 重渲染 |
| QA hybrid 34s | 串行+LLM 两次+ingest 阻塞 | 并行+retrieval_only+ingest 后台 |
| update_topic 500 | session 脱离后访问属性 | commit 前保存变量 |
| 递归超限 | recursion_limit=25 不够 | 提升到 50 |
| LLM 403 无 fallback | 额度耗尽直接报错 | with_fallbacks |
| 阿里云 chat 耦合 | qwen 优先逻辑散布 6 处 | 统一 OPENAI_* 配置 |
| workers.py 编码损坏 | PowerShell Set-Content GBK | Python 重写 |

---

## 九、数据状态

- **SQLite**: 4 专题 / 60 文章 / 3 知识库 / 9 报告 / 5 聊天会话
- **ChromaDB**: 12 chunks（3 篇收藏文章向量索引）
- **Git**: 最新 commit `77284ae`（docs update），04-13 模型统一改造已提交（`0c4a229` refactor + `77284ae` docs）

---

## 十、如何启动

### 后端

```bash
cd E:\workspace\openclaw_project\ResearchPilot\backend
.venv\Scripts\activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 前端

```bash
cd E:\workspace\openclaw_project\ResearchPilot\frontend
npm run dev
```

### 必须配置的环境变量

```bash
# Chat LLM
OPENAI_BASE_URL=https://your-api/v1
OPENAI_API_KEY=your-key
OPENAI_MODEL_NAME=glm-5.1

# Embedding
ALIBABA_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
ALIBABA_API_KEY=your-key
ALIBABA_MODEL_EMBEDDING_NAME=text-embedding-v3

# 搜索（至少配一个）
TAVILY_API_KEY=your-key
```

---

## 十一、下一步优先级

1. **Git commit** — 04-13 模型统一改造代码未提交
2. **前端 citations 适配** — RAG 已返回 published_at/topic，前端展示待填充
3. **上下文压缩** — 长对话自动摘要，避免 token 爆炸
4. **Human-in-the-loop** — 破坏性操作（如删除知识库）需用户确认
5. **PDF 导出** — 当前只能导出 MD
6. **资讯去重** — 相似 URL/title 自动合并

---

## 十二、文档索引

| 文档 | 内容 |
|------|------|
| `docs/ONBOARDING.md` | 新人上手指南（30秒了解+技术栈+启动+关键决策） |
| `docs/PROGRESS.md` | 详细进度（功能/验证/Bug/决策/Git 历史） |
| `docs/TODO.md` | 工作待办 + 每日工作日志 |
| `docs/TECH_ARCHITECTURE_V1.md` | 技术架构详解（模型配置/Copilot/RAG/数据模型） |
| `docs/PRODUCT_OVERVIEW.md` | 产品定义与使用场景 |
| `docs/AGENT_MODULE_DESIGN.md` | Copilot Agent 设计 |
| `docs/MULTI_AGENT_DESIGN.md` | 多 Agent 协作设计 |
| `docs/HANDOVER.md` | 本文档 — 交接清单 |
