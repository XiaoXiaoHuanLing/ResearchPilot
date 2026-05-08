"""智能助手 — DeepAgents 一主六从构建。

Main Agent: 任务编排 + 长期记忆 + 敏感审批
6 Sub Agents: searcher / collector / retriever / kb_manager / topic_manager / report_manager
"""

import logging
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.middleware.permissions import FilesystemPermission

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
        db_path = str(cp_dir / "copilot.db")

        import aiosqlite
        import asyncio
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

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
            logger.info("Copilot checkpointer: InMemorySaver (async fallback; SQLite setup deferred)")
            return _checkpointer
        else:
            _checkpointer = asyncio.run(_make())
            logger.info("Copilot checkpointer: AsyncSqliteSaver at %s", db_path)

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
    return (user_id, "memories")


# ─── System Prompts ───

MAIN_AGENT_PROMPT = """你是 ResearchPilot 全能研究助手，能够执行搜索、采集、知识库管理、报告生成等所有操作。

## 核心流程

### 意图识别
- 理解用户要做什么：搜索？采集？管理？查询？生成报告？
- 判断复杂度：简单(1步) / 中等(2-3步) / 复杂(4+步)
- 简单问题直接回答，不编排

### 任务编排
- 根据意图选择合适的Sub Agent
- 独立任务可并行（如同时搜索+查KB）
- 有依赖的任务串行（如先搜后采）
- 每个Sub Agent只做自己的事，不要跨域

### Sub Agent 选择指南
| 用户意图              | Sub Agent       |
|----------------------|-----------------|
| 搜索互联网            | searcher        |
| 采集URL/专题资讯      | collector       |
| 查询本地知识库        | retriever       |
| 管理/上传知识库       | kb_manager      |
| 管理专题CRUD          | topic_manager   |
| 生成/管理报告         | report_manager  |

### 结果综合
- 单sub结果：整理后直接输出
- 多sub结果：综合整理，标注来源
- 失败结果：说明失败原因，建议替代方案

### 长期记忆
- 新会话开始时，主动recall相关记忆
- 用户明确表达偏好/长期任务时，保存到长期记忆
- 经验教训写入AGENTS.md

### 敏感操作
- 删除操作、报告生成、批量采集需要用户确认
- 系统目录禁止写入

## 重要原则
- 简单问题直接回答
- 复杂任务先规划再执行
- 失败不慌，分析原因换策略
- 必须用中文回答
"""


SEARCHER_PROMPT = """联网搜索专家。采用 Agentic Search 策略，自主驱动搜索循环。
搜索结果存搜索池（SearchPool），按 URL 去重。
用 search_web 搜索、fetch_page 抓取、get_search_content 阅读完整内容。
最多3轮搜索+5次抓取，每轮评估是否充分。用中文回复，简洁专业。不要自我介绍。"""


COLLECTOR_PROMPT = """资讯采集专家。负责URL采集、专题批量采集、咨询管理。
用于：采集URL内容入库、批量采集专题资讯、查看/收藏/删除资讯。
不用于：搜索（搜到URL后交给本agent采集）、知识库操作。
收藏=永久保存+感兴趣标记，不会入知识库。用中文回复，简洁专业。不要自我介绍。"""


RETRIEVER_PROMPT = """知识库检索专家。采用Agentic RAG策略，自主驱动检索循环。
用 search_knowledge 检索（结果存RecallPool）、get_recall_nodes 阅读节点完整内容、list_active_kbs 了解可用知识库。
检索后自主判断充分性，不充分则改写query/调top_k，最多3次迭代。用中文回复，简洁专业。不要自我介绍。"""


KB_MANAGER_PROMPT = """知识库管理专家。负责知识库的创建、删除、启用禁用、文档上传入库。
用于：创建/删除知识库、上传文档到知识库、切换知识库启用状态、查看索引状态。
不用于：知识库内容检索（由retriever负责）。
上传文档走异步入库，状态会稍后更新。用中文回复，简洁专业。不要自我介绍。"""


TOPIC_MANAGER_PROMPT = """专题管理专家。负责专题的创建、删除、关键词管理、定时采集调度。
用于：创建/删除/修改专题、管理专题关键词、查看调度状态。
不用于：搜索采集（由collector负责）、知识库操作。
用中文回复，简洁专业。不要自我介绍。"""


REPORT_MANAGER_PROMPT = """报告管理专家。负责报告的生成、查看、入库、导出。
用于：生成研究报告、查看报告列表、将报告入知识库、导出报告。
生成流程：提取关键信息→生成大纲→用户确认→逐节生成→质量校验→可选入KB。
不用于：知识库检索、搜索。
用中文回复，简洁专业。不要自我介绍。"""


# ─── Tool Loading ───

def _get_searcher_tools():
    # 复用智能对话模块的 Agentic Search 3工具
    from app.services.chat.tools.search import search_web, fetch_page, get_search_content
    return [search_web, fetch_page, get_search_content]

def _get_collector_tools():
    from app.services.copilot.tools.article import TOOLS as art_tools
    from app.services.copilot.tools.search import TOOLS as search_tools
    # collector需要: ingest_url, collect_topic, list/bookmark/delete_article
    collector_names = {"ingest_url", "collect_topic", "list_articles", "bookmark_article", "delete_article"}
    tools = [t for t in (art_tools + search_tools) if t.name in collector_names]
    return tools

def _get_retriever_tools():
    # 新版 Agentic RAG 3工具（替代旧版5工具）
    from app.services.chat.tools.reflexive_retriever import search_knowledge, get_recall_nodes
    from app.services.chat.tools.base import list_active_kbs
    return [search_knowledge, get_recall_nodes, list_active_kbs]

def _get_kb_manager_tools():
    from app.services.copilot.tools.knowledge_base import TOOLS as kb_tools
    from app.services.copilot.tools.rag import TOOLS as rag_tools
    # 增加 toggle_kb_enabled
    extra = []
    try:
        from app.services.knowledge.manager import toggle_kb_enabled
        from langchain_core.tools import tool as lc_tool
        @lc_tool
        def toggle_kb_enabled_tool(kb_id: int) -> str:
            """切换知识库启用/禁用状态。"""
            result = toggle_kb_enabled(kb_id)
            return result.get("message", str(result))
        extra.append(toggle_kb_enabled_tool)
    except Exception:
        pass
    return kb_tools + rag_tools + extra

def _get_topic_manager_tools():
    from app.services.copilot.tools.topic import TOOLS
    return TOOLS

def _get_report_manager_tools():
    from app.services.copilot.tools.report import TOOLS
    # 增加 index_report_to_kb 和 export_report
    extra = _build_report_extra_tools()
    return TOOLS + extra

def _build_report_extra_tools():
    """构建报告管理额外工具"""
    from langchain_core.tools import tool as lc_tool

    @lc_tool
    async def index_report_to_kb(report_id: int, kb_id: int) -> str:
        """将已生成的报告入库到指定知识库。"""
        from app.db.session import SessionLocal
        from app.db.models import ReportModel, KnowledgeBaseModel
        from app.services.knowledge.indexer import index_md_file

        with SessionLocal() as db:
            report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
            if not report:
                return f"❌ 报告 ID={report_id} 不存在"
            if report.status != "ready":
                return f"❌ 报告状态为{report.status}，需要先完成生成"
            if not report.file_path:
                return "❌ 报告文件不存在"

            kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
            if not kb:
                return f"❌ 知识库 ID={kb_id} 不存在"

            chunks = await index_md_file(report.file_path, kb_id, report.title)
            report.indexed = True
            report.kb_id = kb_id
            db.commit()

            return f"✅ 报告已入库到「{kb.name}」({chunks} chunks)"

    @lc_tool
    def export_report(report_id: int, format: str = "markdown") -> str:
        """获取报告导出URL。"""
        base = ""
        if format == "pdf":
            return f"{base}/api/reports/{report_id}/export/pdf"
        return f"{base}/api/reports/{report_id}/export/markdown"

    return [index_report_to_kb, export_report]


def _get_main_tools():
    """主Agent工具：记忆 + 基础"""
    from app.services.copilot.memory import recall_memory, save_memory
    from app.services.chat.tools.base import list_active_kbs, get_current_time
    return [recall_memory, save_memory, list_active_kbs, get_current_time]


# ─── Sub Agent Specs ───

def _build_subagents():
    return [
        {
            "name": "searcher",
            "description": (
                "联网搜索专家。负责互联网搜索、网页抓取。"
                "用于：搜索最新资讯、查找特定信息、获取实时数据。"
                "不用于：本地知识库查询、系统操作。"
            ),
            "system_prompt": SEARCHER_PROMPT,
            "tools": _get_searcher_tools(),
        },
        {
            "name": "collector",
            "description": (
                "资讯采集专家。负责URL采集、专题批量采集、咨询管理。"
                "用于：采集URL内容入库、批量采集专题资讯、查看/收藏/删除资讯。"
                "不用于：搜索、知识库操作。"
            ),
            "system_prompt": COLLECTOR_PROMPT,
            "tools": _get_collector_tools(),
        },
        {
            "name": "retriever",
            "description": (
                "知识库检索专家。采用Agentic RAG策略，自主选择检索方式、评估质量、迭代优化。"
                "用于：本地知识库问答、信息检索、数据查找。"
                "不用于：知识库管理(CRUD)、系统操作。"
            ),
            "system_prompt": RETRIEVER_PROMPT,
            "tools": _get_retriever_tools(),
        },
        {
            "name": "kb_manager",
            "description": (
                "知识库管理专家。负责知识库的创建、删除、启用禁用、文档上传入库。"
                "用于：创建/删除知识库、上传文档到知识库、切换知识库启用状态。"
                "不用于：知识库内容检索（由retriever负责）。"
            ),
            "system_prompt": KB_MANAGER_PROMPT,
            "tools": _get_kb_manager_tools(),
        },
        {
            "name": "topic_manager",
            "description": (
                "专题管理专家。负责专题的创建、删除、关键词管理、定时采集调度。"
                "用于：创建/删除/修改专题、管理专题关键词。"
                "不用于：搜索采集、知识库操作。"
            ),
            "system_prompt": TOPIC_MANAGER_PROMPT,
            "tools": _get_topic_manager_tools(),
        },
        {
            "name": "report_manager",
            "description": (
                "报告管理专家。负责报告的生成、查看、入库、导出。"
                "用于：生成研究报告、将报告入知识库、导出报告。"
                "不用于：知识库检索、搜索。"
            ),
            "system_prompt": REPORT_MANAGER_PROMPT,
            "tools": _get_report_manager_tools(),
        },
    ]


# ─── AGENTS.md ───

AGENT_MD_PATH = "/memories/AGENTS.md"

AGENTS_MD_TEMPLATE = """# 关键记忆

## 用户偏好
- （待记录）

## 长期任务
- （待记录）

## 经验教训
- （待记录）
"""

MAX_AGENTS_MD_LENGTH = 2000


# ─── Deep Agent Singleton ───

def get_deep_agent():
    """获取智能助手Agent实例（单例）"""
    global _deep_agent
    if _deep_agent is not None:
        return _deep_agent

    from app.services.copilot.llm import get_chat_llm
    from app.services.copilot.middleware import (
        SubAgentResilienceMiddleware,
        InputGuardMiddleware,
        ToolCallLimiterMiddleware,
        SessionArchiveMiddleware,  # 兼容旧 import
    )

    llm_chain = get_chat_llm()
    # 框架只接受 ChatOpenAI，不接受 RunnableWithFallbacks
    primary_llm = llm_chain.runnable if hasattr(llm_chain, "runnable") else llm_chain
    if not hasattr(primary_llm, 'profile') or not primary_llm.profile:
        primary_llm.profile = {"max_input_tokens": 128000}
    llm = primary_llm
    llm_with_fallbacks = llm_chain

    # 给 LLM 设置 profile，让框架自动创建的 SummarizationMiddleware 使用 fraction 模式
    # qwen3.5-35b-a3b 上下文窗口约 128K tokens
    if not hasattr(llm, 'profile') or not llm.profile:
        llm.profile = {"max_input_tokens": 128000}

    # 中间件实例（只加自定义的，框架自动创建 SummarizationMiddleware / MemoryMiddleware / TodoListMiddleware 等）
    input_guard = InputGuardMiddleware()
    tool_limiter = ToolCallLimiterMiddleware()
    resilience = SubAgentResilienceMiddleware()

    try:
        # 构建后端（/results/ 走 FilesystemBackend，支持图外写入卸载文件）
        from deepagents.backends import FilesystemBackend
        offload_dir = Path(settings.storage_base_dir) / "offloaded_copilot"
        offload_dir.mkdir(parents=True, exist_ok=True)

        backend = CompositeBackend(
            default=StateBackend(),
            routes={
                "/memories/": StoreBackend(namespace=_user_namespace),
                "/results/": FilesystemBackend(root_dir=str(offload_dir), virtual_mode=True),
            },
        )

        _deep_agent = create_deep_agent(
            model=llm,
            tools=_get_main_tools(),
            system_prompt=MAIN_AGENT_PROMPT,
            subagents=_build_subagents(),

            # 文件系统
            backend=backend,
            store=_get_store(),

            # 关键记忆自动加载（框架自动创建 MemoryMiddleware）
            memory=[AGENT_MD_PATH],

            # 人工审批
            interrupt_on={
                "generate_report": True,
                "delete_topic": True,
                "delete_knowledge_base": True,
                "delete_article": True,
                "collect_topic": True,
                "save_memory": True,
            },

            # 文件权限
            permissions=[
                FilesystemPermission(operations=["write"], paths=["/system/**"], mode="deny"),
                FilesystemPermission(operations=["write"], paths=["/results/**"], mode="allow"),
                FilesystemPermission(operations=["read"], paths=["/results/**"], mode="allow"),
                FilesystemPermission(operations=["write"], paths=["/memories/AGENTS.md"], mode="allow"),
            ],

            # Checkpointer
            checkpointer=_get_checkpointer(),

            # 🔒 中间件栈（只加自定义中间件，框架自动创建 Summarization/Memory/TodoList/Filesystem 等）
            middleware=[
                # 输入守卫：长度截断
                input_guard,
                # 工具调用硬限制：防止无限循环
                tool_limiter,
                # Sub Agent 容错
                resilience,
            ],

            # 上下文
            context_schema=Context,
        )

        logger.info("Copilot Deep Agent created: 1 main + 6 subs (+ Summarization + Memory + InputGuard + ToolLimiter)")

        # 后置修改：自定义框架自动创建的 SummarizationMiddleware 参数
        try:
            from deepagents.middleware.summarization import SummarizationMiddleware as SMClass
            from app.services.chat.agent import SUMMARY_PROMPT  # 复用 Chat Agent 的摘要 prompt
            for mw in getattr(_deep_agent, 'middleware', []):
                if isinstance(mw, SMClass):
                    mw.trigger = [("messages", 20), ("fraction", 0.80)]
                    mw.keep = ("messages", 10)
                    mw.summary_prompt = SUMMARY_PROMPT
                    logger.info("Patched Copilot SummarizationMiddleware with custom trigger/keep/prompt")
                    break
        except Exception as e:
            logger.warning("Could not patch Copilot SummarizationMiddleware: %s", e)

    except Exception as e:
        logger.error("Failed to create Copilot Deep Agent: %s", e, exc_info=True)
        raise

    return _deep_agent


def reset_deep_agent():
    """重置Agent单例"""
    global _deep_agent
    _deep_agent = None
