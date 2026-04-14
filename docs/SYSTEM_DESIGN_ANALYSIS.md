# ResearchPilot 系统设计深度分析

> 创建时间：2026-04-14
> 目的：为下一阶段模块打磨优化提供基线分析，明确每一步设计与业界标准流程的对比、当前优化的价值与不足、以及优化方向

---

## 一、RAG 全链路设计分析

### 1.1 标准业界 RAG 流程

```
用户问题
  │
  ▼
① 查询预处理（query preprocessing）
   - 拼写纠错、同义词扩展、意图分类
  │
  ▼
② 查询改写 / 增强（query transformation）
   - HyDE / Multi-Query / Step-back / Decomposition
  │
  ▼
③ 检索（Retrieval）
   - Dense 向量检索 / Sparse BM25 / Hybrid 混合检索
   - top_k 候选
  │
  ▼
④ 重排序（Reranking）
   - Cross-Encoder / LLM Reranker / ColBERT
   - 精排后取 top_n
  │
  ▼
⑤ 上下文组装（Context Assembly）
   - 去重 / 截断 / 递归压缩 / Lost-in-the-Middle 重排
  │
  ▼
⑥ LLM 生成（Answer Generation）
   - System Prompt + Context + Question → Answer
   - 流式输出 / 引用标注
  │
  ▼
⑦ 后处理（Post-processing）
   - 引用验证 / 事实核查 / 答案结构化
```

### 1.2 ResearchPilot 当前 RAG 实现

**文件**: `backend/app/services/rag/engine.py` (22KB, ~560行)

```
用户问题
  │
  ▼
① 查询预处理
   ✗ 无（直接使用原始问题）
  │
  ▼
② 查询改写
   △ HyDE（可选，默认关闭）
     - 实现：LLM 生成假设性回答 → 嵌入假设性回答 → 检索
     - 问题：每次 HyDE 增加一次 LLM 调用（~30s），成本高
     - 默认关闭，仅在 chat.py knowledge 模式的分析型问题时开启
  │
  ▼
③ 检索
   ✓ Dense 向量检索（ChromaDB + CompatibleOpenAIEmbedding）
   ✗ 无 Sparse BM25
   ✗ 无 Hybrid 混合检索
   - top_k=10 候选（SIMILARITY_TOP_K）
   - 支持 Metadata 过滤（kb_id / kb_type）
  │
  ▼
④ 重排序
   △ 简单分数过滤（MIN_RELEVANCE_SCORE=0.3）
     - 仅过滤低分节点，非真正重排序
     - 无 Cross-Encoder / LLM Reranker
  │
  ▼
⑤ 上下文组装
   △ 简单拼接
     - 每个节点取 text[:600] 截断
     - 按分数降序排列取 top_k=5
     - 无去重 / 无压缩 / 无 Lost-in-the-Middle 重排
  │
  ▼
⑥ LLM 生成
   ✓ LangChain ChatOpenAI（绕过 LlamaIndex 白名单限制）
   - streaming=True
   - 硬编码 System Prompt
   - 单轮生成，无多轮追问
  │
  ▼
⑦ 后处理
   ✗ 无引用验证 / 事实核查
   ✓ 结构化 citation 输出（article_id, title, source, url, published_at, relevance_score, snippet）
```

### 1.3 索引链路（Ingestion Pipeline）

```
原始文档（文章/上传文件）
  │
  ▼
① 文档准备
   ✓ prepare_document() — 纯函数，title + content + rich metadata
   - metadata: article_id, title, kb_id, kb_type, source, url, published_at, topic
  │
  ▼
② 分块（Chunking）
   ✓ SentenceSplitter（语义分块，尊重句子边界）
   - chunk_size=512, chunk_overlap=80（15% 重叠）
   - V1 是全局固定 1024/200，V2 优化为 512/80 更精细
   ✗ 无自适应分块（按文章长度动态调整）
   ✗ 无结构化分块（按标题/段落层级）
  │
  ▼
③ 嵌入（Embedding）
   ✓ CompatibleOpenAIEmbedding — 绕过 LlamaIndex 白名单
   - text-embedding-v3（阿里云 DashScope）
   - 批量嵌入：每批 20 条
   ✗ 无嵌入缓存
  │
  ▼
④ 存储（Storage）
   ✓ ChromaDB 单集合 + metadata 设计
   - 优势：跨 KB 统一排序更准确
   - 通过 kb_id/kb_type metadata 过滤实现按 KB 查询
   ✗ 无索引状态追踪（不知道哪些文档成功/失败/缺失）
  │
  ▼
⑤ 同步机制
   ✓ 收藏文章时自动 index_article()
   ✓ 取消收藏时自动 delete_article_from_index()
   ✓ 批量重索引 reindex_all_articles()
   ✗ 无增量索引（每次全量 delete + insert）
   ✗ 无索引状态持久化到 DB
```

### 1.4 当前优化的价值与不足

| 环节 | 当前优化 | 价值 | 不足 | 优化优先级 |
|------|---------|------|------|-----------|
| 分块 | SentenceSplitter 512/80 | ✅ 比固定 1024 精细 | ❌ 无自适应/结构化分块 | P2 |
| 嵌入 | CompatibleOpenAIEmbedding | ✅ 绕过白名单限制 | ❌ 无缓存 | P3 |
| 检索 | Dense + metadata 过滤 | ✅ 单集合统一排序 | ❌ 无 BM25/混合检索 | **P1** |
| 查询改写 | HyDE（可选） | △ 有但默认关闭 | ❌ 成本高，无 Multi-Query | P2 |
| 重排序 | 分数阈值过滤 | △ 粗粒度 | ❌ 无 Cross-Encoder | **P1** |
| 上下文组装 | 拼接 + 截断 | △ 可用 | ❌ 无压缩/去重/重排 | P2 |
| LLM 生成 | LangChain 合成 | ✅ 绕过限制 | ❌ 无引用验证 | P3 |
| 索引同步 | 收藏自动入库 | ✅ 可用 | ❌ 无增量/状态追踪 | P2 |
| 回退 | 关键词检索 | ✅ 有兜底 | — | — |

### 1.5 RAG 优化路线建议

#### P1 — 核心检索质量（影响最大）

1. **混合检索（Hybrid Search）**
   - 现状：纯 Dense 向量检索
   - 问题：关键词精确匹配（如型号、人名）可能遗漏
   - 方案：Dense + BM25(Sparse) → Reciprocal Rank Fusion(RRF) 融合
   - 实现：ChromaDB 本身不支持 BM25，需要：
     - 方案A：ChromaDB 向量检索 + SQLite FTS5 全文检索 → RRF 融合
     - 方案B：换用 Qdrant/Milvus 支持原生 hybrid search
   - 预期效果：关键词 + 语义双保险，召回率显著提升

2. **重排序（Reranking）**
   - 现状：仅分数阈值过滤（0.3），无精排
   - 问题：Dense 检索 top-10 中噪声多，回答质量取决于最相关的几条
   - 方案：
     - 方案A：LLM Reranker（用小模型对 query-doc pair 打分，成本低）
     - 方案B：Cohere/BGE Re-ranker（专业 rerank 模型，效果好）
     - 方案C：简单的 LLM 打分 prompt（无额外模型依赖）
   - 预期效果：top-5 精度大幅提升，减少无关上下文干扰

#### P2 — 检索增强与上下文管理

3. **查询改写增强**
   - Multi-Query：将用户问题改写为多个子问题，并行检索后合并
   - Step-back：将具体问题抽象为更一般性问题，先检索背景知识
   - 与 HyDE 互补而非替代

4. **自适应分块**
   - 短文章（<500字）：不分块
   - 中文章（500-3000字）：chunk_size=512
   - 长文章（>3000字）：chunk_size=768 + 结构化（按标题层级）

5. **上下文压缩**
   - 长上下文截断 → 递归摘要压缩（ContextualCompression）
   - Lost-in-the-Middle：将最相关文档放在首尾，次相关放中间

6. **索引增量同步**
   - DB 增加 `rag_indexed` 字段
   - 只索引未索引的文档，避免全量重索引
   - 失败重试机制

#### P3 — 高级优化

7. **嵌入缓存**（减少重复嵌入调用）
8. **引用验证**（LLM 后处理，验证答案与引用的一致性）
9. **多轮 RAG**（追问机制，信息不足时自动追问）

---

## 二、智能助手多智能体协作设计分析

### 2.1 标准多智能体协作模式

业界主流的多智能体编排模式：

```
模式 1: Supervisor（中心调度）
━━━━━━━━━━━━━━━━━━━━━━━
    用户 → Supervisor → Worker A
                    → Worker B  （Supervisor 决定谁干活）
                    → Worker C
    优势：可控、可观测、简单
    劣势：Supervisor 成为瓶颈

模式 2: Swarm（自主协作）
━━━━━━━━━━━━━━━━━━━━━━━
    用户 → Agent A ↔ Agent B ↔ Agent C
    （Agent 自主决定何时交接给谁）
    优势：灵活、无中心瓶颈
    劣势：不可控、易发散

模式 3: 分层 Supervisor（Hierarchical）
━━━━━━━━━━━━━━━━━━━━━━━━━
    用户 → Top Supervisor
              → Sub-Supervisor A → Worker A1, A2
              → Sub-Supervisor B → Worker B1, B2
    优势：可扩展、分层管理
    劣势：复杂度高

模式 4: MapReduce（并行聚合）
━━━━━━━━━━━━━━━━━━━━━━━━━━
    用户 → Planner → [Task1, Task2, Task3]
                        ↓ 并行     ↓ 并行     ↓ 并行
                      Worker1    Worker2    Worker3
                        └──────────┬──────────┘
                                   ▼
                              Aggregator → 最终结果
    优势：并行、高效
    劣势：任务需可分解
```

### 2.2 ResearchPilot 当前多智能体实现

**文件**: `backend/app/services/copilot/agent/`

```
架构: Supervisor 模式（模式1）+ 部分并行
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

用户消息
  │
  ▼
┌──────────────────────────────────────┐
│  Supervisor 节点                      │
│                                      │
│  Phase 1（新请求）:                   │
│    LLM 分析意图 → JSON 任务分解       │
│    {"delegate": true, "tasks": [...]} │
│                                      │
│  Phase 2+（Worker 返回后）:           │
│    纯逻辑路由（不调 LLM）             │
│    → 找下一个 pending worker          │
│    → 或综合结果                       │
└──────┬───────────────────────────────┘
       │
       ├── researcher_node ──── asyncio.gather（并行执行所有 researcher 任务）
       │     工具: search_web, ingest_url, collect_topic, list_topics
       │
       ├── analyst_node ──── 串行执行单个任务
       │     工具: rag_query, rag_stats, rag_reindex, generate_report, list_reports
       │
       └── manager_node ──── 串行执行单个任务
             工具: topic CRUD, article CRUD, KB CRUD, system status, context files
```

**图编排（LangGraph StateGraph）**:

```python
START → supervisor → [conditional route]
                      ├── researcher → supervisor
                      ├── analyst   → supervisor
                      ├── manager   → supervisor
                      └── FINISH    → END
```

### 2.3 当前各环节设计与标准对比

#### 2.3.1 任务分解（Supervisor Phase 1）

| 维度 | 标准做法 | 当前实现 | 差距分析 |
|------|---------|---------|---------|
| 分解方式 | LLM + 结构化输出 | LLM → JSON prompt | ✅ 一致，JSON prompt 比自由文本可靠 |
| 分解粒度 | 可递归分解 | 单层分解 | ❌ 复杂任务无法再拆分 |
| 角色映射 | 动态角色池 | 3 固定角色 | ❌ 不灵活，新增角色需改代码 |
| 错误处理 | 解析失败→重试 | fallback→manager | △ 兜底可用，但失去意图识别 |
| 约束提示 | Supervisor 自身无工具 | 明确告知"你无工具" | ✅ 防止 Supervisor 编造数据 |

#### 2.3.2 任务调度与路由

| 维度 | 标准做法 | 当前实现 | 差距分析 |
|------|---------|---------|---------|
| 调度策略 | 自适应/优先级队列 | 固定顺序：researcher→analyst→manager | ❌ 不够灵活，可能非最优 |
| 并行度 | 所有独立任务并行 | 仅 researcher 内部并行 | ❌ researcher+manager 理论上可并行 |
| 依赖管理 | DAG 依赖图 | 无 | ❌ 无法表达任务间依赖 |
| 超时控制 | 每任务超时 | 无 | ❌ 单任务卡住会阻塞整个流程 |
| 重试机制 | 失败重试 | 标记 failed 继续 | △ 健壮但无重试 |

#### 2.3.3 Worker 执行

| 维度 | 标准做法 | 当前实现 | 差距分析 |
|------|---------|---------|---------|
| Agent 类型 | ReAct / Reflexion / Plan-Execute | create_react_agent | ✅ 标准 ReAct |
| 工具分配 | 按需动态绑定 | 静态预分配 | △ 简单但不够灵活 |
| 上下文隔离 | 独立线程 + 共享记忆 | 独立 checkpointer | ✅ 隔离好 |
| 结果截断 | 全量返回 | result[:500] 截断 | ❌ 信息可能丢失 |
| 递归限制 | 25（worker）/ 50（单agent） | 同 | ✅ 合理 |

#### 2.3.4 结果综合（Synthesis）

| 维度 | 标准做法 | 当前实现 | 差距分析 |
|------|---------|---------|---------|
| 单任务 | 直接透传 | ✅ 直接透传 | ✅ 省一次 LLM 调用 |
| 多任务 | LLM 综合摘要 | ✅ LLM 综合摘要 | ✅ 一致 |
| 失败处理 | 部分失败仍综合 | ✅ 标注失败继续 | ✅ 健壮 |
| 引用溯源 | 标注哪个 Worker 产出 | ❌ 仅标注 worker 名称 | ❌ 无法溯源到具体工具/文档 |

### 2.4 SSE 流式设计

**单 Agent 流式**:
```
on_chat_model_stream → token 事件（逐 token 推送）
on_tool_start       → tool_start 事件
on_tool_end         → tool_end 事件
done                → done 事件（含 thread_id）
```

**Supervisor 流式**:
```
on_chain_start(worker_node) → worker_switch 事件（Worker 切换通知）
on_chat_model_stream        → token 事件（含 worker 标识）
on_tool_start/end           → tool 事件（含 worker 标识）
done                        → done 事件（含 mode: supervisor）
```

**问题**: 
- Supervisor 内部 LLM 调用（任务分解 + 结果综合）的 token 也被推送，用户会看到 Supervisor 的 JSON 输出
- 无中间进度事件（如"正在搜索..."、"正在分析..."）

### 2.5 当前优化亮点

1. **Supervisor V2 纯逻辑路由**: Worker 返回后不调 LLM 路由，省 3 次 LLM 调用 → 5x 速度提升
2. **Researcher 并行**: 同类任务 asyncio.gather 并行执行
3. **Supervisor 无工具约束**: 明确告知 Supervisor 无工具，防止编造数据
4. **Worker 独立 checkpointer**: 避免并行状态冲突
5. **单任务直接透传**: 不浪费 LLM 调用做简单综合
6. **with_fallbacks**: 主模型失败自动切换，增强鲁棒性

### 2.6 智能助手优化路线建议

#### P1 — 图编排与协作质量

1. **跨 Worker 并行**
   - 现状：仅 researcher 内部并行
   - 问题：researcher + manager 无依赖关系，应可并行
   - 方案：Supervisor 路由时识别无依赖的 worker 组，一次性派出并行执行
   - 实现：在 supervisor_node 中将所有 pending 的 researcher + manager 同时标记为 running

2. **SSE 事件过滤**
   - 问题：Supervisor 的 JSON 输出和内部 LLM token 被推送给前端
   - 方案：
     - supervisor 节点内部的 token 事件不推送给前端（仅 worker 的推送）
     - 或改为推送结构化的 `thinking` 事件（"正在分析您的请求..."）

3. **任务超时与重试**
   - 现状：无超时，单任务卡住会阻塞
   - 方案：每个 Worker 调用加 `asyncio.wait_for(timeout=60)`，超时自动标记 failed
   - 失败任务可选重试一次

4. **结果完整性**
   - 现状：`result[:500]` 截断可能丢失关键信息
   - 方案：存储完整 result，LLM 综合时用 `result[:1000]` 或传入引用 ID

#### P2 — 图编排增强

5. **动态角色扩展**
   - 现状：3 固定角色（researcher/analyst/manager）
   - 方案：Worker 注册表模式，新 Worker 可动态注册（如 coder、translator）
   - Supervisor prompt 动态生成（根据可用 Worker 列表）

6. **意图路由优化**
   - 现状：所有请求先调 LLM 分解
   - 方案：简单请求（如"列出专题"）可直接路由到 manager，跳过 LLM 分解
   - 规则引擎 + LLM fallback：关键词匹配快速路由，复杂请求才调 LLM

7. **任务依赖 DAG**
   - 现状：无依赖管理
   - 方案：任务支持 `depends_on` 字段，Supervisor 按 DAG 拓扑序调度
   - 例：搜索任务 → 分析任务（分析依赖搜索结果）

8. **上下文共享机制**
   - 现状：Worker 间无直接通信，仅通过 Supervisor 综合结果
   - 方案：共享 scratchpad / blackboard 模式
   - Worker A 的产出写入共享 state，Worker B 可读取

#### P3 — 高级协作

9. **Human-in-the-loop**
   - 破坏性操作前确认（如删除知识库、清空数据）
   - LangGraph interrupt 机制

10. **长对话上下文压缩**
    - 现状：MemorySaver 无限积累，长对话 token 膨胀
    - 方案：滑动窗口 + 自动摘要压缩
    - 最近 N 轮完整保留，更早的对话压缩为摘要

11. **Supervisor 自适应模式切换**
    - 自动判断任务复杂度 → 单 Agent / Supervisor 切换
    - 简单任务走单 Agent（快），复杂任务走 Supervisor（准）

---

## 三、Chat 服务三模式设计分析

**文件**: `backend/app/services/chat.py` (18KB)

### 3.1 Hybrid 模式流程

```
用户问题
  │
  ▼
意图分类（关键词匹配）
  │
  ├─ needs_fresh_info=True → 并行启动 RAG + Search
  ├─ needs_fresh_info=False → 仅 RAG
  │     │
  │     ▼ RAG 结果不足 → 补充 Search
  │
  ▼
LLM 综合（单次调用，结合 RAG context + Search context）
  │
  ▼
后台异步入库（fire-and-forget，不阻塞响应）
```

### 3.2 当前优化

| 优化点 | 说明 | 效果 |
|--------|------|------|
| RAG+Search 并行 | asyncio.gather | 34s→14s |
| retrieval_only | RAG 只检索不生成，hybrid 统一 LLM 生成 | 省一次 LLM 调用 |
| 后台入库 | asyncio.create_task | 不阻塞响应 |
| 意图分类 | 关键词匹配决定是否联网 | 减少无谓搜索 |

### 3.3 不足与优化方向

| 环节 | 不足 | 优化方向 |
|------|------|---------|
| 意图分类 | 关键词匹配，粗糙 | LLM 意图分类（更准） |
| 搜索触发 | 二元判断（搜/不搜） | 搜索置信度评分，动态决定 |
| 结果融合 | 拼接 RAG+Search 给 LLM | 分层融合：先 RAG，不足补充 Search |
| 多轮上下文 | 最近6条硬编码 | 动态 token 预算分配 |
| 知识库指定 | 需用户手动选 | 自动匹配最相关 KB |

---

## 四、系统整体优化路线总结

### Phase 1 — RAG 核心检索质量（预计 2-3 天）
1. ✅ 混合检索（Dense + BM25 + RRF）
2. ✅ 重排序（LLM Reranker 或 BGE Re-ranker）
3. ✅ 查询改写 Multi-Query

### Phase 2 — 智能助手图编排（预计 2-3 天）
4. ✅ 跨 Worker 并行调度
5. ✅ SSE 事件过滤与进度推送
6. ✅ 任务超时与重试机制
7. ✅ 简单请求快速路由（跳过 LLM 分解）

### Phase 3 — 中级优化（预计 2-3 天）
8. ✅ 自适应分块 + 结构化分块
9. ✅ 上下文压缩与 Lost-in-the-Middle
10. ✅ 长对话上下文自动压缩
11. ✅ 索引增量同步与状态追踪

### Phase 4 — 高级功能（持续）
12. 🔲 动态 Worker 注册表
13. 🔲 任务依赖 DAG
14. 🔲 Human-in-the-loop
15. 🔲 引用验证与事实核查
16. 🔲 Supervisor 自适应模式切换
