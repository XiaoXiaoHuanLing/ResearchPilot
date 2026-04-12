# ResearchPilot 项目交接清单

> 生成时间：2026-04-11 23:00
> 交接人：comi / 来财 (AI 助手)

---

## 一、30秒概览

ResearchPilot 是一个**面向公开专题研究的 AI 助手平台**：用户定义研究专题 → 系统自动采集互联网资讯 → 用户收藏进入知识库 → 智能问答 + 报告生成。

核心卖点：**自然语言 Copilot** — 用户说一句话，Agent 自主调用24个工具完成所有操作。

---

## 二、项目结构

```
E:\workspace\openclaw_project\ResearchPilot\
├── backend/           # FastAPI 后端 (Python 3.12)
│   ├── app/
│   │   ├── api/routes/    # 9个路由文件: articles/chat_sessions/copilot/health/knowledge_base/qa/reports/tasks/topics
│   │   ├── core/config.py # 配置: .env → Settings
│   │   ├── db/             # SQLAlchemy ORM: 7个模型
│   │   ├── schemas/       # Pydantic 请求/响应
│   │   └── services/
│   │       ├── chat.py          # QA 对话（3种模式）
│   │       ├── copilot/         # 🤖 智能助手（解耦架构）
│   │       │   ├── agent/       # LangGraph create_react_agent
│   │       │   ├── llm/         # 模型选择（阿里云优先）
│   │       │   └── tools/       # 8个文件24工具 + FUNC_MAP
│   │       ├── ingestion.py     # 搜索+抓取+入库
│   │       ├── rag/engine.py    # LlamaIndex + ChromaDB
│   │       ├── report_generator.py
│   │       ├── scheduler.py     # APScheduler 定时采集
│   │       ├── seed.py          # 初始演示数据
│   │       └── tasks.py         # 后台任务管理
│   ├── .env               # ⚠️ 不入库，包含API密钥
│   ├── .env.example       # 配置模板
│   └── pyproject.toml
├── frontend/          # Vue 3 前端
│   ├── src/
│   │   ├── views/    # 7个页面: Articles/Copilot/Dashboard/KB/QA/Reports/Topics
│   │   ├── api.ts    # API 调用封装
│   │   ├── types.ts  # TypeScript 类型
│   │   ├── router/   # 路由
│   │   ├── stores/   # Pinia 状态
│   │   └── style.css # Tailwind + 自定义
│   └── vite.config.ts  # ⚠️ SSE 独立代理配置
├── docs/              # 项目文档（详见下方）
└── .gitignore
```

---

## 三、快速启动

### 后端
```bash
cd E:\workspace\openclaw_project\ResearchPilot\backend
# 1. 复制 .env.example → .env，填入API密钥（见下方配置说明）
# 2. 安装依赖（已用 uv 管理，.venv 已存在）
.venv\Scripts\activate
# 3. 启动
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 前端
```bash
cd E:\workspace\openclaw_project\ResearchPilot\frontend
npm install   # 首次
npm run dev   # http://localhost:5173
```

### 注意事项
- **不要用 `--reload`** — uvicorn 热更新不生效，改代码后需完全重启
- **Vite 代理** — 前端 `/api` 代理到后端 `:8000`，SSE 端点有独立代理规则
- **PowerShell** — 不支持 `&&`，用 `;` 代替

---

## 四、核心配置 (.env)

| 变量 | 说明 | 当前值 |
|------|------|--------|
| `RESEARCHPILOT_OPENAI_API_KEY` | 代理 GPT API key | 有值 |
| `RESEARCHPILOT_OPENAI_BASE_URL` | 代理 API base URL | 有值 |
| `RESEARCHPILOT_LLM_MODEL` | 代理模型名 | gpt-5.4 |
| `RESEARCHPILOT_ALIBABA_API_KEY` | 阿里云 DashScope key | 有值（优先使用） |
| `RESEARCHPILOT_ALIBABA_BASE_URL` | 阿里云 API base | 有值 |
| `RESEARCHPILOT_ALIBABA_MODEL_NAME` | 阿里云模型 | qwen3.5-flash |
| `RESEARCHPILOT_EMBEDDING_API_KEY` | Embedding API key | 阿里云 |
| `RESEARCHPILOT_EMBEDDING_MODEL` | Embedding 模型 | text-embedding-v3 |
| `RESEARCHPILOT_TAVILY_API_KEY` | Tavily 搜索 | 有值 |
| `RESEARCHPILOT_SERPER_API_KEY` | Serper 搜索 | 有值 |

**⚠️ 空值行要注释掉** — `RESEARCHPILOT_OPENAI_API_KEY=` 空值会覆盖有效配置！

---

## 五、技术决策速查（为什么这样设计）

| 决策 | 原因 |
|------|------|
| Copilot 用 `create_react_agent` 而非自定义 StateGraph | 原生支持 streaming + checkpoint + tool calling |
| FUNC_MAP 模式 | async @tool 的 `.func=None`，需要独立函数映射 |
| LlamaIndex 只做检索，LangChain 做答案生成 | LlamaIndex 模型白名单拒绝 qwen + 代理非流式返回空 |
| 所有 ChatOpenAI 加 `streaming=True` | 代理 gpt-5.4 非流式返回 content=None |
| QA/Copilot LLM 优先阿里云 qwen | 代理太慢(12-16s)，qwen 3-4s |
| ChromaDB 单集合+元数据 | 跨库检索统一排序更准确 |
| Vite `/api/copilot/chat/stream` 独立代理 | 防止 SSE 响应被代理缓冲 |
| `rag_query(retrieval_only=True)` | hybrid 模式只检索不生成，省掉一次 LLM 调用 |

---

## 六、文档索引

| 文档 | 内容 |
|------|------|
| `docs/PROGRESS.md` | **主文档** — 完整进度、架构图、Bug记录、验证结果、决策记录 |
| `docs/TODO.md` | 待办清单 + 工作日志 |
| `docs/TECH_ARCHITECTURE_V1.md` | 技术架构总览 |
| `docs/PRODUCT_OVERVIEW.md` | 产品概览 |
| `docs/AGENT_MODULE_DESIGN.md` | Copilot 智能助手设计文档 |
| `docs/RAG_OPTIMIZATION_LOG.md` | RAG 优化过程记录 |
| `docs/ONBOARDING.md` | 协作上手指南 |
| `docs/HANDOVER.md` | **本文档** — 交接清单 |

---

## 七、未完成工作（按优先级）

### 🔴 P0 — 必须完成

1. **Copilot 24工具端到端测试**
   - 只验证了 `list_topics` 和 `get_system_status`
   - 其余22个工具可能有参数/路径问题
   - 方法：`POST /api/copilot/chat/stream` 逐个触发
   - 重点测试：`search_web`、`rag_query`、`generate_report`、`ingest_url`

2. **APScheduler 定时触发验证**
   - 手动触发已通，定时触发未确认
   - 方法：等一个调度周期看日志，或查 `/api/copilot/tools` 的 `get_scheduler_jobs`

3. **Copilot SSE 流式浏览器实测**
   - 代码已修（splice重渲染 + Vite代理 + chunk兼容）
   - 但未在浏览器实测打字机效果
   - 测试：打开 http://localhost:5173 → Copilot → 发"你好"和"列出所有专题"

4. **代码提交**
   - 大量未提交变更
   - 建议：清理 `__pycache__`、`chroma_db/`、`*.db` 后 commit

### 🟡 P1 — 功能完善

5. **前端 citations 适配新字段** — 后端已返回 `published_at`/`topic`，前端没展示
6. **Copilot 虚拟文件系统是空壳** — `write/read_context_file` 只是 stub
7. **PDF 导出** — 需 weasyprint，目前回退 HTML
8. **前端全局错误处理** — API 失败无统一提示
9. **资讯去重** — URL + title 相似度去重

### 🟢 P2 — 增强优化

10. Copilot 子 Agent 协作（deepagents / LangGraph multi-agent）
11. Copilot 上下文压缩（长对话自动摘要）
12. Copilot Human-in-the-loop（破坏性操作确认）
13. 采集反爬长期方案
14. PDF/DOCX 文档解析
15. 用户认证系统
16. Docker 部署
17. PostgreSQL 升级
18. 移动端适配

---

## 八、已知坑点 ⚠️

1. **qwen DashScope API 偶尔极慢** — 有时简单调用也要 40s，非代码问题
2. **代理 gpt-5.4 非流式返回 content=None** — 必须用 `streaming=True`
3. **uvicorn --reload 不生效** — 必须完全重启
4. **PowerShell 不支持 `&&`** — 用 `;` 或分开执行
5. **`.env` 空值行覆盖** — `KEY=` 空值会覆盖之前的有效配置，必须注释掉
6. **杀进程要看进程树** — Windows 下 uvicorn 有 reloader 子进程，杀父可能连杀子
7. **Copilot `agent.py` 已删除** — 旧版单体文件，现在用 `agent/__init__.py`
8. **ChromaDB 数据在 `backend/chroma_db/`** — 已加入 .gitignore

---

## 九、数据库状态

- **SQLite**: `backend/researchpilot.db`
- **7 张表**: articles, topics, reports, knowledge_bases, kb_documents, chat_sessions, chat_messages
- **演示数据**: 4个专题、60篇文章、3个知识库、9份报告、5个聊天会话
- **ChromaDB**: 12 chunks（3篇收藏文章的向量索引）

---

## 十、API 端点速查

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 系统状态 |
| GET/POST/DELETE | `/api/topics` | 专题 CRUD |
| GET/POST | `/api/articles` | 文章列表/采集 |
| POST | `/api/articles/{id}/bookmark` | 收藏/取消 |
| GET/POST/DELETE | `/api/knowledge-bases` | 知识库管理 |
| POST | `/api/qa/chat` | 智能问答（3种模式） |
| GET/POST | `/api/reports` | 报告列表/生成 |
| GET/POST/DELETE | `/api/chat-sessions` | 聊天会话 |
| POST | `/api/copilot/chat` | Copilot 非流式 |
| POST | `/api/copilot/chat/stream` | Copilot SSE 流式 |
| GET | `/api/copilot/tools` | 工具列表(24) |
| GET | `/api/tasks` | 后台任务 |
| GET | `/api/rag/stats` | RAG 索引状态 |
| POST | `/api/rag/reindex` | 重建索引 |

---

_祝顺利 🚀_
