"""智能对话API — 统一入口，DeepAgents一主二从。

废除 search/knowledge/hybrid 三模式，Agent自主决策。
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


# ─── DB 会话持久化辅助 ───

def _ensure_db_session(db_session_id: int | None, session_id: str, user_msg: str) -> int:
    """确保数据库会话存在，不存在则创建。返回 db_session_id。"""
    from app.db.session import SessionLocal
    from app.db.models.chat import ChatSessionModel

    with SessionLocal() as db:
        if db_session_id:
            session = db.query(ChatSessionModel).filter(ChatSessionModel.id == db_session_id).first()
            if session:
                return db_session_id

        # 创建新会话
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        title = user_msg[:50] + ("..." if len(user_msg) > 50 else "")
        session = ChatSessionModel(
            title=title, mode="agent", created_at=now, updated_at=now,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session.id


def _save_message_to_db(db_session_id: int, role: str, content: str, search_used: bool = False, citations: list = None):
    """保存一条消息到数据库。"""
    from app.db.session import SessionLocal
    from app.db.models.chat import ChatSessionModel, ChatMessageModel

    with SessionLocal() as db:
        session = db.query(ChatSessionModel).filter(ChatSessionModel.id == db_session_id).first()
        if not session:
            return

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        msg = ChatMessageModel(
            session_id=db_session_id,
            role=role,
            content=content,
            mode="agent",
            search_used=search_used,
            citations_json=json.dumps(citations or [], ensure_ascii=False),
            created_at=now,
        )
        db.add(msg)
        session.updated_at = now
        db.commit()


# ─── Schemas ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    db_session_id: int | None = None  # 数据库会话ID，用于历史恢复


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict] = []
    search_used: bool = False
    kb_used: bool = False
    session_id: str = ""
    db_session_id: int | None = None


# ─── SSE Helpers ──────────────────────────────────────────────

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


# ─── Endpoints ────────────────────────────────────────────────

@router.post("/query", response_model=ChatResponse)
async def chat_query(payload: ChatRequest):
    """智能对话（非流式）。Agent自主决定走搜索/KB/两者。"""
    from app.services.chat.agent import get_chat_agent

    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    session_id = payload.session_id or f"chat_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    # 确保 DB 会话存在 + 保存用户消息
    db_sid = _ensure_db_session(payload.db_session_id, session_id, payload.message)
    _save_message_to_db(db_sid, "user", payload.message)

    # 设置 thread_id 到工具上下文（供 RecallPool/SearchPool 使用）
    from app.services.chat.tools.thread_context import set_current_thread_id
    set_current_thread_id(session_id)

    # 新对话轮次：清空上一轮的 RecallPool 和 SearchPool
    # 同一 session_id 的连续对话，每轮应有独立的检索结果
    from app.services.chat.tools.reflexive_retriever import RecallPoolManager
    from app.services.chat.tools.search import SearchPoolManager
    RecallPoolManager(session_id).reset_for_new_turn()
    SearchPoolManager(session_id).reset_for_new_turn()

    agent = get_chat_agent()
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 20}
    input_msg = {"messages": [HumanMessage(content=payload.message)]}

    final_content = ""
    search_used = False
    kb_used = False

    try:
        async for event in agent.astream_events(input_msg, config=config, version="v2"):
            kind = event.get("event", "")

            if kind == "on_chat_model_end":
                output = event.get("data", {}).get("output", {})
                if hasattr(output, "content") and output.content:
                    has_tool_calls = hasattr(output, "tool_calls") and output.tool_calls
                    if not has_tool_calls:
                        final_content = output.content

            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                if tool_name in ("search_web", "fetch_page", "get_search_content"):
                    search_used = True
                if tool_name in ("search_knowledge", "get_recall_nodes", "rerank_recall_pool", "list_active_kbs"):
                    kb_used = True

        # 保存助手回复到 DB
        _save_message_to_db(db_sid, "assistant", final_content, search_used=search_used)

        return ChatResponse(
            answer=final_content,
            search_used=search_used,
            kb_used=kb_used,
            session_id=session_id,
            db_session_id=db_sid,
        )
    except Exception as e:
        err_msg = str(e)[:200]
        if "AllocationQuota" in err_msg or "free tier" in err_msg:
            raise HTTPException(status_code=503, detail="LLM免费额度已耗尽，请在控制台关闭'仅使用免费额度'或切换付费模式")
        logger.error("Chat query error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"对话执行失败: {err_msg}")


@router.post("/stream")
async def chat_stream(payload: ChatRequest):
    """智能对话（SSE流式）。Agent自主决策，逐token推送。"""
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    session_id = payload.session_id or f"chat_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    # 确保 DB 会话存在 + 保存用户消息
    db_sid = _ensure_db_session(payload.db_session_id, session_id, payload.message)
    _save_message_to_db(db_sid, "user", payload.message)

    async def event_generator():
        try:
            # 设置 thread_id 到工具上下文
            from app.services.chat.tools.thread_context import set_current_thread_id
            set_current_thread_id(session_id)

            # 新对话轮次：清空上一轮的 RecallPool 和 SearchPool
            from app.services.chat.tools.reflexive_retriever import RecallPoolManager
            from app.services.chat.tools.search import SearchPoolManager
            RecallPoolManager(session_id).reset_for_new_turn()
            SearchPoolManager(session_id).reset_for_new_turn()

            from app.services.chat.agent import get_chat_agent
            agent = get_chat_agent()
            config = {"configurable": {"thread_id": session_id}, "recursion_limit": 20}
            input_msg = {"messages": [HumanMessage(content=payload.message)]}

            search_used = False
            kb_used = False
            full_answer = ""  # 累积完整助手回复

            async for event in agent.astream_events(input_msg, config=config, version="v2"):
                kind = event.get("event", "")
                agent_name = event.get("metadata", {}).get("lc_agent_name", "main")

                # 主Agent的LLM输出token
                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        text = _extract_chunk_text(chunk.content)
                        if text and agent_name == "main":
                            full_answer += text
                            data = json.dumps({"type": "token", "content": text}, ensure_ascii=False)
                            yield f"event: token\ndata: {data}\n\n"

                # 工具开始
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "")
                    if tool_name in ("search_web", "fetch_page", "get_search_content"):
                        search_used = True
                        data = json.dumps({"type": "agent_start", "agent": "searcher", "task": tool_name}, ensure_ascii=False)
                        yield f"event: agent_start\ndata: {data}\n\n"
                    elif tool_name in ("search_knowledge", "get_recall_nodes", "rerank_recall_pool", "list_active_kbs"):
                        kb_used = True
                        data = json.dumps({"type": "agent_start", "agent": "retriever", "task": tool_name}, ensure_ascii=False)
                        yield f"event: agent_start\ndata: {data}\n\n"
                    else:
                        data = json.dumps({"type": "tool_start", "tool": tool_name}, ensure_ascii=False)
                        yield f"event: tool_start\ndata: {data}\n\n"

                # 工具结束
                elif kind == "on_tool_end":
                    tool_name = event.get("name", "")
                    raw_output = event.get("data", {}).get("output", "")
                    result_str = str(raw_output.content if hasattr(raw_output, "content") else raw_output)
                    result_preview = result_str.replace("\n", " ")[:200]
                    data = json.dumps({"type": "tool_end", "tool": tool_name, "result": result_preview}, ensure_ascii=False)
                    yield f"event: tool_end\ndata: {data}\n\n"

                # Sub Agent委托
                elif kind == "on_tool_start" and event.get("name") == "task":
                    tool_input = event.get("data", {}).get("input", {})
                    data = json.dumps({
                        "type": "delegate",
                        "agent": tool_input.get("subagent_type", "?"),
                        "task": tool_input.get("description", "")[:200],
                    }, ensure_ascii=False)
                    yield f"event: delegate\ndata: {data}\n\n"

                # Interrupt 事件
                elif kind == "on_interrupt":
                    interrupt_data = event.get("data", {})
                    tool_name = interrupt_data.get("tool_name", "unknown")
                    tool_input = interrupt_data.get("input", {})
                    data = json.dumps({
                        "type": "interrupt",
                        "tool": tool_name,
                        "input": tool_input,
                        "message": f"Agent 请求执行 {tool_name}，等待审批",
                    }, ensure_ascii=False)
                    yield f"event: interrupt\ndata: {data}\n\n"

            done_data = json.dumps({
                "type": "done",
                "session_id": session_id,
                "db_session_id": db_sid,
                "search_used": search_used,
                "kb_used": kb_used,
            }, ensure_ascii=False)
            yield f"event: done\ndata: {done_data}\n\n"

            # 保存助手回复到 DB（异步，不阻塞 SSE）
            _save_message_to_db(db_sid, "assistant", full_answer, search_used=search_used)

        except Exception as e:
            err_msg = str(e)[:300]
            logger.error("Chat stream error: %s", e, exc_info=True)
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


# ─── 保留旧接口兼容 ──────────────────────────────────────────

@router.post("/chat")
async def legacy_chat(payload: ChatRequest):
    """兼容旧 /api/chat/chat 接口，重定向到新逻辑"""
    return await chat_query(payload)


# ─── Interrupt 审批 ──────────────────────────────────────────

class InterruptApproveRequest(BaseModel):
    approved: bool = True
    modified_input: dict | None = None  # 用户修改后的工具输入


@router.post("/interrupt/{thread_id}/approve")
async def approve_interrupt(thread_id: str, payload: InterruptApproveRequest):
    """审批 Agent 的 interrupt 请求。

    当 Agent 遇到 interrupt_on 工具（如 save_memory）时，
    会暂停执行并发出 interrupt 事件。
    前端收到后展示审批对话框，用户同意/拒绝/修改参数。

    Args:
        thread_id: 会话ID
        payload: 审批决定（approved=True 继续，False 拒绝）
    """
    from app.services.chat.agent import get_chat_agent

    try:
        agent = get_chat_agent()
        config = {"configurable": {"thread_id": thread_id}}

        # 获取当前 Checkpointer state
        from langgraph.checkpoint.base import CheckpointTuple
        checkpointer = agent.checkpointer if hasattr(agent, "checkpointer") else None

        if not checkpointer:
            raise HTTPException(status_code=500, detail="Checkpointer not available")

        checkpoint = await checkpointer.aget_tuple(config)
        if not checkpoint or not checkpoint.pending:
            raise HTTPException(status_code=404, detail=f"No pending interrupt for thread {thread_id}")

        # 恢复执行
        if payload.approved:
            # 同意：继续执行工具
            input_data = payload.modified_input if payload.modified_input else None
            # 用 Command(resume=True) 恢复
            from langgraph.types import Command
            resume_command = Command(resume=input_data or True)

            result_content = ""
            async for event in agent.astream_events(resume_command, config=config, version="v2"):
                kind = event.get("event", "")
                if kind == "on_chat_model_end":
                    output = event.get("data", {}).get("output", {})
                    if hasattr(output, "content") and output.content:
                        result_content = output.content

            return {"status": "approved", "result": result_content[:500] if result_content else "ok"}
        else:
            # 拒绝：恢复执行但返回拒绝消息
            from langgraph.types import Command
            reject_command = Command(resume=f"用户拒绝了此操作。请告知用户并继续对话。")

            result_content = ""
            async for event in agent.astream_events(reject_command, config=config, version="v2"):
                kind = event.get("event", "")
                if kind == "on_chat_model_end":
                    output = event.get("data", {}).get("output", {})
                    if hasattr(output, "content") and output.content:
                        result_content = output.content

            return {"status": "rejected", "result": result_content[:500] if result_content else "ok"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Interrupt approve error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"审批处理失败: {str(e)[:200]}")
