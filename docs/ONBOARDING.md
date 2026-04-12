# ResearchPilot 项目协作说明

> 给合作者的快速上手指南 — 最后更新：2026-04-10

---

## 一、这是什么项目

ResearchPilot 是一个面向公开专题研究的 AI 助手平台。用户定义研究专题（关键词+调度），系统自动从互联网采集资讯，用户收藏后进入向量知识库，可进行智能问答和报告生成。

**项目路径**：`E:\workspace\openclaw_project\ResearchPilot`

**在线访问**：前端 `http://localhost:5173`，后端 `http://localhost:8000`

---

## 二、技术栈一览

- 前端：Vue 3 + Naive UI + Pinia + Tailwind CSS
- 后端：FastAPI + SQLAlchemy + SQLite
- RAG：LlamaIndex + ChromaDB（单集合+元数据区分知识库）
- LLM：LangChain + LangGraph
- 搜索：Tavily / Serper API
- 抓取：httpx + readability + BeautifulSoup

---

## 三、先看这3个文档（按顺序）

| # | 文件 | 内容 | 看完你将了解 |
|---|------|------|-------------|
| 1 | `docs/PRODUCT_OVERVIEW.md` | 产品定义、使用场景、功能模块 | 项目做什么、给谁用 |
| 2 | `docs/TECH_ARCHITECTURE_V1.md` | 技术架构、目录结构、关键设计决策 | 怎么实现的、代码在哪 |
| 3 | `docs/PROGRESS.md` | 已完成功能清单、已知问题、下一步计划（P0/P1/P2） | 做到哪了、接下来做什么 |

---

## 四、关键代码文件速查

### 4.1 前端核心（`frontend/src/`）

| 文件 | 作用 |
|------|------|
| `App.vue` | 整体布局 + 左侧导航菜单 |
| `router/index.ts` | 路由定义（6个页面） |
| `stores/index.ts` | Pinia Store（topics/articles/reports/kb 四个） |
| `api.ts` | 所有后端 API 调用 |
| `types.ts` | TypeScript 类型定义 |
| `views/DashboardView.vue` | 仪表盘（系统状态+统计+概览） |
| `views/TopicsView.vue` | 专题管理（CRUD+关键词+调度） |
| `views/ArticlesView.vue` | 资讯中心（搜索+收藏+删除+采集+详情） |
| `views/QaView.vue` | 智能对话（三种模式+知识库选择） |
| `views/ReportsView.vue` | 报告中心（选文章+提示词生成+导出） |
| `views/KnowledgeBaseView.vue` | 知识库管理（新建+上传+文档列表） |

### 4.2 后端核心（`backend/app/`）

**API 路由**（`api/routes/`）：

| 文件 | 端点前缀 | 功能 |
|------|---------|------|
| `health.py` | `/api` | 健康检查 + 系统状态 + RAG统计 + 重建索引 + 调度任务列表 |
| `topics.py` | `/api/topics` | 专题 CRUD |
| `articles.py` | `/api/articles` | 资讯列表/收藏/删除/采集(同步+异步) |
| `qa.py` | `/api/qa` | RAG问答 + 智能对话(支持session_id持久化) |
| `reports.py` | `/api/reports` | 报告生成/删除/导出 |
| `knowledge_base.py` | `/api/knowledge-bases` | 知识库+文档管理 |
| `tasks.py` | `/api/tasks` | 后台任务状态查询 |
| `chat_sessions.py` | `/api/chat-sessions` | 聊天会话CRUD+消息持久化 |

**核心服务**（`services/`）：

| 文件 | 作用 | 重点关注 |
|------|------|---------|
| `rag/engine.py` | RAG 引擎 | 单集合+metadata设计、CompatibleOpenAIEmbedding、分块逻辑 |
| `chat.py` | 智能对话 | 三种模式(search/knowledge/hybrid)、自动联网判断 |
| `ingestion.py` | 采集管道 | Tavily搜索→httpx抓取→readability提取→LLM摘要→入库 |
| `report_generator.py` | 报告生成 | LangGraph StateGraph、支持用户提示词 |
| `scheduler.py` | 定时调度 | APScheduler，**目前已注册但未实际触发采集** |

**数据模型**（`db/models/`）：

| 文件 | 表名 | 说明 |
|------|------|------|
| `topic.py` | topics | 专题（名称/关键词/调度/启用状态） |
| `article.py` | articles | 资讯（标题/来源/摘要/原文/收藏状态） |
| `report.py` | reports | 报告（标题/专题/摘要/详细内容） |
| `knowledge_base.py` | knowledge_bases | 知识库（名称/类型/是否默认） |
| `kb_document.py` | kb_documents | 知识库文档（标题/内容/文件类型/索引状态） |

**配置**（`core/config.py`）：
- pydantic-settings 读 `.env`，支持 OPENAI_* / ALIBABA_* 两组变量自动 fallback
- LLM 和 Embedding 分离配置（可走不同 base_url）

---

## 五、当前状态总结

### ✅ 已完成（可用）
- 6个前端页面全部可用
- 后端 API 全部真实实现（非模拟数据）
- RAG 问答 + 联网搜索对话已验证通过
- 报告生成（选文章+提示词）已验证
- 知识库管理（新建/上传/删除）已验证
- 资讯收藏→向量库自动同步
- 报告导出 Markdown

### ⚠️ 已知问题
- ChromaDB 新集合 `researchpilot_all` 目前为空，需重新收藏文章建立索引
- APScheduler 定时采集未实际触发
- 采集耗时较长，前端可能超时
- 聊天历史不持久化

---

## 六、下一步工作（优先级排序）

### P0 — 核心体验（建议先做）
1. **重新索引收藏文章** — 让 RAG 问答能查到数据
2. **APScheduler 定时采集跑通** — 自动采集是核心功能
3. **聊天历史持久化** — 多轮对话需要上下文
4. **采集异步化** — 避免前端超时

### P1 — 功能完善
5. PDF 导出 | 6. 用户认证 | 7. 文档预览 | 8. 去重优化 | 9. 多轮对话

### P2 — 增强优化
10. PostgreSQL | 11. Docker | 12. SSE流式输出 | 13. PDF/DOCX解析 | 14. 移动端适配

---

## 七、如何启动项目

```bash
# 后端
cd E:\workspace\openclaw_project\ResearchPilot\backend
.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000

# 前端
cd E:\workspace\openclaw_project\ResearchPilot\frontend
npm run dev
```

环境变量配置在 `backend/.env`，需要：
- `ALIBABA_API_KEY` / `ALIBABA_BASE_URL` / `ALIBABA_MODEL_NAME` — LLM
- `ALIBABA_MODEL_EMBEDDING_NAME` — Embedding模型
- `TAVILY_API_KEY` — 联网搜索
