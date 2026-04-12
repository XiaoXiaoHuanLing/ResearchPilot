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

对于复杂任务（涉及多种操作：搜索采集、分析报告、数据管理），你必须按以下JSON格式回复：

```json
{
  "delegate": true,
  "tasks": [
    {"worker": "researcher", "description": "搜索AI最新新闻"},
    {"worker": "analyst", "description": "分析知识库中关于AI的内容"},
    {"worker": "manager", "description": "创建AI研究专题"}
  ]
}
```

worker 只能是: researcher（搜索采集）、analyst（分析报告）、manager（数据管理）

对于简单任务（只需要一个操作），直接回复用户，不输出JSON。"""


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
        # 标记所有同 worker 的 pending 任务为 running（支持并行）
        # 优先 researcher（可并行），其他串行
        next_worker = pending_tasks[0]["worker"]
        updated_tasks = []
        for t in state["tasks"]:
            if t["worker"] == next_worker and t["status"] == "pending":
                t["status"] = "running"
            updated_tasks.append(t)
        return {
            "next_worker": next_worker,
            "tasks": updated_tasks,
        }

    # 新请求：分析是否需要分解
    last_msg = messages[-1].content if messages else ""
    analysis = await llm.ainvoke([
        SystemMessage(content=SUPERVISOR_PROMPT),
        HumanMessage(content=last_msg),
    ])
    response_text = analysis.content or ""

    # 尝试解析 JSON 格式的任务分配
    tasks = _parse_tasks(response_text)
    if tasks:
        logger.info("Supervisor delegates %d tasks: %s", len(tasks), 
                   [f"{t['worker']}:{t['description'][:30]}" for t in tasks])
        return {
            "tasks": tasks,
            "next_worker": tasks[0]["worker"],
        }
    
    # 简单任务：直接回复
    logger.info("Supervisor responds directly (no delegation)")
    return {
        "messages": [AIMessage(content=response_text)],
        "next_worker": "FINISH",
    }


async def researcher_node(state: SupervisorState) -> dict:
    """Researcher Worker 节点：搜索+采集，支持并行执行多个任务。"""
    import asyncio
    from app.services.copilot.agent.workers import get_worker

    researcher = get_worker("researcher")

    # 找出所有属于 researcher 且状态为 pending 或 running 的任务
    my_tasks = [
        t for t in state.get("tasks", [])
        if t["worker"] == "researcher" and t["status"] in ("pending", "running")
    ]
    if not my_tasks:
        return {"next_worker": "supervisor"}

    # 并行执行所有采集任务
    async def run_task(task: dict) -> tuple[str, str]:
        config = {"configurable": {"thread_id": f"researcher_{task['id']}"}}
        try:
            result = await researcher.ainvoke(
                {"messages": [HumanMessage(content=task["description"])]},
                config=config,
            )
            result_text = result["messages"][-1].content if result.get("messages") else ""
            return task["id"], result_text[:500]
        except Exception as e:
            logger.error("Researcher task %s failed: %s", task["id"], e)
            return task["id"], f"❌ 执行失败: {e}"

    results = await asyncio.gather(*[run_task(t) for t in my_tasks])

    # 更新所有任务状态为 done
    updated_tasks = []
    for t in state.get("tasks", []):
        for tid, content in results:
            if t["id"] == tid:
                t["status"] = "done"
                t["result"] = content
        updated_tasks.append(t)

    return {
        "tasks": updated_tasks,
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
    """从 LLM 输出解析任务列表，支持 JSON 和 markdown 两种格式。"""
    tasks = []
    counter = 0

    # 优先尝试 JSON 解析
    json_str = _extract_json_block(text)
    if json_str:
        try:
            data = json.loads(json_str)
            if isinstance(data, dict) and data.get("delegate") and isinstance(data.get("tasks"), list):
                for item in data["tasks"]:
                    worker = item.get("worker", "")
                    description = item.get("description", "")
                    if worker in ("researcher", "analyst", "manager") and description:
                        counter += 1
                        tasks.append({
                            "id": f"task_{counter}",
                            "description": description,
                            "worker": worker,
                            "status": "pending",
                            "result": None,
                        })
                if tasks:
                    logger.info("Parsed %d tasks from JSON block", len(tasks))
                    return tasks
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.debug("JSON parse failed, falling back to line parser")

    # Fallback: 逐行解析 - [worker] description 格式
    for line in text.split("\n"):
        line = line.strip()
        # Match patterns like: - [researcher] xxx  or  * [analyst] xxx
        if not (line.startswith("- [") or line.startswith("* [")):
            continue
        try:
            bracket_start = line.index("[")
            bracket_end = line.index("]", bracket_start)
            worker = line[bracket_start + 1:bracket_end]
            description = line[bracket_end + 1:].strip().lstrip("-:")
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


def _extract_json_block(text: str) -> str | None:
    """从文本中提取 ```json ... ``` 代码块或裸 JSON 对象。"""
    # Try ```json ... ``` block first
    import re
    m = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        return m.group(1).strip()

    # Try ``` ... ``` block
    m = re.search(r"```\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        if candidate.startswith("{"):
            return candidate

    # Try finding a raw JSON object with "delegate" key
    # Use non-greedy match, find the outermost balanced braces
    start = text.find('{"delegate"')
    if start == -1:
        start = text.find('{ "delegate"')
    if start >= 0:
        # Walk forward to find matching closing brace
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

    return None


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
