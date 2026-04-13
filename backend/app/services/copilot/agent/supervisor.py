"""Supervisor Agent — 任务分解、分配、综合

V2 优化：
1. 并行执行同类型 Worker（researcher 并行采集）
2. supervisor 路由不再调 LLM——纯逻辑路由，省掉 3 次 LLM 调用
3. Worker 补全关键工具（researcher 加 list_topics）
4. 每个 Worker 独立 checkpointer，避免状态冲突
5. 错误恢复：Worker 失败时标记并继续，不阻塞整个流程
"""

import json
import logging
from datetime import datetime, timezone
from typing import TypedDict, Annotated

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
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

⚠️ 关键规则：你没有工具，不能查询真实数据！所有数据操作必须分配给 worker。

规则：
1. 禁止自我介绍
2. 用中文回复
3. **绝对不要自己回答涉及数据查询/操作的问题**（如"列出专题"、"系统状态"等），你无法获取真实数据，回答就是编造！必须分配给 worker。

对于所有用户请求，按以下JSON格式回复：

```json
{
  "delegate": true,
  "tasks": [
    {"worker": "researcher", "description": "搜索AI最新新闻"},
    {"worker": "analyst", "description": "分析知识库中关于AI的内容"},
    {"worker": "manager", "description": "列出所有专题"}
  ]
}
```

worker 只能是: researcher（搜索采集）、analyst（分析报告）、manager（数据管理/查询/系统状态）

即使是简单任务（如"列出专题"、"系统状态"），也必须分配给对应 worker，不要自己回答！"""


# ─── Nodes ──────────────────────────────────────────────────────────────────

async def supervisor_node(state: SupervisorState) -> dict:
    """Supervisor 节点：只做两件事——
    1. 首次进入：调 LLM 分析意图，分解任务或直接回复
    2. 后续进入：纯逻辑路由（不调 LLM），找下一个 pending worker 或综合结果
    """
    messages = state.get("messages", [])
    tasks = state.get("tasks", [])

    # ── Phase 2+: Workers have returned, route without LLM ──
    if tasks:
        done_tasks = [t for t in tasks if t.get("status") == "done"]
        failed_tasks = [t for t in tasks if t.get("status") == "failed"]
        pending_tasks = [t for t in tasks if t.get("status") == "pending"]

        # All tasks done (or failed) → synthesize final answer
        if not pending_tasks:
            return await _synthesize(state, done_tasks, failed_tasks)

        # Find next pending worker (prioritize: researcher first for parallel, then analyst, then manager)
        # Group by worker to allow parallel dispatch
        worker_order = ["researcher", "analyst", "manager"]
        for w in worker_order:
            worker_pending = [t for t in pending_tasks if t["worker"] == w]
            if worker_pending:
                updated_tasks = []
                for t in tasks:
                    if t["worker"] == w and t["status"] == "pending":
                        t["status"] = "running"
                    updated_tasks.append(t)
                return {
                    "next_worker": w,
                    "tasks": updated_tasks,
                }

        # Fallback
        return {"next_worker": "FINISH"}

    # ── Phase 1: New request — analyze intent via LLM ──
    from app.services.copilot.llm import get_chat_llm

    llm = get_chat_llm(max_tokens=1500)
    last_msg = messages[-1].content if messages else ""
    analysis = await llm.ainvoke([
        SystemMessage(content=SUPERVISOR_PROMPT),
        HumanMessage(content=last_msg),
    ])
    response_text = analysis.content or ""

    # Try to parse task delegation
    parsed_tasks = _parse_tasks(response_text)
    if parsed_tasks:
        logger.info("Supervisor delegates %d tasks: %s", len(parsed_tasks),
                   [f"{t['worker']}:{t['description'][:30]}" for t in parsed_tasks])
        # Mark first worker's tasks as running
        first_worker = parsed_tasks[0]["worker"]
        for t in parsed_tasks:
            if t["worker"] == first_worker:
                t["status"] = "running"
        return {
            "tasks": parsed_tasks,
            "next_worker": first_worker,
        }

    # LLM failed to produce valid delegation — assign to manager as fallback
    # (Supervisor must NEVER answer directly, it has no tools and will fabricate data)
    logger.warning("Supervisor LLM output could not be parsed as tasks, delegating to manager as fallback")
    last_msg = messages[-1].content if messages else ""
    fallback_tasks = [{
        "id": "task_1",
        "description": last_msg,
        "worker": "manager",
        "status": "running",
        "result": None,
        "error": None,
    }]
    return {
        "tasks": fallback_tasks,
        "next_worker": "manager",
    }


async def _synthesize(state: SupervisorState, done_tasks: list[dict], failed_tasks: list[dict]) -> dict:
    """综合所有 Worker 结果，生成最终回复。
    
    优化：单个 worker 完成时直接透传结果，不调 LLM 综合（省一次调用）。
    多个 worker 时才调 LLM 综合。
    """
    # Single task done → just pass through worker result
    if len(done_tasks) == 1 and not failed_tasks:
        result_text = done_tasks[0].get("result", "") or "无结果"
        logger.info("Supervisor: single task done, pass-through result")
        return {
            "messages": [AIMessage(content=result_text)],
            "next_worker": "FINISH",
        }

    # Multiple tasks or has failures → synthesize via LLM
    from app.services.copilot.llm import get_chat_llm

    llm = get_chat_llm(max_tokens=1500)
    messages = state.get("messages", [])

    results_parts = []
    for t in done_tasks:
        results_parts.append(f"- [{t['worker']}] {t['description']}: {t.get('result', '无结果')[:300]}")
    for t in failed_tasks:
        results_parts.append(f"- [{t['worker']}] {t['description']}: ❌ 失败 - {t.get('error', '未知错误')[:100]}")

    results_text = "\n".join(results_parts)
    synth_prompt = f"以下是各助手的工作结果，请综合后简洁回复用户：\n{results_text}"
    response = await llm.ainvoke(messages + [HumanMessage(content=synth_prompt)])
    return {
        "messages": [AIMessage(content=response.content)],
        "next_worker": "FINISH",
    }


async def researcher_node(state: SupervisorState) -> dict:
    """Researcher Worker 节点：搜索+采集，支持并行执行多个任务。"""
    import asyncio
    from app.services.copilot.agent.workers import get_worker

    researcher = get_worker("researcher")

    my_tasks = [
        t for t in state.get("tasks", [])
        if t["worker"] == "researcher" and t["status"] in ("pending", "running")
    ]
    if not my_tasks:
        return {"next_worker": "supervisor"}

    # Parallel execution of all researcher tasks
    async def run_task(task: dict) -> tuple[str, str, str | None]:
        config = {"configurable": {"thread_id": f"researcher_{task['id']}"}}
        try:
            result = await researcher.ainvoke(
                {"messages": [HumanMessage(content=task["description"])]},
                config=config,
            )
            result_text = result["messages"][-1].content if result.get("messages") else ""
            return task["id"], "done", result_text[:500]
        except Exception as e:
            logger.error("Researcher task %s failed: %s", task["id"], e)
            return task["id"], "failed", f"执行失败: {str(e)[:100]}"

    results = await asyncio.gather(*[run_task(t) for t in my_tasks])

    updated_tasks = []
    for t in state.get("tasks", []):
        for tid, status, content in results:
            if t["id"] == tid:
                t["status"] = status
                if status == "done":
                    t["result"] = content
                else:
                    t["error"] = content
        updated_tasks.append(t)

    return {
        "tasks": updated_tasks,
        "next_worker": "supervisor",
    }


async def analyst_node(state: SupervisorState) -> dict:
    """Analyst Worker 节点：分析+报告。"""
    from app.services.copilot.agent.workers import get_worker

    analyst = get_worker("analyst")
    task = _get_active_task(state, "analyst")
    if not task:
        return {"next_worker": "supervisor"}

    config = {"configurable": {"thread_id": f"analyst_{task['id']}"}}
    try:
        result = await analyst.ainvoke(
            {"messages": [HumanMessage(content=task["description"])]},
            config=config,
        )
        result_text = result["messages"][-1].content if result.get("messages") else ""
        return {
            "tasks": _update_task(state["tasks"], task["id"], "done", result=result_text[:500]),
            "next_worker": "supervisor",
        }
    except Exception as e:
        logger.error("Analyst task %s failed: %s", task["id"], e)
        return {
            "tasks": _update_task(state["tasks"], task["id"], "failed", error=str(e)[:100]),
            "next_worker": "supervisor",
        }


async def manager_node(state: SupervisorState) -> dict:
    """Manager Worker 节点：数据CRUD+系统管理。"""
    from app.services.copilot.agent.workers import get_worker

    manager = get_worker("manager")
    task = _get_active_task(state, "manager")
    if not task:
        return {"next_worker": "supervisor"}

    config = {"configurable": {"thread_id": f"manager_{task['id']}"}}
    try:
        result = await manager.ainvoke(
            {"messages": [HumanMessage(content=task["description"])]},
            config=config,
        )
        result_text = result["messages"][-1].content if result.get("messages") else ""
        return {
            "tasks": _update_task(state["tasks"], task["id"], "done", result=result_text[:500]),
            "next_worker": "supervisor",
        }
    except Exception as e:
        logger.error("Manager task %s failed: %s", task["id"], e)
        return {
            "tasks": _update_task(state["tasks"], task["id"], "failed", error=str(e)[:100]),
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

    # Try JSON first
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
                            "error": None,
                        })
                if tasks:
                    logger.info("Parsed %d tasks from JSON block", len(tasks))
                    return tasks
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.debug("JSON parse failed, falling back to line parser")

    # Fallback: line parser
    for line in text.split("\n"):
        line = line.strip()
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
                    "error": None,
                })
        except (ValueError, IndexError):
            continue

    return tasks


def _extract_json_block(text: str) -> str | None:
    """从文本中提取 ```json ... ``` 代码块或裸 JSON 对象。"""
    import re
    m = re.search(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        return m.group(1).strip()

    m = re.search(r"```\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        if candidate.startswith("{"):
            return candidate

    start = text.find('{"delegate"')
    if start == -1:
        start = text.find('{ "delegate"')
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

    return None


def _update_task(tasks: list[dict], task_id: str, status: str,
                 result: str | None = None, error: str | None = None) -> list[dict]:
    """更新指定任务状态。"""
    updated = []
    for t in tasks:
        if t["id"] == task_id:
            t["status"] = status
            if result is not None:
                t["result"] = result
            if error is not None:
                t["error"] = error
        updated.append(t)
    return updated


def _get_active_task(state: SupervisorState, worker: str) -> dict | None:
    """获取指定 worker 的当前活跃任务（running > pending）。"""
    for t in state.get("tasks", []):
        if t["worker"] == worker and t["status"] == "running":
            return t
    for t in state.get("tasks", []):
        if t["worker"] == worker and t["status"] == "pending":
            return t
    return None
