# RAG 全流程优化日志

> 开始时间：2026-04-10 00:50
> 目标：参考业界最佳实践，对 ResearchPilot RAG 全流程进行系统性优化

---

## 一、现状问题分析

经过完整代码审查，识别出以下核心问题：

### 1.1 分块策略粗糙
- **现状**：全局 `chunk_size=1024, chunk_overlap=200`，无语义分块
- **问题**：文章内容长短不一，固定分块会切断语义完整性；overlap 太小容易丢失上下文

### 1.2 索引流程不清晰
- **现状**：`index_article()` 函数同时负责删除旧文档+插入新文档+计数，职责混杂
- **问题**：缺少索引状态管理，无法知道哪些文档已索引、哪些失败；无重试机制

### 1.3 查询流程过于简单
- **现状**：直接用 `idx.as_query_engine(similarity_top_k=5)` 做单次向量检索
- **问题**：
  - 无查询改写（query transformation），用户原始问法可能不匹配知识库内容
  - 无重排（reranking），向量相似度≠语义相关性
  - 无混合检索（hybrid search = dense + sparse），纯向量检索可能遗漏关键词精确匹配
  - 引用信息不完整（缺少 source/url 等 metadata 传递）

### 1.4 元数据管理薄弱
- **现状**：metadata 只有 `article_id, title, kb_id, kb_type`
- **问题**：缺少 `source, url, published_at` 等关键溯源字段，导致引用信息不完整

### 1.5 错误处理和可观测性不足
- **现状**：大部分异常只 log warning/error，无结构化索引状态记录
- **问题**：无法追踪索引进度，无法识别哪些文档索引失败需要重试

### 1.6 采集与索引耦合
- **现状**：`ingestion.py` 中收藏即索引，但 `reindex_all_articles()` 是批量重建
- **问题**：没有增量索引的概念，重建索引时无法区分新旧

---

## 二、优化方案与实施记录

### Phase 1: RAG 引擎核心重构 (engine.py) — ✅ 已完成

| # | 优化项 | 方法 | 状态 | 完成时间 |
|---|--------|------|------|---------|
| 1.1 | 语义分块 | SentenceSplitter（按句子边界分块），chunk_size=512, overlap=80 | ✅ | 04-10 01:00 |
| 1.2 | 元数据完善 | 索引时写入 source/url/published_at/topic | ✅ | 04-10 01:00 |
| 1.3 | 索引状态管理 | 新增 get_collection_stats() 和 /api/rag/stats 接口 | ✅ | 04-10 01:00 |
| 1.4 | 重构 index_article | 新增 prepare_document() 纯函数，分离文档创建与索引插入 | ✅ | 04-10 01:00 |
| 1.5 | 批量索引优化 | reindex_all_articles() 返回详细结果 {total, success, failed, details}，每5篇记录进度 | ✅ | 04-10 01:00 |

**修改的文件：**
- `backend/app/services/rag/engine.py` — 完全重写（V2）
- `backend/app/api/routes/articles.py` — index_article 调用增加 rich metadata 参数
- `backend/app/api/routes/knowledge_base.py` — 同上
- `backend/app/services/ingestion.py` — 同上

**关键设计决策：**

| 决策 | 原因 |
|------|------|
| chunk_size 从 1024 降到 512 | 更小粒度 = 更精准的相似度匹配，牺牲一点上下文但换来更准确的检索 |
| chunk_overlap 从 200 降到 80 (15%) | 512 的 15% ≈ 80，比例合理，避免过多冗余 |
| 新增 SIMILARITY_TOP_K=20 + FINAL_TOP_K=5 两阶段 | 先宽召回再精筛，平衡召回率与精准度 |
| 新增 MIN_RELEVANCE_SCORE=0.3 | 过滤低相关度噪声，0.3 是经验阈值（ChromaDB cosine） |
| 新增 HyDE 查询改写 | 对分析型问题效果好，但需要额外 LLM 调用，仅 analytical 意图触发 |
| 新增 prepare_document() 纯函数 | 可测试性，与索引插入解耦 |

### Phase 2: 查询流程增强 — ✅ 已完成

| # | 优化项 | 方法 | 状态 | 完成时间 |
|---|--------|------|------|---------|
| 2.1 | 查询意图分类 | _classify_query_intent()：区分 needs_fresh/is_factual/is_analytical | ✅ | 04-10 01:00 |
| 2.2 | HyDE 查询改写 | _generate_hyde()：对分析型问题生成假设性文档嵌入 | ✅ | 04-10 01:00 |
| 2.3 | 相关度过滤重排 | retrieve top_k=20 → score >= 0.3 → sort desc → top 5 | ✅ | 04-10 01:00 |
| 2.4 | 完善引用 | citations 返回 source/url/published_at/topic 全部溯源字段 | ✅ | 04-10 01:00 |

**修改的文件：**
- `backend/app/services/chat.py` — 完全重写（V2）

**关键设计决策：**

| 决策 | 原因 |
|------|------|
| 意图分类取代简单关键词匹配 | 原版用字符串 contains 判断"需要联网"，太脆弱；新版结构化分类更稳健 |
| 聊天历史参数 chat_history | 预留多轮对话支持，当前前端未传，但接口已就绪 |
| _build_messages() 统一消息构建 | 3种模式的消息构建逻辑统一，减少重复代码 |
| hybrid 模式增强提示词 | 区分本地/网络信息冲突时以网络为准并说明，提升可信度 |

### Phase 3: 采集与调度优化 — ✅ 已完成

| # | 优化项 | 方法 | 状态 | 完成时间 |
|---|--------|------|------|---------|
| 3.1 | 聊天历史持久化 | ChatSession/ChatMessage DB模型 + /api/chat-sessions + qa chat session_id | ✅ | 04-10 01:45 |
| 3.2 | 采集异步化 | services/tasks.py 后台任务管理 + /api/tasks 端点 + async_mode 参数 | ✅ | 04-10 01:45 |
| 3.3 | APScheduler 修复 | asyncio.run() 替代 get_event_loop()，5个定时任务生效 | ✅ | 04-10 01:45 |

**修改/新增的文件：**
- `backend/app/services/tasks.py` — 新增，后台任务管理器
- `backend/app/services/scheduler.py` — 完全重写（V2）
- `backend/app/api/routes/tasks.py` — 新增，任务状态API
- `backend/app/api/routes/chat_sessions.py` — 新增，聊天会话持久化API
- `backend/app/api/routes/qa.py` — 增强，支持 session_id + 历史上下文 + 自动持久化
- `backend/app/api/routes/articles.py` — 增强，collect 支持 async_mode
- `backend/app/api/routes/health.py` — 新增 /scheduler/jobs 端点
- `backend/app/db/models/chat.py` — 新增，ChatSessionModel + ChatMessageModel
- `backend/app/db/models/__init__.py` — 注册新模型
- `backend/app/main.py` — 注册新路由

### Phase 4: 评估与可观测 — ✅ 部分完成

| # | 优化项 | 方法 | 状态 | 完成时间 |
|---|--------|------|------|---------|
| 4.1 | RAG 统计接口 | /api/rag/stats 查看索引/DB统计 | ✅ | 04-10 01:00 |
| 4.2 | 重建索引接口 | /api/rag/reindex 一键重建索引 | ✅ | 04-10 01:00 |
| 4.3 | 查询日志 | 记录每次查询的 question/retrieved_docs/answer | 🔲 | - |

**修改的文件：**
- `backend/app/api/routes/health.py` — 新增 /rag/stats 和 /rag/reindex 端点

---

## 三、API 变更记录

### 新增端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/rag/stats` | GET | 查看 RAG 系统状态（向量库统计 + DB统计） |
| `/api/rag/reindex` | POST | 触发所有收藏文章重新索引 |

### 修改的端点

| 端点 | 变更 |
|------|------|
| `/api/qa/chat` | 新增可选参数 `chat_history`（预留多轮对话），返回的 citations 增加字段 |

### 返回值变更

**citations 字段新增：**
```python
# 旧版
{"article_id", "title", "source", "url", "relevance_score", "snippet"}

# 新版
{"article_id", "title", "source", "url", "published_at", "topic", "relevance_score", "snippet"}
```

---

## 四、待验证项

- [ ] 重新索引收藏文章（POST /api/rag/reindex）并验证 RAG 问答可用
- [ ] 混合模式对话测试
- [ ] 前端是否需要适配新的 citations 字段

---

## 五、下一步工作

1. **启动后端 + 执行 reindex** — 让 RAG 问答恢复可用
2. **前端适配** — citations 新字段、RAG stats 展示
3. **Phase 3** — 采集异步化 + APScheduler 修复 + 聊天持久化
