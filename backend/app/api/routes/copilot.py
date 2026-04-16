"""Copilot API 路由 — 对外接口层。

核心：
1. 多轮对话：用 Checkpointer 恢复历史，只传新消息
2. 流式输出：astream_events 原生 streaming 逐 token 推送
3. 非流式：用 agent.run_copilot() 收集
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Helpers ──────────────────────────────────────────────────────────────

def _extract_chunk_text(content) -> str:
    """Extract text from a chunk's content field (str or list[dict])."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text = ""
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text += part.get("text", "")
            elif isinstance(part, str):
                text += part
        return text
    return ""


# ─── Schemas ──────────────────────────────────────────────────────────────

class CopilotChatRequest(BaseModel):
    message: str
    thread_id: str | None = None
    use_supervisor: bool = False
    use_deep_agent: bool = False

class CopilotChatResponse(BaseModel):
    response: str
    tool_log: list[dict] = []
    thread_id: str
    mode: str | None = None
    tasks: list[dict] | None = None

class CopilotSessionInfo(BaseModel):
    thread_id: str
    message_count: int = 0
    last_active: str = ""


# ─── Session Index ────────────────────────────────────────────────────────

_session_index: dict[str, dict] = {}

def _touch_session(thread_id: str) -> None:
    _session_index[thread_id] = {
        "thread_id": thread_id,
        "last_active": datetime.now(timezone.utc).isoformat(),
    }


# ─── Endpoints ────────────────────────────────────────────────────────────

@router.post("/chat", response_model=CopilotChatResponse)
async def copilot_chat(payload: CopilotChatRequest):
    """Copilot 对话（非流式）。"""
    from app.services.copilot.agent import run_copilot

    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    thread_id = payload.thread_id or f"cp_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    _touch_session(thread_id)

    try:
        result = await run_copilot(
            payload.message, thread_id,
            use_supervisor=payload.use_supervisor,
            use_deep_agent=payload.use_deep_agent,
        )
        _touch_session(result["thread_id"])
        return CopilotChatResponse(**result)
    except Exception as e:
        err_msg = str(e)
        logger.error("Copilot chat error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"智能助手执行失败: {err_msg[:200]}")


@router.post("/chat/stream")
async def copilot_chat_stream(payload: CopilotChatRequest):
    """Copilot 对话（SSE 原生流式）— 支持 Supervisor 多 Agent 模式。"""
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    thread_id = payload.thread_id or f"cp_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    _touch_session(thread_id)

    async def event_generator():
        try:
            if payload.use_deep_agent:
                async for chunk in _deep_agent_stream(payload.message, thread_id):
                    yield chunk
            elif payload.use_supervisor:
                async for chunk in _supervisor_stream(payload.message, thread_id):
                    yield chunk
            else:
                async for chunk in _single_agent_stream(payload.message, thread_id):
                    yield chunk
        except Exception as e:
            err_msg = str(e)
            logger.error("Copilot stream error: %s", e, exc_info=True)
            data = json.dumps({"type": "error", "message": err_msg[:300]}, ensure_ascii=False)
            yield f"event: error\ndata: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Type": "text/event-stream; charset=utf-8",
        },
    )


async def _single_agent_stream(message: str, thread_id: str):
    """单 Agent SSE 流式生成器 — 原生 streaming 逐 token 推送。"""
    from app.services.copilot.agent import get_compiled_agent

    agent = get_compiled_agent()
    config = {"configurable": {"thread_id": thread_id}}
    input_msg = {"messages": [HumanMessage(content=message)]}

    try:
        async for event in agent.astream_events(input_msg, config=config, version="v2"):
            kind = event.get("event", "")

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    text = _extract_chunk_text(chunk.content)
                    if text:
                        data = json.dumps({"type": "token", "content": text}, ensure_ascii=False)
                        yield f"event: token\ndata: {data}\n\n"

            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                data = json.dumps({"type": "tool_start", "tool": tool_name}, ensure_ascii=False)
                yield f"event: tool_start\ndata: {data}\n\n"

            elif kind == "on_tool_end":
                tool_name = event.get("name", "")
                raw_output = event.get("data", {}).get("output", "")
                if hasattr(raw_output, "content"):
                    result_str = str(raw_output.content)
                else:
                    result_str = str(raw_output)
                result_preview = result_str.replace("\n", " ")[:200]
                data = json.dumps({"type": "tool_end", "tool": tool_name, "result": result_preview}, ensure_ascii=False)
                yield f"event: tool_end\ndata: {data}\n\n"

        yield f"event: done\ndata: {json.dumps({'type': 'done', 'thread_id': thread_id}, ensure_ascii=False)}\n\n"
        _touch_session(thread_id)

    except Exception as e:
        raise


async def _supervisor_stream(message: str, thread_id: str):
    """Supervisor 多 Agent SSE 流式生成器 — 原生 streaming 逐 token 推送。

    推送事件：worker_switch / token / tool_start / tool_end / done
    """
    from app.services.copilot.agent.supervisor import get_supervisor_graph

    graph = get_supervisor_graph()
    config = {"configurable": {"thread_id": f"sv_{thread_id}"}}
    input_msg = {
        "messages": [HumanMessage(content=message)],
        "tasks": [],
        "next_worker": None,
    }

    _current_worker = "supervisor"

    try:
        async for event in graph.astream_events(input_msg, config=config, version="v2"):
            kind = event.get("event", "")
            name = event.get("name", "")

            worker_nodes = {"researcher", "analyst", "manager", "supervisor"}

            if kind == "on_chain_start" and name in worker_nodes:
                _current_worker = name
                data = json.dumps({"type": "worker_switch", "worker": name}, ensure_ascii=False)
                yield f"event: worker_switch\ndata: {data}\n\n"
                continue

            if kind == "on_chain_end" and name in worker_nodes:
                _current_worker = "supervisor"
                continue

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    text = _extract_chunk_text(chunk.content)
                    if text:
                        data = json.dumps({
                            "type": "token",
                            "content": text,
                            "worker": _current_worker,
                        }, ensure_ascii=False)
                        yield f"event: token\ndata: {data}\n\n"

            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                data = json.dumps({
                    "type": "tool_start",
                    "tool": tool_name,
                    "worker": _current_worker,
                }, ensure_ascii=False)
                yield f"event: tool_start\ndata: {data}\n\n"

            elif kind == "on_tool_end":
                tool_name = event.get("name", "")
                raw_output = event.get("data", {}).get("output", "")
                if hasattr(raw_output, "content"):
                    result_str = str(raw_output.content)
                else:
                    result_str = str(raw_output)
                result_preview = result_str.replace("\n", " ")[:200]
                data = json.dumps({
                    "type": "tool_end",
                    "tool": tool_name,
                    "result": result_preview,
                    "worker": _current_worker,
                }, ensure_ascii=False)
                yield f"event: tool_end\ndata: {data}\n\n"

        yield f"event: done\ndata: {json.dumps({'type': 'done', 'thread_id': thread_id, 'mode': 'supervisor'}, ensure_ascii=False)}\n\n"
        _touch_session(thread_id)

    except Exception as e:
        raise


async def _deep_agent_stream(message: str, thread_id: str):
    """Deep Agent SSE 流式生成器 — 原生 streaming，按事件类型推送。

    推送事件：
    - token: 主 Agent 的文本输出
    - plan: write_todos 规划事件
    - plan_updated: todos 状态更新
    - delegate: task 委托 Sub Agent
    - delegate_done: Sub Agent 返回结果
    - tool_start / tool_end: 其他工具调用
    - sub_progress: Sub Agent 正在工作
    - done: 完成
    """
    from app.services.copilot.agent import get_deep_agent
    from app.services.copilot.agent.memory_tools import set_current_context, clear_current_context

    agent = get_deep_agent()
    config = {"configurable": {"thread_id": f"da_{thread_id}"}}
    input_msg = {"messages": [HumanMessage(content=message)]}

    # 设置用户上下文
    set_current_context(user_id=thread_id, session_id=thread_id)

    try:
        async for event in agent.astream_events(input_msg, config=config, version="v2"):
            kind = event.get("event", "")
            agent_name = event.get("metadata", {}).get("lc_agent_name", "main")

            # LLM 输出 token（仅主 Agent）
            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    text = _extract_chunk_text(chunk.content)
                    if text and agent_name == "main":
                        data = json.dumps({"type": "token", "content": text}, ensure_ascii=False)
                        yield f"event: token\ndata: {data}\n\n"
                    # Sub Agent 的 token 不推，避免刷屏

            # 工具调用开始
            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                tool_input = event.get("data", {}).get("input", {})

                if tool_name == "write_todos":
                    data = json.dumps({"type": "plan", "todos": tool_input.get("todos", [])}, ensure_ascii=False)
                    yield f"event: plan\ndata: {data}\n\n"
                elif tool_name == "task":
                    data = json.dumps({
                        "type": "delegate",
                        "agent": tool_input.get("subagent_type", "?"),
                        "task": tool_input.get("description", "")[:200],
                    }, ensure_ascii=False)
                    yield f"event: delegate\ndata: {data}\n\n"
                else:
                    # Sub Agent 内部工具推简化事件，前端用小标签展示
                    data = json.dumps({
                        "type": "sub_tool_start",
                        "tool": tool_name,
                        "agent": agent_name,
                    }, ensure_ascii=False)
                    yield f"event: sub_tool_start\ndata: {data}\n\n"

            # 工具调用结束
            elif kind == "on_tool_end":
                tool_name = event.get("name", "")
                raw_output = event.get("data", {}).get("output", "")

                if tool_name == "task":
                    if hasattr(raw_output, "content"):
                        result_str = str(raw_output.content)
                    else:
                        result_str = str(raw_output)
                    preview = result_str.replace("\n", " ")[:200]
                    data = json.dumps({"type": "delegate_done", "preview": preview}, ensure_ascii=False)
                    yield f"event: delegate_done\ndata: {data}\n\n"
                elif tool_name == "write_todos":
                    data = json.dumps({"type": "plan_updated", "status": "updated"}, ensure_ascii=False)
                    yield f"event: plan_updated\ndata: {data}\n\n"
                else:
                    # Sub Agent 内部工具完成 — 推简化事件
                    if hasattr(raw_output, "content"):
                        result_str = str(raw_output.content)
                    else:
                        result_str = str(raw_output)
                    result_preview = result_str.replace("\n", " ")[:100]
                    data = json.dumps({
                        "type": "sub_tool_end",
                        "tool": tool_name,
                        "result": result_preview,
                        "agent": agent_name,
                    }, ensure_ascii=False)
                    yield f"event: sub_tool_end\ndata: {data}\n\n"

        yield f"event: done\ndata: {json.dumps({'type': 'done', 'thread_id': thread_id, 'mode': 'deep_agent'}, ensure_ascii=False)}\n\n"
        _touch_session(thread_id)

    except Exception as e:
        raise
    finally:
        clear_current_context()


@router.get("/sessions", response_model=list[CopilotSessionInfo])
async def list_sessions():
    sessions = []
    for tid, info in _session_index.items():
        sessions.append(CopilotSessionInfo(thread_id=tid, last_active=info.get("last_active", "")))
    sessions.sort(key=lambda s: s.last_active, reverse=True)
    return sessions[:50]


@router.delete("/sessions/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(thread_id: str):
    _session_index.pop(thread_id, None)


@router.get("/tools")
async def list_tools():
    from app.services.copilot.tools import get_all_tools
    all_tools = get_all_tools()
    tools_info = [{"name": t.name, "description": (t.description or "")[:200]} for t in all_tools]
    return {"tools": tools_info, "count": len(tools_info)}
