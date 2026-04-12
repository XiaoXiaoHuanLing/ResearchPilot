# ResearchPilot 技术架构

## 前端
- Vue 3 + TypeScript + Vite
- Naive UI（暗色主题）
- Pinia（状态管理）
- Vue Router（路由）
- Tailwind CSS（样式）

## 后端
- Python 3.12 + FastAPI + uvicorn
- SQLAlchemy + SQLite（可升级 PostgreSQL）
- pydantic-settings（配置管理，读 .env）

## AI / RAG
- **LlamaIndex**：向量索引 + 检索 + query engine
- **ChromaDB**：持久化向量存储，单集合 + metadata 区分知识库
- **CompatibleOpenAIEmbedding**：自定义 BaseEmbedding，绕过模型名白名单，支持阿里云
- **LangChain**：ChatOpenAI 用于报告分析和对话综合
- **LangGraph**：StateGraph 驱动报告生成工作流

## Copilot Agent
- **LangGraph `create_react_agent`**：预构建 ReAct Agent，原生支持 streaming + checkpoint + tool calling
- **MemorySaver**：内存级 checkpoint，多轮对话上下文恢复
- **24 工具**：按功能域拆 8 文件，FUNC_MAP 模式注册
- **SSE 流式**：astream_events(v2) → token/tool_start/tool_end/done 事件
- **前端流式**：Vite proxy 独立 SSE 代理 + splice 重渲染触发 Vue 响应式

## 搜索与采集
- **Tavily / Serper**：联网搜索 API
- **httpx**：异步网页抓取
- **readability + BeautifulSoup**：正文提取
- **APScheduler**：专题定时采集调度

## 向量库设计（单集合 + 元数据）
- 集合名：`researchpilot_all`
- 每个文档携带 metadata：`kb_id`（知识库ID）、`kb_type`（bookmarks/upload）、`article_id`、`title`
- 全库检索：单次查询，统一排序
- 指定KB检索：MetadataFilter(kb_id=X, kb_type=Y)
- 删除KB：collection.delete(where={"kb_id": X}) 批量删除
- 文档分块：LlamaIndex 按 chunk_size=1024 自动分块嵌入

## 配置
- LLM 和 Embedding 分离配置（可走不同 base_url）
- 支持 OPENAI_* / ALIBABA_* 两组 .env 变量自动 fallback

## 目录结构
```
ResearchPilot/
├── docs/                          # 文档
│   ├── PRODUCT_OVERVIEW.md        # 产品定义
│   ├── TECH_ARCHITECTURE.md       # 技术架构（本文件）
│   └── PROGRESS.md                # 项目进度+待办
├── backend/
│   ├── app/
│   │   ├── api/routes/            # API路由
│   │   │   ├── articles.py        # 资讯（CRUD+采集+收藏+删除）
│   │   │   ├── knowledge_base.py  # 知识库（CRUD+文档上传）
│   │   │   ├── qa.py              # 问答/对话（RAG+联网搜索）
│   │   │   ├── reports.py         # 报告（生成+删除+导出）
│   │   │   ├── topics.py          # 专题（CRUD）
│   │   │   └── health.py          # 健康检查+系统状态
│   │   ├── core/config.py         # 配置（pydantic-settings）
│   │   ├── db/                    # 数据库模型+会话
│   │   ├── schemas/               # Pydantic Schema
│   │   └── services/              # 核心服务
│   │       ├── rag/engine.py      # RAG引擎（LlamaIndex+ChromaDB）
│   │       ├── chat.py            # 智能对话（三模式）
│   │       ├── ingestion.py       # 采集管道（搜索+抓取+提取）
│   │       ├── report_generator.py# 报告生成（LangGraph）
│   │       ├── scheduler.py       # APScheduler调度
│   │       └── seed.py            # 种子数据
│   ├── .env / .env.example
│   ├── researchpilot.db           # SQLite数据库
│   └── chroma_db/                 # ChromaDB持久化目录
├── frontend/
│   └── src/
│       ├── views/                 # 6个页面组件
│       ├── stores/index.ts        # Pinia Store
│       ├── api.ts                 # API调用层
│       ├── types.ts               # TypeScript类型
│       └── App.vue                # 布局+导航
└── .gitignore
```
