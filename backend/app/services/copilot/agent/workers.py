"""多智能体 Worker 定义 — Researcher, Analyst, Manager

每个 Worker 是一个独立的 create_react_agent，拥有专属工具子集。
"""

import logging

from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)

# ─── Worker Prompts ────────────────────────────────────────────────────────

RESEARCHER_PROMPT = """你是采集助手，负责搜索和采集信息。

规则：
1. 禁止自我介绍，直接执行任务
2. 只做搜索和采集，不做分析
3. 返回搜索结果摘要和采集状态
4. 如果搜索无结果，明确告知
5. 用中文回复，简洁专业"""

ANALYST_PROMPT = """你是分析助手，负责知识库问答、数据分析和报告生成。

规则：
1. 禁止自我介绍，直接执行任务
2. 先用 rag_query 查知识库，不够再请求补充信息
3. 生成报告时提供关键发现
4. 返回分析结论，不要重复原始数据
5. 用中文回复，简洁专业"""

MANAGER_PROMPT = """你是管理助手，负责数据CRUD和系统管理。

规则：
1. 禁止自我介绍，直接执行任务
2. 批量操作分步执行
3. 返回操作结果
4. 用中文回复，简洁专业"""


# ─── Worker Agent Instances (lazy) ──────────────────────────────────────────

_workers: dict[str, object] = {}
_worker_checkpointer = MemorySaver()


def get_researcher():
    """获取采集 Agent（单例）— 搜索 + 入库 + 采集。"""
    if "researcher" not in _workers:
        from app.services.copilot.llm import get_chat_llm
        from app.services.copilot.tools.search import TOOLS as search_tools

        llm = get_chat_llm(streaming=True)
        tools = search_tools  # search_web + ingest_url + collect_topic

        _workers["researcher"] = create_react_agent(
            model=llm,
            tools=tools,
            checkpointer=_worker_checkpointer,
            prompt=RESEARCHER_PROMPT,
        ).with_config(recursion_limit=30)
        logger.info("Researcher agent created with %d tools", len(tools))
    return _workers["researcher"]


def get_analyst():
    """获取分析 Agent（单例）。"""
    if "analyst" not in _workers:
        from app.services.copilot.llm import get_chat_llm
        from app.services.copilot.tools.rag import TOOLS as rag_tools
        from app.services.copilot.tools.report import TOOLS as report_tools

        llm = get_chat_llm(streaming=True)
        tools = rag_tools + report_tools

        _workers["analyst"] = create_react_agent(
            model=llm,
            tools=tools,
            checkpointer=_worker_checkpointer,
            prompt=ANALYST_PROMPT,
        ).with_config(recursion_limit=30)
        logger.info("Analyst agent created with %d tools", len(tools))
    return _workers["analyst"]


def get_manager():
    """获取管理 Agent（单例）。"""
    if "manager" not in _workers:
        from app.services.copilot.llm import get_chat_llm
        from app.services.copilot.tools.topic import TOOLS as topic_tools
        from app.services.copilot.tools.article import TOOLS as article_tools
        from app.services.copilot.tools.knowledge_base import TOOLS as kb_tools
        from app.services.copilot.tools.system import TOOLS as system_tools
        from app.services.copilot.tools.context import TOOLS as context_tools

        llm = get_chat_llm(streaming=True)
        tools = topic_tools + article_tools + kb_tools + system_tools + context_tools

        _workers["manager"] = create_react_agent(
            model=llm,
            tools=tools,
            checkpointer=_worker_checkpointer,
            prompt=MANAGER_PROMPT,
        ).with_config(recursion_limit=30)
        logger.info("Manager agent created with %d tools", len(tools))
    return _workers["manager"]


def get_worker(name: str):
    """按名称获取 Worker Agent。"""
    workers = {
        "researcher": get_researcher,
        "analyst": get_analyst,
        "manager": get_manager,
    }
    factory = workers.get(name)
    if factory is None:
        raise ValueError(f"Unknown worker: {name}")
    return factory()


def reset_all_workers():
    """重置所有 Worker 单例（LLM fallback 时调用）。"""
    _workers.clear()
