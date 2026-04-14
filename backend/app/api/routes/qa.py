from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import ArticleModel
from app.db.session import get_db

router = APIRouter()


class QARequest(BaseModel):
    question: str


class ChatRequest(BaseModel):
    question: str
    mode: str = "hybrid"  # "search" | "knowledge" | "hybrid"
    knowledge_base_id: int | None = None  # For knowledge mode: specify KB
    force_search: bool = False  # Force web search even if local KB seems sufficient
    session_id: int | None = None  # Chat session ID for persistence and multi-turn context


@router.post("/query")
async def query_qa(payload: QARequest, db: Session = Depends(get_db)):
    """Traditional RAG-powered Q&A endpoint (local knowledge base only)."""
    from app.core.config import settings

    if settings.embedding_configured:
        from app.services.rag.engine import rag_query
        return await rag_query(payload.question, top_k=5)
    else:
        keywords = [w for w in payload.question.replace("？", "").replace("?", "").split() if len(w) > 1]

        query = db.query(ArticleModel)
        if keywords:
            from sqlalchemy import or_
            pattern = "%".join(keywords[:5])
            query = query.filter(
                (ArticleModel.title.ilike(f"%{pattern}%"))
                | (ArticleModel.summary.ilike(f"%{pattern}%"))
                | (ArticleModel.content.ilike(f"%{pattern}%"))
            )

        articles = query.order_by(ArticleModel.id.desc()).limit(5).all()
        citations = [
            {
                "article_id": article.id,
                "title": article.title,
                "source": article.source,
                "url": article.url,
                "snippet": article.summary[:300],
            }
            for article in articles
        ]

        return {
            "question": payload.question,
            "answer": f"（未配置 LLM API，当前为关键词检索模式）\n\n检索到 {len(articles)} 条相关资讯。配置 DASHSCOPE_API_KEY 后将启用真实 RAG 问答。",
            "citations": citations,
        }


@router.post("/chat")
async def chat(payload: ChatRequest):
    """Intelligent chat with multiple modes.

    Modes:
    - "search": Web search only, no local KB
    - "knowledge": Local knowledge base RAG only, optionally specify a KB
    - "hybrid": Auto-decide, combine web + local (default)

    knowledge_base_id: When mode is "knowledge", restrict RAG to a specific KB.
    session_id: When provided, persist messages and use chat history for context.
    """
    from app.services.chat import chat_with_search

    # Load chat history if session_id provided
    chat_history = None
    if payload.session_id is not None:
        from app.db.session import SessionLocal
        from app.db.models.chat import ChatMessageModel
        with SessionLocal() as db:
            messages = db.query(ChatMessageModel).filter(
                ChatMessageModel.session_id == payload.session_id
            ).order_by(ChatMessageModel.id.asc()).limit(12).all()
            if messages:
                chat_history = [{"role": m.role, "content": m.content} for m in messages]

    result = await chat_with_search(
        question=payload.question,
        mode=payload.mode,
        knowledge_base_id=payload.knowledge_base_id,
        force_search=payload.force_search,
        chat_history=chat_history,
    )

    # Persist messages if session_id provided
    if payload.session_id is not None:
        _persist_chat_messages(payload.session_id, payload.question, result, payload.mode)

    return result


def _persist_chat_messages(session_id: int, question: str, result: dict, mode: str):
    """Save user question and assistant response to chat session."""
    from app.db.session import SessionLocal
    from app.db.models.chat import ChatSessionModel, ChatMessageModel
    import json
    from datetime import datetime, timezone

    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        with SessionLocal() as db:
            session = db.query(ChatSessionModel).filter(ChatSessionModel.id == session_id).first()
            if session is None:
                return

            # Save user message
            user_msg = ChatMessageModel(
                session_id=session_id, role="user", content=question,
                mode=mode, search_used=False, citations_json="[]", created_at=now,
            )
            db.add(user_msg)

            # Save assistant message
            citations = result.get("citations", [])
            serializable_citations = []
            for c in citations:
                sc = {}
                for k, v in c.items():
                    if v is None:
                        sc[k] = None
                    elif isinstance(v, (str, int, float, bool)):
                        sc[k] = v
                    else:
                        sc[k] = str(v)
                serializable_citations.append(sc)

            asst_msg = ChatMessageModel(
                session_id=session_id, role="assistant", content=result.get("answer", ""),
                mode=result.get("mode", mode),
                search_used=result.get("search_used", False),
                citations_json=json.dumps(serializable_citations, ensure_ascii=False),
                created_at=now,
            )
            db.add(asst_msg)

            # Update session
            session.updated_at = now
            if session.title == "新对话":
                session.title = question[:50] + ("..." if len(question) > 50 else "")

            db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to persist chat messages: %s", e)
