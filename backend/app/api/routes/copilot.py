"""智能助手API — 统一Deep Agent模式。

废除 single_agent / supervisor 模式，只保留 deep_agent。
一主六从：searcher/collector/retriever/kb_manager/topic_manager/report_manager
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────

class CopilotChatRequest(BaseModel):
    message: str
    thread_id: str | None = None
    # 不再有 use_supervisor / use_deep_agent


class CopilotChatResponse(BaseModel):
    response: str
    tool_log: list[dict] = []
    thread_id: str
    mode: str = "deep_agent"


class CopilotSessionInfo(BaseModel):
    thread_id: str
    message_count: int = 0
    last_active: str = ""


# ─── Helpers ──────────────────────────────────────────────────

def _extract_chunk_text(content) -> str:
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


# ─── Session Index ───

_session_index: dict[str, dict] = {}


def _touch_session(thread_id: str) -> None:
    _session_index[thread_id] = {
        "thread_id": thread_id,
        "last_active": datetime.now(timezone.utc).isoformat(),
    }


# ─── Sub Agent Names ───

SUB_AGENT_NAMES = {"searcher", "collector", "retriever", "kb_manager", "topic_manager", "report_manager"}


# ─── Endpoints ──────────────────────────────────────────────

@router.post("/chat", response_model=CopilotChatResponse)
async def copilot_chat(payload: CopilotChatRequest):
    """智能助手对话（非流式）。统一Deep Agent模式。"""
    from app.services.copilot.agent import get_deep_agent
    from app.services.copilot.memory import set_current_context, clear_current_context

    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    thread_id = payload.thread_id or f"cp_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    _touch_session(thread_id)

    agent = get_deep_agent()
    config = {"configurable": {"thread_id": f"da_{thread_id}"}, "recursion_limit": 25}

    set_current_context(user_id=thread_id, session_id=thread_id)

    try:
        input_msg = {"messages": [HumanMessage(content=payload.message)]}

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

        _touch_session(thread_id)
        return CopilotChatResponse(
            response=final_content,
            tool_log=tool_log,
            thread_id=thread_id,
            mode="deep_agent",
        )
    except Exception as e:
        err_msg = str(e)[:200]
        if "AllocationQuota" in err_msg or "free tier" in err_msg:
            raise HTTPException(status_code=503, detail="LLM免费额度已耗尽，请在控制台关闭'仅使用免费额度'或切换付费模式")
        logger.error("Copilot chat error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"智能助手执行失败: {err_msg}")
    finally:
        clear_current_context()


@router.post("/chat/stream")
async def copilot_chat_stream(payload: CopilotChatRequest):
    """智能助手对话（SSE流式）。统一Deep Agent模式。"""
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    thread_id = payload.thread_id or f"cp_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    _touch_session(thread_id)

    async def event_generator():
        try:
            from app.services.copilot.agent import get_deep_agent
            from app.services.copilot.memory import set_current_context, clear_current_context

            agent = get_deep_agent()
            config = {"configurable": {"thread_id": f"da_{thread_id}"}, "recursion_limit": 25}

            set_current_context(user_id=thread_id, session_id=thread_id)

            try:
                input_msg = {"messages": [HumanMessage(content=payload.message)]}

                async for event in agent.astream_events(input_msg, config=config, version="v2"):
                    kind = event.get("event", "")
                    agent_name = event.get("metadata", {}).get("lc_agent_name", "main")

                    # 主Agent LLM输出token
                    if kind == "on_chat_model_stream":
                        chunk = event.get("data", {}).get("chunk")
                        if chunk and hasattr(chunk, "content") and chunk.content:
                            text = _extract_chunk_text(chunk.content)
                            if text and agent_name == "main":
                                data = json.dumps({"type": "token", "content": text}, ensure_ascii=False)
                                yield f"event: token\ndata: {data}\n\n"

                    # 工具调用开始
                    elif kind == "on_tool_start":
                        tool_name = event.get("name", "")
                        tool_input = event.get("data", {}).get("input", {})

                        if tool_name == "task":
                            # Sub Agent委派
                            sub_type = tool_input.get("subagent_type", "?")
                            task_desc = tool_input.get("description", "")[:200]
                            data = json.dumps({
                                "type": "delegate",
                                "agent": sub_type,
                                "task": task_desc,
                            }, ensure_ascii=False)
                            yield f"event: delegate\ndata: {data}\n\n"
                        elif tool_name == "write_todos":
                            data = json.dumps({"type": "plan", "todos": tool_input.get("todos", [])}, ensure_ascii=False)
                            yield f"event: plan\ndata: {data}\n\n"
                        else:
                            # Sub Agent内部工具
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
                            result_str = str(raw_output.content if hasattr(raw_output, "content") else raw_output)
                            preview = result_str.replace("\n", " ")[:200]
                            data = json.dumps({"type": "delegate_done", "preview": preview}, ensure_ascii=False)
                            yield f"event: delegate_done\ndata: {data}\n\n"
                        elif tool_name == "write_todos":
                            data = json.dumps({"type": "plan_updated", "status": "updated"}, ensure_ascii=False)
                            yield f"event: plan_updated\ndata: {data}\n\n"
                        else:
                            result_str = str(raw_output.content if hasattr(raw_output, "content") else raw_output)
                            result_preview = result_str.replace("\n", " ")[:100]
                            data = json.dumps({
                                "type": "sub_tool_end",
                                "tool": tool_name,
                                "result": result_preview,
                                "agent": agent_name,
                            }, ensure_ascii=False)
                            yield f"event: sub_tool_end\ndata: {data}\n\n"

                done_data = json.dumps({
                    "type": "done",
                    "thread_id": thread_id,
                    "mode": "deep_agent",
                }, ensure_ascii=False)
                yield f"event: done\ndata: {done_data}\n\n"
                _touch_session(thread_id)

            finally:
                clear_current_context()

        except Exception as e:
            err_msg = str(e)[:300]
            logger.error("Copilot stream error: %s", e, exc_info=True)
            data = json.dumps({"type": "error", "message": err_msg}, ensure_ascii=False)
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


@router.get("/sessions", response_model=list[CopilotSessionInfo])
async def list_sessions():
    sessions = []
    for tid, info in _session_index.items():
        sessions.append(CopilotSessionInfo(thread_id=tid, last_active=info.get("last_active", "")))
    sessions.sort(key=lambda s: s.last_active, reverse=True)
    return sessions[:50]


@router.delete("/sessions/{thread_id}", status_code=204)
async def delete_session(thread_id: str):
    _session_index.pop(thread_id, None)


@router.get("/tools")
async def list_tools():
    from app.services.copilot.tools import get_all_tools
    all_tools = get_all_tools()
    tools_info = [{"name": t.name, "description": (t.description or "")[:200]} for t in all_tools]
    return {"tools": tools_info, "count": len(tools_info)}
