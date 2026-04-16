"""Copilot Agent — 三模式架构。

1. single_agent: 现有 create_react_agent + 24 工具（最快，简单任务）
2. supervisor:   旧版 Supervisor 多节点图编排（保留备用）
3. deep_agent:   新版 DeepAgents 主+子 Agent 架构（推荐，复杂任务）

路由逻辑：
- 默认走 single_agent
- use_supervisor=True → 走 supervisor
- use_deep_agent=True → 走 deep_agent
"""

import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)

# ─── System Prompt (single agent) ──────────────────────────────────────────

COPILOT_SYSTEM_PROMPT = """你是一个研究助手，直接回答用户问题。

规则：
1. 禁止自我介绍。不要说"你好"、"我是"、"我可以帮你"等开场白。
2. 直接执行用户请求或回答问题。
3. 用中文回复，简洁专业。
4. 需要搜索就调 search_web，需要查知识库就调 rag_query，需要管理数据就调对应工具。
5. 工具返回结果后，直接总结给用户，不要重复工具原始输出。
6. 一次请求中尽量少调用工具，避免调用过多。
7. upload_document 工具需要 kb_id、title 和 content 三个参数。如果用户没提供 content，请用用户给的信息作为内容，不要先查列表再上传。
8. generate_report 工具只需调用一次，不要重复调用。"""

# ─── Single Agent ──────────────────────────────────────────────────────────

_checkpointer = MemorySaver()
_compiled_agent = None


def get_compiled_agent():
    """获取预构建的 ReAct Agent（单例）。"""
    global _compiled_agent
    if _compiled_agent is not None:
        return _compiled_agent

    from app.services.copilot.llm import get_chat_llm
    from app.services.copilot.tools import get_all_tools

    all_tools = get_all_tools()
    llm = get_chat_llm()

    _compiled_agent = create_react_agent(
        model=llm,
        tools=all_tools,
        checkpointer=_checkpointer,
        prompt=COPILOT_SYSTEM_PROMPT,
    )
    _compiled_agent = _compiled_agent.with_config(recursion_limit=50)

    logger.info("Copilot single agent created with %d tools", len(all_tools))
    return _compiled_agent


def reset_agent():
    """重置 single agent 单例。"""
    global _compiled_agent
    _compiled_agent = None


# ─── Supervisor Mode (legacy) ──────────────────────────────────────────────

def get_supervisor_graph():
    """获取 Supervisor 多 Agent Graph（懒加载，旧版备用）。"""
    from app.services.copilot.agent.supervisor import get_supervisor_graph as _get
    return _get()


def reset_supervisor():
    """重置 Supervisor 单例。"""
    from app.services.copilot.agent.supervisor import reset_supervisor as _reset
    _reset()
    from app.services.copilot.agent.workers import reset_all_workers
    reset_all_workers()


# ─── Deep Agent Mode (new) ──────────────────────────────────────────────────

def get_deep_agent():
    """获取 Deep Agent 实例（懒加载）。"""
    from app.services.copilot.agent.deep_agent import get_deep_agent as _get
    return _get()


def reset_deep_agent():
    """重置 Deep Agent 单例。"""
    from app.services.copilot.agent.deep_agent import reset_deep_agent as _reset
    _reset()


# ─── Public: run agent and collect final response ──────────────────────────

async def run_copilot(
    message: str,
    thread_id: str,
    use_supervisor: bool = False,
    use_deep_agent: bool = False,
) -> dict:
    """运行 Copilot Agent（非流式）。

    Args:
        message: 用户消息
        thread_id: 会话 ID
        use_supervisor: 是否使用旧版 Supervisor 模式
        use_deep_agent: 是否使用 Deep Agent 模式
    """
    if use_deep_agent:
        return await _run_deep_agent(message, thread_id)

    if use_supervisor:
        return await _run_supervisor(message, thread_id)

    # 默认走单 Agent
    agent = get_compiled_agent()
    config = {"configurable": {"thread_id": thread_id}}
    input_msg = {"messages": [HumanMessage(content=message)]}

    final_content = ""
    tool_log = []

    async for event in agent.astream_events(input_msg, config=config, version="v2"):
        kind = event.get("event", "")

        if kind == "on_chat_model_end":
            output = event.get("data", {}).get("output", {})
            if hasattr(output, "content") and output.content:
                has_tool_calls = hasattr(output, "tool_calls") and output.tool_calls
                if not has_tool_calls:
                    final_content = output.content

        elif kind == "on_tool_end":
            tool_name = event.get("name", "")
            output = str(event.get("data", {}).get("output", ""))[:200]
            tool_log.append({
                "tool": tool_name,
                "status": "done",
                "result_preview": output,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    return {
        "response": final_content,
        "tool_log": tool_log,
        "thread_id": thread_id,
    }


async def _run_supervisor(message: str, thread_id: str) -> dict:
    """运行旧版 Supervisor 多 Agent 模式。"""
    graph = get_supervisor_graph()
    config = {"configurable": {"thread_id": f"sv_{thread_id}"}}
    input_msg = {
        "messages": [HumanMessage(content=message)],
        "tasks": [],
        "next_worker": None,
    }

    result_state = await graph.ainvoke(input_msg, config=config)

    messages = result_state.get("messages", [])
    final_content = ""
    if messages:
        last_msg = messages[-1]
        if hasattr(last_msg, "content"):
            final_content = last_msg.content

    tasks = result_state.get("tasks", [])
    task_summary = [
        {"worker": t.get("worker"), "description": t.get("description"), "status": t.get("status")}
        for t in tasks
    ]

    return {
        "response": final_content,
        "tool_log": [],
        "thread_id": thread_id,
        "mode": "supervisor",
        "tasks": task_summary,
    }


async def _run_deep_agent(message: str, thread_id: str) -> dict:
    """运行 Deep Agent 模式（非流式）。"""
    from app.services.copilot.agent.memory_tools import set_current_context, clear_current_context

    agent = get_deep_agent()
    config = {"configurable": {"thread_id": f"da_{thread_id}"}}

    # 设置用户上下文（简化：用 thread_id 作为 user_id 占位）
    set_current_context(user_id=thread_id, session_id=thread_id)

    try:
        input_msg = {"messages": [HumanMessage(content=message)]}

        final_content = ""
        tool_log = []

        async for event in agent.astream_events(input_msg, config=config, version="v2"):
            kind = event.get("event", "")

            if kind == "on_chat_model_end":
                output = event.get("data", {}).get("output", {})
                if hasattr(output, "content") and output.content:
                    has_tool_calls = hasattr(output, "tool_calls") and output.tool_calls
                    if not has_tool_calls:
                        final_content = output.content

            elif kind == "on_tool_end":
                tool_name = event.get("name", "")
                output = str(event.get("data", {}).get("output", ""))[:200]
                tool_log.append({
                    "tool": tool_name,
                    "status": "done",
                    "result_preview": output,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

        return {
            "response": final_content,
            "tool_log": tool_log,
            "thread_id": thread_id,
            "mode": "deep_agent",
        }
    finally:
        clear_current_context()
