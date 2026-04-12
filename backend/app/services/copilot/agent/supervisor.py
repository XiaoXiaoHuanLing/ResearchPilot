"""Supervisor Agent — 任务分解、分配、综合

Supervisor 模式：
1. 接收用户消息
2. 分析意图，分解任务（如果需要多步骤）
3. 分配给对应 Worker（researcher/analyst/manager）
4. 收集 Worker 结果，综合后回复用户
"""

import json
import logging
from datetime import datetime, timezone
from typing import TypedDict, Annotated

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)

# ─── State ──────────────────────────────────────────────────────────────────

class SupervisorState(TypedDict):
    messages: Annotated[list, add_messages]
    tasks: list[dict]       # TaskItem list
    next_worker: str | None # Next worker to invoke, or "FINISH"


# ─── Supervisor System Prompt ──────────────────────────────────────────────

SUPERVISOR_PROMPT = """你是研究助手的调度中心。根据用户请求决定任务分配。

规则：
1. 禁止自我介绍
2. 用中文回复

你必须用以下格式回复：

对于复杂任务（涉及搜索+分析+管理多种操作），回复：
DELEGATE
- [researcher] 搜索AI最新新闻
- [analyst] 分析知识库中关于AI的内容
- [manager] 创建AI研究专题

对于简单任务，直接回复用户即可（不加 DELEGATE 前缀）。"""


# ─── Nodes ──────────────────────────────────────────────────────────────────

async def supervisor_node(state: SupervisorState) -> dict:
    """Supervisor 节点：分析意图，分解任务，或直接回复。"""
    from app.services.copilot.llm import get_chat_llm

    llm = get_chat_llm(streaming=True, max_tokens=1500)
    messages = state.get("messages", [])

    # 如果有已完成的任务，综合结果
    done_tasks = [t for t in state.get("tasks", []) if t.get("status") == "done"]
    pending_tasks = [t for t in state.get("tasks", []) if t.get("status") == "pending"]

    if done_tasks and not pending_tasks:
        # 所有任务完成，综合回复
        results_text = "\n".join(
            f"- [{t['worker']}] {t['description']}: {t.get('result', '无结果')[:300]}"
            for t in done_tasks
        )
        synth = f"以下是各助手的工作结果，请综合后简洁回复用户：\n{results_text}"
        response = await llm.ainvoke(messages + [HumanMessage(content=synth)])
        return {
            "messages": [AIMessage(content=response.content)],
            "next_worker": "FINISH",
        }

    if pending_tasks:
        # 还有未完成任务，分配下一个
        next_task = pending_tasks[0]
        return {
            "next_worker": next_task["worker"],
            "tasks": _update_task_status(state["tasks"], next_task["id"], "running"),
        }

    # 新请求：分析是否需要分解
    last_msg = messages[-1].content if messages else ""
    analysis = await llm.ainvoke([
        SystemMessage(content=SUPERVISOR_PROMPT),
        HumanMessage(content=last_msg),
    ])
    response_text = analysis.content or ""

    if "DELEGATE" in response_text:
        tasks = _parse_tasks(response_text)
        logger.info("DELEGATE detected, parsed %d tasks from: %s", len(tasks), response_text[:200])
        if tasks:
            logger.info("Supervisor delegates %d tasks: %s", len(tasks), 
                       [f"{t['worker']}:{t['description'][:30]}" for t in tasks])
            return {
                "tasks": tasks,
                "next_worker": tasks[0]["worker"],
            }
        else:
            logger.warning("DELEGATE detected but no tasks parsed!")
    
    # 简单任务：直接回复
    logger.info("Supervisor responds directly (no delegation)")
    return {
        "messages": [AIMessage(content=response_text)],
        "next_worker": "FINISH",
    }


async def researcher_node(state: SupervisorState) -> dict:
    """Researcher Worker 节点：搜索+采集。"""
    from app.services.copilot.agent.workers import get_worker

    researcher = get_worker("researcher")
    task = _get_running_task(state, "researcher")
    if not task:
        return {"next_worker": "supervisor"}

    config = {"configurable": {"thread_id": f"researcher_{task['id']}"}}
    result = await researcher.ainvoke(
        {"messages": [HumanMessage(content=task["description"])]},
        config=config,
    )
    result_text = result["messages"][-1].content if result.get("messages") else ""
    return {
        "tasks": _update_task_status(state["tasks"], task["id"], "done", result_text[:500]),
        "next_worker": "supervisor",
    }


async def analyst_node(state: SupervisorState) -> dict:
    """Analyst Worker 节点：分析+报告。"""
    from app.services.copilot.agent.workers import get_worker

    analyst = get_worker("analyst")
    task = _get_running_task(state, "analyst")
    if not task:
        return {"next_worker": "supervisor"}

    config = {"configurable": {"thread_id": f"analyst_{task['id']}"}}
    result = await analyst.ainvoke(
        {"messages": [HumanMessage(content=task["description"])]},
        config=config,
    )
    result_text = result["messages"][-1].content if result.get("messages") else ""
    return {
        "tasks": _update_task_status(state["tasks"], task["id"], "done", result_text[:500]),
        "next_worker": "supervisor",
    }


async def manager_node(state: SupervisorState) -> dict:
    """Manager Worker 节点：数据CRUD+系统管理。"""
    from app.services.copilot.agent.workers import get_worker

    manager = get_worker("manager")
    task = _get_running_task(state, "manager")
    if not task:
        return {"next_worker": "supervisor"}

    config = {"configurable": {"thread_id": f"manager_{task['id']}"}}
    result = await manager.ainvoke(
        {"messages": [HumanMessage(content=task["description"])]},
        config=config,
    )
    result_text = result["messages"][-1].content if result.get("messages") else ""
    return {
        "tasks": _update_task_status(state["tasks"], task["id"], "done", result_text[:500]),
        "next_worker": "supervisor",
    }


# ─── Routing ────────────────────────────────────────────────────────────────

def route_to_worker(state: SupervisorState) -> str:
    """根据 next_worker 决定下一个节点。"""
    next_w = state.get("next_worker")
    logger.info("route_to_worker: next_worker=%s, tasks=%s", next_w, 
                [f"{t['worker']}:{t['status']}" for t in state.get("tasks", [])])
    if next_w == "FINISH" or next_w is None:
        return "FINISH"
    if next_w in ("researcher", "analyst", "manager"):
        return next_w
    return "supervisor"


# ─── Supervisor Graph ───────────────────────────────────────────────────────

_supervisor_graph = None
_supervisor_checkpointer = MemorySaver()


def get_supervisor_graph():
    """获取 Supervisor Graph（单例）。"""
    global _supervisor_graph
    if _supervisor_graph is not None:
        return _supervisor_graph

    graph = StateGraph(SupervisorState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("researcher", researcher_node)
    graph.add_node("analyst", analyst_node)
    graph.add_node("manager", manager_node)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", route_to_worker, {
        "researcher": "researcher",
        "analyst": "analyst",
        "manager": "manager",
        "FINISH": END,
    })
    graph.add_edge("researcher", "supervisor")
    graph.add_edge("analyst", "supervisor")
    graph.add_edge("manager", "supervisor")

    _supervisor_graph = graph.compile(checkpointer=_supervisor_checkpointer)
    logger.info("Supervisor graph compiled")
    return _supervisor_graph


def reset_supervisor():
    """重置 Supervisor 单例。"""
    global _supervisor_graph
    _supervisor_graph = None


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _parse_tasks(text: str) -> list[dict]:
    """从 LLM 输出解析任务列表。"""
    tasks = []
    counter = 0
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("- ["):
            continue
        try:
            bracket_end = line.index("]")
            worker = line[2:bracket_end]
            description = line[bracket_end + 1:].strip()
            if worker in ("researcher", "analyst", "manager") and description:
                counter += 1
                tasks.append({
                    "id": f"task_{counter}",
                    "description": description,
                    "worker": worker,
                    "status": "pending",
                    "result": None,
                })
        except (ValueError, IndexError):
            continue
    return tasks


def _update_task_status(tasks: list[dict], task_id: str, status: str, result: str | None = None) -> list[dict]:
    """更新指定任务状态。"""
    updated = []
    for t in tasks:
        if t["id"] == task_id:
            t["status"] = status
            if result is not None:
                t["result"] = result
        updated.append(t)
    return updated


def _get_running_task(state: SupervisorState, worker: str) -> dict | None:
    """获取指定 worker 的当前运行中任务。"""
    for t in state.get("tasks", []):
        if t["worker"] == worker and t["status"] == "running":
            return t
    for t in state.get("tasks", []):
        if t["worker"] == worker and t["status"] == "pending":
            return t
    return None
