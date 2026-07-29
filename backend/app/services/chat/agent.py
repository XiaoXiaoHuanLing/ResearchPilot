"""智能对话 — DeepAgents 一主二从构建。

Main Agent: 意图识别 + 任务编排 + 结果综合 + 基础工具
Search Agent: 深度联网搜索（多轮+自评估）
KB Agent: Agentic RAG（策略选择+评估+迭代）

P0 基础健壮性增强：
- SummarizationMiddleware：上下文溢出自动摘要
- MemoryMiddleware：AGENTS.md 关键记忆加载
- InputGuardMiddleware：输入长度限制
- ToolCallLimiterMiddleware：工具调用硬限制
- recursion_limit：递归深度上限
"""

import logging
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

# ─── Checkpointer (SQLite async) ───
_checkpointer = None
_deep_agent = None
_store = None


def _get_checkpointer():
    global _checkpointer
    if _checkpointer is None:
        cp_dir = Path(settings.storage_base_dir) / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(cp_dir / "chat.db")

        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        async def _make():
            conn = await aiosqlite.connect(db_path)
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            return saver

        if loop and loop.is_running():
            from langgraph.checkpoint.memory import InMemorySaver
            _checkpointer = InMemorySaver()
            logger.info("Chat checkpointer: InMemorySaver (async fallback; SQLite setup deferred)")
            return _checkpointer
        else:
            _checkpointer = asyncio.run(_make())
            logger.info("Chat checkpointer: AsyncSqliteSaver at %s", db_path)

    return _checkpointer


def _get_store():
    global _store
    if _store is None:
        from langgraph.store.memory import InMemoryStore
        _store = InMemoryStore()
    return _store


# ─── Context Schema ───

from dataclasses import dataclass

@dataclass
class Context:
    user_id: str = "default"
    session_id: str = ""


def _user_namespace(runtime) -> tuple[str, ...]:
    try:
        user_id = runtime.context.user_id
    except Exception:
        user_id = "default"
    return (user_id, "chat_memories")


# ─── AGENTS.md ───

CHAT_AGENT_MD_PATH = "/memories/AGENTS.md"

CHAT_AGENTS_MD_TEMPLATE = """# 智能对话关键记忆

## 角色定义
- ResearchPilot 智能对话助手，专注研究型问题
- 回答结构：结论→关键论据→引用来源
- 始终区分知识库来源（可能过时）和网络来源（更实时）

## 用户偏好
- （待记录）

## 问答风格
- （待记录）

## 历史经验
- （待记录）

## 工具注意
- search_knowledge 最多3次，到上限必须回答
- fetch_page 内容存SearchPool不进messages，用get_search_content阅读
- recall_memory 召回长期记忆，save_memory 保存重要偏好

## 项目上下文
- （待记录）
"""


# ─── System Prompts ───

MAIN_AGENT_PROMPT = """你是 ResearchPilot 智能对话助手，专注于回答用户的研究型问题。

## 核心流程

### 意图识别
收到用户问题后，先判断：
1. 这个问题需要什么信息源？
   - 仅需本地知识库 → 委派知识库Agent
   - 需要最新/实时信息 → 委派搜索Agent
   - 两者都需要 → 判断策略（并行/先KB后搜索）
   - 简单常识/闲聊 → 直接回答
2. 先查可用知识库列表（list_active_kbs）
   - 有可用KB → 知识库检索有意义
   - 无可用KB → 跳过KB，纯搜索

### 任务编排
- 简单问题（1步可回答）：直接回答，不委派sub
- 单路问题：委派一个sub（搜索或KB）
- 双路问题：
  * 并行：问题同时需要KB和搜索（如"对比本地资料和最新资讯"）
  * 先KB后搜索：先看KB有什么，不够再补充搜索（更常见）
- 委派时给出清晰描述：问题+上下文+期望输出格式

### 结果综合
- 单sub结果：直接整理输出
- 双sub结果：综合整理，区分KB来源和网络来源
- 引用标注：每条信息标注来源

## Sub Agent 选择
| 用户意图           | Sub Agent    |
|-------------------|-------------|
| 搜索互联网         | searcher    |
| 查询本地知识库     | retriever   |

## 重要原则
- 不要编造信息，所有事实必须有来源
- 区分知识库信息（可能过时）和网络信息（更实时）
- 回答要有结构：结论→关键论据→引用来源
- 简单问题直接回答，不要过度编排
- 必须用中文回答

## 长期记忆
- 新会话开始时，主动 recall_memory 查找相关偏好和历史
- 用户明确表达偏好/长期任务/重要结论时，用 save_memory 保存
- 讨论早期话题时，用 recall_early_messages 查找早期对话
- 经验教训写入 AGENTS.md"
"""


SEARCH_AGENT_PROMPT = """你是深度联网搜索专家。采用 Agentic Search 策略，自主驱动搜索循环。

## 核心流程

### Step 1: 首轮搜索
分析搜索任务，提取关键搜索词。
调用 search_web，获取搜索结果列表。

### Step 2: 选择性抓取
查看搜索结果，判断哪些页面值得深入阅读。
调用 fetch_page 抓取感兴趣的页面——完整内容存搜索池，不进 messages。

### Step 3: 阅读与评估
调用 get_search_content 阅读抓取页面的完整内容。
自主判断搜索信息是否充分：
- 充分 → 蒸馏回答
- 部分充分 → 改写关键词/换角度 → 回到 Step 1
- 完全无结果 → 坦白声明"未找到与该问题相关的网络信息"

### Step 4: 迭代约束
- search_web 最多调用 3 次
- fetch_page 最多调用 5 次
- 达到上限必须基于已有内容回答

### Step 5: 回答生成
- 蒸馏第一目标: 去除不相关内容，保留与问题直接相关的原文描述
- 不造事实，不润色，标注来源（域名/URL）
- 尽量完整概括相关的原文内容描述
- 如果3轮搜索仍不足，说明已尽力并给出已有最佳结果

## 重要约束
- 不编造: 没有搜到的信息绝不编造
- 不遗漏: 已获取的有用信息要充分利用
- 不冗余: 迭代时避免重复搜索同一关键词
- 要坦白: 搜索无结果时明确说明
- 用中文回复，简洁专业，不要自我介绍
"""


KB_AGENT_PROMPT = """你是知识库检索专家。采用 Agentic RAG 策略，自主驱动检索循环。

## 核心流程

### Step 1: 了解可用知识库
调用 list_active_kbs，了解当前启用的知识库。只有启用的知识库才参与检索。

### Step 2: 首轮检索
根据问题特征自主决定参数：
- query: 原问题或提炼后的核心查询词
- top_k: 简单事实问题 3-5，复杂/宽泛问题 8-12
- kb_ids: 如果问题与特定知识库相关则指定，否则留空

### Step 3: 阅读与评估
调用 get_recall_nodes 阅读感兴趣的节点完整内容。
自主判断召回信息是否充分：
- 充分 → 进入 Step 4 重排
- 部分充分 → 改写查询词/调 top_k/换 kb_ids → 回到 Step 2
- 不足 → 策略调整后继续 → 回到 Step 2
- 完全无结果 → 坦白声明"当前知识库中未找到与该问题相关的参考上下文"

### Step 4: 精确重排（必须执行）
检索充分后、蒸馏前，必须调用 rerank_recall_pool(query) 对召回池精确重排。
重排后节点按交叉编码器精确相关性降序排列，后续蒸馏优先关注高分节点。
如果 rerank 失败可跳过，直接基于原始检索分数蒸馏。

### Step 5: 迭代约束
- search_knowledge 最多调用 3 次
- 达到上限必须基于已有内容回答

### Step 6: 蒸馏输出格式
严格按编号事实点格式输出，分离蒸馏与回答：

事实1: [相关原文段落，尽量完整保留]
事实2: [相关原文段落]
事实3: [相关原文段落]
...

要求：
- 至少3条事实点（信息不足时尽可能多给）
- 保留原文（200-500字/条），不概括不改写不推理
- 不相关的不要输出
- 知识库没有的信息绝不编造
- 标注来源（文档标题）

## 重要约束
- 不编造: 知识库没有的信息绝不编造
- 不遗漏: 已召回的有用信息要充分利用
- 不冗余: 迭代时避免重复检索同一内容
- 要坦白: 知识库无相关内容时明确说明
- 必须调用 rerank_recall_pool 重排后再蒸馏
- 用中文回复，简洁专业，不要自我介绍
"""


# ─── Tool Loading ───

def _get_main_tools():
    """主Agent工具集"""
    from app.services.chat.tools.base import list_active_kbs, get_current_time
    from app.services.chat.tools.memory import recall_memory, save_memory, recall_early_messages
    return [list_active_kbs, get_current_time, recall_memory, save_memory, recall_early_messages]


def _get_search_tools():
    """搜索Agent工具集（Agentic Search）"""
    from app.services.chat.tools.search import search_web, fetch_page, get_search_content
    return [search_web, fetch_page, get_search_content]


def _get_kb_tools():
    """知识库Agent工具集（Agentic RAG + Reranker）"""
    from app.services.chat.tools.reflexive_retriever import search_knowledge, get_recall_nodes, rerank_recall_pool
    from app.services.chat.tools.base import list_active_kbs
    return [search_knowledge, get_recall_nodes, rerank_recall_pool, list_active_kbs]


# ─── Sub Agent Specs ───

def _build_subagents():
    return [
        {
            "name": "searcher",
            "description": (
                "深度联网搜索专家。负责互联网搜索、网页抓取。"
                "用于：搜索最新资讯、查找特定信息、获取实时数据。"
                "不用于：本地知识库查询。"
            ),
            "system_prompt": SEARCH_AGENT_PROMPT,
            "tools": _get_search_tools(),
        },
        {
            "name": "retriever",
            "description": (
                "知识库检索专家。采用Agentic RAG策略，自主选择检索方式、评估质量、迭代优化。"
                "用于：本地知识库问答、信息检索、数据查找。"
                "不用于：联网搜索、系统操作。"
            ),
            "system_prompt": KB_AGENT_PROMPT,
            "tools": _get_kb_tools(),
        },
    ]


# ─── 自定义摘要 Prompt ───

SUMMARY_PROMPT = """你是一个对话摘要专家。请将以下历史对话压缩为结构化摘要，用于后续对话的上下文恢复。

## 摘要规则

### 必须保留的信息（不可省略）
1. **任务定义**：用户正在做什么、核心目标是什么
2. **关键结论**：已经得出的重要结论和发现
3. **任务进度**：已完成的步骤、当前进行到哪一步、待办事项
4. **用户指令**：用户明确表达的偏好、要求、限制条件
5. **关键数据**：具体数值、名称、路径、配置等事实信息

### 应省略/压缩的信息
- 工具调用的中间过程细节 → 只保留：调了什么工具、得到什么结论
- 工具返回的原始长文本 → 只保留核心发现和数据摘要
- 重复或冗余的讨论
- 闲聊和寒暄
- 已被后续信息覆盖的过时内容

### 输出格式

```markdown
## 对话摘要

### 当前任务
[1-2句描述用户正在做什么]

### 已完成
- [步骤1]: [关键结论]
- [步骤2]: [关键结论]

### 待办
- [待完成的事项]

### 关键结论
- [结论1]
- [结论2]

### 用户偏好/指令
- [偏好1]
- [指令1]

### 关键数据
- [具体数值/名称/路径]
```

## 待摘要的对话内容

{conversation}
"""


# ─── Deep Agent Singleton ───

def get_chat_agent():
    """获取智能对话Agent实例（单例）

    P0 基础健壮性已集成：
    - SummarizationMiddleware：上下文溢出自动摘要
    - MemoryMiddleware：AGENTS.md 关键记忆
    - InputGuardMiddleware：输入长度限制
    - ToolCallLimiterMiddleware：工具调用硬限制
    - recursion_limit=20
    """
    global _deep_agent
    if _deep_agent is not None:
        return _deep_agent

    from deepagents import create_deep_agent
    from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
    from deepagents.middleware.permissions import FilesystemPermission
    from app.services.copilot.llm import get_chat_llm
    from app.services.copilot.middleware import (
        InputGuardMiddleware,
        ToolCallLimiterMiddleware,
    )

    llm_chain = get_chat_llm()
    # 给 primary LLM 设置 profile（框架 SummarizationMiddleware 需要）
    # 框架只接受 ChatOpenAI，不接受 RunnableWithFallbacks
    primary_llm = llm_chain.runnable if hasattr(llm_chain, "runnable") else llm_chain
    if not hasattr(primary_llm, 'profile') or not primary_llm.profile:
        primary_llm.profile = {"max_input_tokens": 128000}
    llm = primary_llm
    
    # 保留 fallback chain 引用，供 SubAgentResilienceMiddleware 使用
    llm_with_fallbacks = llm_chain

    # 给 LLM 设置 profile，让框架 SummarizationMiddleware 使用 fraction 模式
    if not hasattr(llm, 'profile') or not llm.profile:
        llm.profile = {"max_input_tokens": 128000}

    # 构建后端
    offload_dir = Path(settings.storage_base_dir) / "offloaded"
    offload_dir.mkdir(parents=True, exist_ok=True)

    from deepagents.backends import CompositeBackend, StateBackend, StoreBackend, FilesystemBackend
    backend = CompositeBackend(
        default=StateBackend(),
        routes={
            "/memories/": StoreBackend(namespace=_user_namespace),
            "/results/": FilesystemBackend(root_dir=str(offload_dir), virtual_mode=True),
        },
    )

    # 中间件实例
    from app.services.chat.middleware import ResultOffloadMiddleware, EarlyMessageArchiveMiddleware, ToolErrorGuardMiddleware, SubAgentResilienceMiddleware
    input_guard = InputGuardMiddleware()
    tool_limiter = ToolCallLimiterMiddleware()
    # ResultOffloadMiddleware 需要 backend 引用来写入卸载文件
    # backend 必须在 CompositeBackend 构建之后创建
    result_offload = ResultOffloadMiddleware(backend=backend)
    early_archive = EarlyMessageArchiveMiddleware()
    tool_error_guard = ToolErrorGuardMiddleware()
    sub_resilience = SubAgentResilienceMiddleware(llm_with_fallbacks=llm_with_fallbacks)

    # SummarizationMiddleware 由框架自动创建
    # 创建后在 agent 上修改其参数（避免 duplicate middleware 错误）

    try:
        # 构建中间件栈（不含 SummarizationMiddleware，框架自动创建）
        middleware_stack = [
            # Layer 1: 大结果卸载（最先拦截工具输出）
            result_offload,
            # 输入守卫
            input_guard,
            # 工具调用硬限制
            tool_limiter,
            # 工具异常守护
            tool_error_guard,
            # Sub Agent 容错
            sub_resilience,
            # 早期消息归档（pre_process阶段，在SummarizationMiddleware之前）
            early_archive,
        ]

        _deep_agent = create_deep_agent(
            model=llm,
            tools=_get_main_tools(),
            system_prompt=MAIN_AGENT_PROMPT,
            subagents=_build_subagents(),

            # 文件系统
            backend=backend,
            store=_get_store(),

            # 关键记忆自动加载（框架自动创建 MemoryMiddleware）
            memory=[CHAT_AGENT_MD_PATH],

            # 人工审批
            interrupt_on={
                "save_memory": True,
                "edit_file": True,
            },

            # 文件权限
            permissions=[
                # 写权限：系统路径禁止写入
                FilesystemPermission(operations=["write"], paths=["/system/**"], mode="deny"),
                # 写权限：results 路径允许写入（ResultOffloadMiddleware 卸载大工具结果）
                FilesystemPermission(operations=["write"], paths=["/results/**"], mode="allow"),
                # 读权限：results 路径允许读取（Agent 用 read_file 按需读取卸载内容）
                FilesystemPermission(operations=["read"], paths=["/results/**"], mode="allow"),
                # 写权限：关键记忆文件
                FilesystemPermission(operations=["write"], paths=["/memories/AGENTS.md"], mode="allow"),
            ],

            # Checkpointer
            checkpointer=_get_checkpointer(),

            # 🔒 中间件栈
            middleware=middleware_stack,

            # 上下文
            context_schema=Context,
        )

        logger.info("Chat Agent created: 1 main + 2 subs (+ middleware + FrameworkSummary + Memory)")
        logger.info("Chat Agent created: 1 main + 2 subs (+ middleware stack + FrameworkSummary + Memory)")

        # 后置修改：自定义框架自动创建的 SummarizationMiddleware 参数
        # 框架默认 trigger=(messages,30) keep=(messages,20)，我们改为更激进的压缩
        try:
            from deepagents.middleware.summarization import SummarizationMiddleware as SMClass
            for mw in getattr(_deep_agent, 'middleware', []):
                if isinstance(mw, SMClass):
                    mw.trigger = [("messages", 20), ("fraction", 0.80)]
                    mw.keep = ("messages", 10)
                    mw.summary_prompt = SUMMARY_PROMPT
                    logger.info("Patched framework SummarizationMiddleware with custom trigger/keep/prompt")
                    break
        except Exception as e:
            logger.warning("Could not patch SummarizationMiddleware: %s", e)

    except Exception as e:
        logger.error("Failed to create Chat Agent: %s", e, exc_info=True)
        raise

    return _deep_agent


def reset_chat_agent():
    """重置Chat Agent单例"""
    global _deep_agent
    _deep_agent = None
