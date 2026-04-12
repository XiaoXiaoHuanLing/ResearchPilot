# TODO.md — 来财的工作待办

> 创建时间：2026-04-10 01:30
> 最后更新：2026-04-12 14:35
> 规则：每完成一项打勾并记录时间；阶段汇报时附截图

---

## 🔥 P0 — 核心体验（进行中）

- [x] RAG 引擎 V2 重构（语义分块 + 元数据完善 + 查询增强 + HyDE） — 04-10 01:00
- [x] Chat 服务 V2 重构（意图分类 + 增强提示词 + 统一消息构建） — 04-10 01:00
- [x] RAG 可观测接口（/api/rag/stats + /api/rag/reindex） — 04-10 01:00
- [x] 重建索引验证 — 04-10 01:15，11 chunks / 3 citations / score 0.71-0.73
- [x] 采集异步化 — 后台任务管理 + 进度追踪 — 04-10 01:45
- [x] APScheduler 定时采集修复 — asyncio.run() 解决事件循环问题，5个调度任务生效 — 04-10 01:45
- [x] 聊天历史持久化 — ChatSession/ChatMessage DB 模型 + /api/chat-sessions + qa chat 支持 session_id — 04-10 01:45
- [x] Copilot SSE 流式修复 — Vue 响应式 splice 重渲染 + Vite proxy 独立 SSE 代理 + 后端 chunk 兼容 — 04-11 14:00
- [x] QA hybrid 模式超时优化 — RAG+搜索并行 + retrieval_only 省LLM + ingest后台非阻塞 — 04-11 23:00
- [x] 代码清理 — 删除13个冗余文件、修复3个文件、.gitignore完善 — 04-11 14:30
- [x] Copilot 24工具端到端测试 — 24/24 通过 — 04-12 00:10
- [x] APScheduler 异步采集验证 — async_mode+task进度追踪 — 04-12 00:10
- [x] Copilot SSE 流式实测 — token级打字机+工具日志+qwen3.5-plus — 04-12 00:15
- [x] LLM 请求级 Fallback — 3层(flash→plus→proxy)+reset_agent — 04-12 00:15
- [ ] 前端 citations 适配新字段（published_at, topic）— 已有字段定义，待 RAG 填充真实值
- [x] 前端 QaView 适配 session_id + 聊天历史 — 04-12 已有

## 🤖 P1 — 智能助手模块（进行中）

> 核心理念：用户全程自然语言交互，Agent 自主完成所有后端操作
> 技术方向：LangGraph + deepagents 多智能体 harness 工程

- [x] Copilot 架构解耦重构 — tools/ + llm/ + agent/ — 04-11 02:30
- [x] 24 工具全部注册 + FUNC_MAP 模式 — 04-11 02:30
- [x] SSE 流式 + 非流式 API 均验证通过 — 04-11 02:30
- [x] 前端 CopilotView 对话界面 + localStorage 持久化 — 04-11 02:30
- [x] 20 个 bug 修复（详见 PROGRESS.md 第五节） — 04-12 00:15
- [x] QA hybrid 模式超时优化 — 04-11 23:00
- [x] LLM 3层 Fallback + 模型切换 — 04-12 00:15
- [x] with_fallbacks 重构 — 干掉手写try/except，LLM链自动切换 — 04-12 20:30
- [x] Supervisor SSE worker_switch 修复 — event.name节点识别+上下文追踪 — 04-12 20:30
- [x] CopilotChatResponse schema 修复 — mode/tasks字段不再被Pydantic丢弃 — 04-12 20:30
- [ ] 调研 deepagents 框架最新设计和用法
- [x] 调研 LangGraph 多 Agent 协作最佳实践 — 04-12 设计完成
- [x] 设计 Agent 架构（角色划分、协作协议、记忆管理）— 04-12 MULTI_AGENT_DESIGN.md
- [ ] 设计虚拟文件系统（Agent 工作空间）
- [ ] 设计上下文记忆管理策略
- [x] 前端新页面 — Agent 对话界面 — CopilotView 已有
- [x] 后端 Agent 服务实现 — supervisor.py + workers.py + SSE
- [ ] 集成测试

## 📋 P2 — 功能完善

- [x] 代码提交 & 变更归档 — 04-12 已分5批提交 + 04-12 20:30 with_fallbacks重构
- [ ] PDF 导出
- [ ] 用户认证系统
- [ ] 知识库文档预览
- [ ] 资讯去重优化（URL + title 相似度）
- [ ] 多轮对话上下文（QA/Chat 携带历史消息给 LLM）
- [ ] 前端全局错误处理

## 🔧 P3 — 增强优化

- [x] SSE 流式输出 — 04-10/11 Copilot SSE + QA SSE 均已实现
- [ ] 采集反爬 403 长期方案
- [x] Copilot 子 Agent 协作（并行采集+分析）— 04-12 researcher 并行已实现
- [ ] Copilot 上下文压缩（长对话自动摘要）
- [ ] Copilot Human-in-the-loop（确认破坏性操作）
- [ ] PDF/DOCX 文档解析
- [ ] PostgreSQL 升级
- [ ] Docker 部署
- [ ] 前端移动端适配

---

## 📝 工作日志

### 2026-04-10
- 00:50 开始 RAG 全流程优化
- 01:00 完成 engine.py V2 + chat.py V2 + health.py 新端点
- 01:00 更新 articles.py / knowledge_base.py / ingestion.py 传入 rich metadata
- 01:15 重建索引验证通过，RAG 问答可用
- 01:30 收到智能助手模块需求，创建 TODO + 设计文档 AGENT_MODULE_DESIGN.md
- 01:40 调研 deepagents + LangGraph 多 Agent 最佳实践
- 01:45 完成 tasks.py 后台任务管理 + scheduler.py V2 修复 + chat_sessions 聊天持久化
- 01:50 全部后端 API 验证通过，5 个定时调度任务生效，聊天会话持久化可用
- **阶段汇报 1**：P0 核心后端改造全部完成

### 2026-04-11
- 02:30 Copilot 架构重构完成：单文件 1100 行 → tools/(8 文件) + llm/ + agent/
- 02:30 24 工具全部注册，FUNC_MAP 模式验证
- 02:30 工具调用链验证：list_topics/get_system_status 成功
- 02:30 非流式+流式 API 均正常
- 02:30 前端修复：切换模式不新建对话、滚动条美化、Copilot localStorage 持久化
- 02:36 后端持续运行，Copilot `/chat/stream` 正常
- 08:59 飞书 Tailscale Switch 连接认证失败（非项目问题）
- 12:02 comi 检查前后端，发现端口 8000 双进程冲突
- 12:05 清理旧进程 → 后端掉线 → 重启恢复
- 13:05 更新 PROGRESS.md + TODO.md
- 13:25 comi 反馈 Copilot SSE 前端问题（不打字机 + 工具调用后不渲染）
- 13:30 诊断根因：Vue 响应式不触发 + Vite proxy 缓冲 + chunk.content 格式兼容
- 13:35 修复三文件：CopilotView.vue（splice 重渲染+SSE解析增强）、copilot.py（chunk兼容+换行折叠）、vite.config.ts（SSE独立代理）
- 14:00 前后端重启测试，关闭所有旧进程
- 14:19 代码清理：删除13个冗余文件、修复3个文件
- 14:30 前端 build 验证通过
- 17:30 comi 要求梳理项目进度
- 19:40 comi 要求按顺序优化 P0 项
- 20:22 开始 QA hybrid 模式超时优化
- 20:25 RAG+搜索并行、retrieval_only、ingest后台化
- 22:44 测试：Hybrid(RAG够用) 34.6s→14.2s ✅
- 22:46 测试：Hybrid(需搜索) 33.4s（qwen API延迟40s属外部因素）
- 23:00 更新文档、创建交接清单
- **阶段汇报 3**：P0-1 QA hybrid 优化完成，代码提速2.4x

### 2026-04-12
- 00:10 Copilot 24 工具端到端测试 24/24 通过（修3 bug: update_topic session脱离、upload_document描述、generate_report递归）
- 00:10 APScheduler 异步采集验证通过（async_mode+task进度）
- 00:15 SSE 流式实测通过（qwen3.5-plus，token级打字机+工具日志）
- 00:15 LLM 3层 Fallback 实现（flash→plus→proxy + reset_agent + 友好提示）
- 00:15 主模型切换 qwen3.5-flash → qwen3.5-plus（flash免费额度耗尽）
- 00:20 更新 PROGRESS.md + TODO.md
- **P0 全部完成（除代码提交），准备进入多智能体协作设计**

- 09:50 代码提交归档：70个未提交文件分5批commit（Copilot模块/后端改进/前端更新/文档清理/gitignore）
- 09:55 Supervisor 多 Agent SSE 流式支持：copilot.py 拆分为 _single_agent_stream + _supervisor_stream
- 09:55 Researcher 并行执行：asyncio.gather 同时处理多个采集任务
- 09:55 前端 Supervisor 模式切换：CopilotView 添加 useSupervisor 开关 + worker_switch SSE 事件
- 10:00 Supervisor 任务解析改进：prompt 改为 JSON 格式输出，_parse_tasks 支持 JSON + markdown 双解析
- 10:10 修复 workers.py：Researcher 工具补全（search_tools 已含 ingest_url + collect_topic）
- 14:25 Supervisor 集成测试失败：qwen3.5-plus 免费额度耗尽(AllocationQuota)，非代码问题
- 14:30 qwen3.5-27b fallback 也有 UnicodeEncodeError 编码问题
- 14:35 当前所有阿里云模型额度耗尽，Supervisor 集成测试无法继续，需等额度刷新或切换到 proxy
- **阶段汇报 4**：多 Agent 架构代码完成，待 LLM 额度恢复后集成测试
