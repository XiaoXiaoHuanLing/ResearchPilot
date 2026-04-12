"""Copilot API 路由 — 对外接口层。

核心修复：
1. 多轮对话：用 Checkpointer 恢复历史，只传新消息
2. 流式输出：astream_events token 级流式
3. 非流式：用 agent.run_copilot() 收集（避免 ainvoke 返回空 messages）
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, AIMessage

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────────────────

class CopilotChatRequest(BaseModel):
    message: str
    thread_id: str | None = None
    use_supervisor: bool = False  # 启用多 Agent Supervisor 模式

class CopilotChatResponse(BaseModel):
    response: str
    tool_log: list[dict] = []
    thread_id: str

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
        result = await run_copilot(payload.message, thread_id, use_supervisor=payload.use_supervisor)
        _touch_session(result["thread_id"])
        return CopilotChatResponse(**result)
    except Exception as e:
        err_msg = str(e)
        logger.error("Copilot chat error: %s", e, exc_info=True)
        # LLM quota exhausted — try fallback
        if "AllocationQuota" in err_msg or "403" in err_msg or "free tier" in err_msg.lower():
            from app.services.copilot.agent import reset_agent
            reset_agent()
            raise HTTPException(status_code=503, detail="当前模型额度已耗尽，已切换到备选模型，请重试")
        raise HTTPException(status_code=500, detail=f"智能助手执行失败: {err_msg[:200]}")


@router.post("/chat/stream")
async def copilot_chat_stream(payload: CopilotChatRequest):
    """Copilot 对话（SSE 流式）— 支持 Supervisor 多 Agent 模式。"""
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    thread_id = payload.thread_id or f"cp_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    _touch_session(thread_id)

    async def event_generator():
        try:
            if payload.use_supervisor:
                async for chunk in _supervisor_stream(payload.message, thread_id):
                    yield chunk
            else:
                async for chunk in _single_agent_stream(payload.message, thread_id):
                    yield chunk
        except Exception as e:
            err_msg = str(e)
            logger.error("Copilot stream error: %s", e, exc_info=True)

            if "AllocationQuota" in err_msg or "403" in err_msg or "free tier" in err_msg.lower():
                logger.warning("LLM quota exhausted, resetting agent to try fallback LLM")
                from app.services.copilot.agent import reset_agent, reset_supervisor
                reset_agent()
                reset_supervisor()
                data = json.dumps({
                    "type": "error",
                    "message": "当前模型额度已耗尽，已切换到备选模型。请重新发送消息。",
                    "retry": True,
                }, ensure_ascii=False)
            else:
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
    """单 Agent SSE 流式生成器。"""
    from app.services.copilot.agent import get_compiled_agent, reset_agent

    agent = get_compiled_agent()
    config = {"configurable": {"thread_id": thread_id}}
    input_msg = {"messages": [HumanMessage(content=message)]}

    try:
        async for event in agent.astream_events(input_msg, config=config, version="v2"):
            kind = event.get("event", "")

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    text = ""
                    if isinstance(chunk.content, str):
                        text = chunk.content
                    elif isinstance(chunk.content, list):
                        for part in chunk.content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text += part.get("text", "")
                            elif isinstance(part, str):
                                text += part
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
        err_msg = str(e)
        if "AllocationQuota" in err_msg or "403" in err_msg:
            reset_agent()
        raise


async def _supervisor_stream(message: str, thread_id: str):
    """Supervisor 多 Agent SSE 流式生成器。

    推送事件类型：
    - task_plan: 任务分解结果
    - worker_switch: Worker 切换
    - task_update: 任务状态变更
    - token: 文本 token
    - tool_start / tool_end: 工具调用
    - done: 完成
    """
    from app.services.copilot.agent.supervisor import get_supervisor_graph, reset_supervisor
    from langgraph.graph.state import CompiledStateGraph

    graph = get_supervisor_graph()
    config = {"configurable": {"thread_id": f"sv_{thread_id}"}}
    input_msg = {
        "messages": [HumanMessage(content=message)],
        "tasks": [],
        "next_worker": None,
    }

    try:
        async for event in graph.astream_events(input_msg, config=config, version="v2"):
            kind = event.get("event", "")
            tags = event.get("tags", [])
            node_name = ""

            # Identify which node generated this event
            for tag in tags:
                if tag.startswith("langgraph:nodes:"):
                    node_name = tag.split(":")[-1]
                    break

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    text = ""
                    if isinstance(chunk.content, str):
                        text = chunk.content
                    elif isinstance(chunk.content, list):
                        for part in chunk.content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text += part.get("text", "")
                            elif isinstance(part, str):
                                text += part
                    if text:
                        data = json.dumps({"type": "token", "content": text, "node": node_name}, ensure_ascii=False)
                        yield f"event: token\ndata: {data}\n\n"

            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                data = json.dumps({"type": "tool_start", "tool": tool_name, "worker": node_name}, ensure_ascii=False)
                yield f"event: tool_start\ndata: {data}\n\n"

            elif kind == "on_tool_end":
                tool_name = event.get("name", "")
                raw_output = event.get("data", {}).get("output", "")
                if hasattr(raw_output, "content"):
                    result_str = str(raw_output.content)
                else:
                    result_str = str(raw_output)
                result_preview = result_str.replace("\n", " ")[:200]
                data = json.dumps({"type": "tool_end", "tool": tool_name, "result": result_preview, "worker": node_name}, ensure_ascii=False)
                yield f"event: tool_end\ndata: {data}\n\n"

            # Emit worker_switch when we enter a worker node
            elif kind == "on_chain_start" and node_name in ("researcher", "analyst", "manager", "supervisor"):
                data = json.dumps({"type": "worker_switch", "worker": node_name}, ensure_ascii=False)
                yield f"event: worker_switch\ndata: {data}\n\n"

        yield f"event: done\ndata: {json.dumps({'type': 'done', 'thread_id': thread_id, 'mode': 'supervisor'}, ensure_ascii=False)}\n\n"
        _touch_session(thread_id)

    except Exception as e:
        err_msg = str(e)
        if "AllocationQuota" in err_msg or "403" in err_msg:
            reset_supervisor()
        raise


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
