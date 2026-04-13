"""Copilot Agent — 单 Agent + Supervisor 双模式。

简单任务：直接走 create_react_agent（快，24 工具）
复杂任务：走 Supervisor 多 Agent（researcher/analyst/manager 协作）

路由逻辑：
1. 用户消息 → 默认走单 Agent
2. API 参数 use_supervisor=True → 走 Supervisor
3. 未来可自动检测复杂度切换

LLM fallback:
- get_chat_llm() 返回带 with_fallbacks 的 LLM 链
- 主模型失败时自动切换后备，无需 reset_agent 或手写 try/except
"""

import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)

# ─── System Prompt ──────────────────────────────────────────────────────────

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

# ─── Checkpointer ──────────────────────────────────────────────────────────

_checkpointer = MemorySaver()
_compiled_agent = None


def get_compiled_agent():
    """获取预构建的 ReAct Agent（单例）。

    LLM 已带 with_fallbacks，主模型失败自动切换，无需预测试。
    """
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

    logger.info("Copilot agent created with %d tools (LLM with fallbacks)", len(all_tools))
    return _compiled_agent


def reset_agent():
    """重置 agent 单例（配置变更时调用）。"""
    global _compiled_agent
    _compiled_agent = None


# ─── Supervisor Mode (optional) ────────────────────────────────────────────

def get_supervisor_graph():
    """获取 Supervisor 多 Agent Graph（懒加载）。"""
    from app.services.copilot.agent.supervisor import get_supervisor_graph as _get
    return _get()


def reset_supervisor():
    """重置 Supervisor 单例。"""
    from app.services.copilot.agent.supervisor import reset_supervisor as _reset
    _reset()
    from app.services.copilot.agent.workers import reset_all_workers
    reset_all_workers()


# ─── Public: run agent and collect final response ──────────────────────────

async def run_copilot(message: str, thread_id: str, use_supervisor: bool = False) -> dict:
    """运行 Copilot Agent（非流式）。

    Args:
        message: 用户消息
        thread_id: 会话 ID
        use_supervisor: 是否使用 Supervisor 多 Agent 模式
    """
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
    """运行 Supervisor 多 Agent 模式（非流式）。

    用 ainvoke 运行整个 graph，从最终 state 取结果。
    """
    graph = get_supervisor_graph()
    config = {"configurable": {"thread_id": f"sv_{thread_id}"}}
    input_msg = {
        "messages": [HumanMessage(content=message)],
        "tasks": [],
        "next_worker": None,
    }

    # 用 ainvoke 运行完整 graph
    result_state = await graph.ainvoke(input_msg, config=config)

    # 从最终 state 提取结果
    messages = result_state.get("messages", [])
    final_content = ""
    if messages:
        last_msg = messages[-1]
        if hasattr(last_msg, "content"):
            final_content = last_msg.content

    tasks = result_state.get("tasks", [])
    task_summary = []
    for t in tasks:
        task_summary.append({
            "worker": t.get("worker"),
            "description": t.get("description"),
            "status": t.get("status"),
        })

    return {
        "response": final_content,
        "tool_log": [],  # Worker 内部工具调用暂不展开
        "thread_id": thread_id,
        "mode": "supervisor",
        "tasks": task_summary,
    }
