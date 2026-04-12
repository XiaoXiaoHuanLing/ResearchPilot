"""AI Chat service with web search capability — V2 Optimized.

Key improvements over V1:
1. Better web-search trigger logic (structured signals, not fragile string matching)
2. Rich citation output matching new RAG engine metadata
3. Query intent classification before mode selection
4. Context-aware prompt engineering for LLM synthesis
5. Streaming-ready structure (for future SSE support)

Three modes:
1. "search": Web search only
2. "knowledge": Local RAG knowledge base only (optionally specific KB)
3. "hybrid": Auto-decide, combine web + local (default)
"""

import logging
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from app.core.config import settings
from app.services.ingestion import search_web, fetch_page, extract_article
from app.services.rag.engine import rag_query

logger = logging.getLogger(__name__)


# --- Intent Classification ---

# Keywords indicating the user needs fresh/real-time information
FRESHNESS_KEYWORDS = [
    "最新", "最近", "当前", "今天", "近期", "最新动态", "最新进展",
    "有什么新", "有什么变化", "现在", "目前", "刚刚", "最新消息",
    "今天发生了", "近期有没有", "最近有什么",
]

# Signals from RAG answer indicating insufficient local knowledge
INSUFFICIENT_SIGNALS = [
    "没有足够的", "无法回答", "知识库中没有", "暂无", "未找到",
    "不足以", "缺少", "我无法", "不知道", "没有相关信息",
    "知识库中暂无", "未包含",
]


def _classify_query_intent(question: str) -> dict:
    """Classify the query intent for smarter routing.
    
    Returns: {
        needs_fresh_info: bool,  # User explicitly asks for current info
        is_factual: bool,       # Factual question (good for KB lookup)
        is_analytical: bool,    # Analysis/synthesis question (needs LLM)
        keywords: list[str],    # Extracted key terms
    }
    """
    q = question.lower()
    needs_fresh = any(kw in q for kw in FRESHNESS_KEYWORDS)
    is_factual = any(w in q for w in ["是什么", "什么是", "有哪些", "多少", "是谁", "在哪", "哪个"])
    is_analytical = any(w in q for w in ["分析", "比较", "区别", "趋势", "影响", "原因", "如何", "为什么", "评估", "总结"])
    
    # Simple keyword extraction (split on common delimiters)
    import re
    keywords = [w for w in re.split(r"[，。？、\s,?\s]+", question) if len(w) >= 2][:5]
    
    return {
        "needs_fresh_info": needs_fresh,
        "is_factual": is_factual,
        "is_analytical": is_analytical,
        "keywords": keywords,
    }


def _should_search_web(question: str, rag_answer: str, intent: dict | None = None) -> bool:
    """Determine if web search is needed based on question and RAG answer quality."""
    if intent is None:
        intent = _classify_query_intent(question)
    
    # User explicitly asks for fresh/current info
    if intent["needs_fresh_info"]:
        return True
    
    # RAG answer signals insufficient local knowledge
    rag_insufficient = any(sig in rag_answer for sig in INSUFFICIENT_SIGNALS)
    if rag_insufficient:
        return True
    
    return False


def _get_llm() -> ChatOpenAI | None:
    """Get a configured ChatOpenAI instance, or None if not configured.
    
    Priority: Alibaba qwen (fast, reliable) > proxy gpt (slow, unstable).
    Uses streaming=True to work around proxy APIs that return content=None
    in non-streaming mode. LangChain collects all chunks and returns full content.
    """
    # Prefer Alibaba qwen (much faster for chat)
    if settings.alibaba_api_key and settings.alibaba_base_url:
        return ChatOpenAI(
            model=settings.alibaba_model_name or "qwen3.5-flash",
            api_key=settings.alibaba_api_key,
            base_url=settings.alibaba_base_url,
            streaming=True,
            temperature=0.3, max_tokens=2000, timeout=60,
        )
    # Fallback to proxy
    if settings.openai_api_key:
        return ChatOpenAI(
            model=settings.llm_model, api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            streaming=True,
            temperature=0.3, max_tokens=2000, timeout=120,
        )
    return None


# --- Chat Modes ---

async def chat_with_search(
    question: str,
    mode: str = "hybrid",
    knowledge_base_id: int | None = None,
    force_search: bool = False,
    chat_history: list[dict] | None = None,
) -> dict:
    """Main chat entry point with mode selection.
    
    Args:
        question: User's question
        mode: "search" | "knowledge" | "hybrid"
        knowledge_base_id: Restrict RAG to specific KB (knowledge mode)
        force_search: Force web search even if local KB seems sufficient
        chat_history: Previous messages [{role, content}, ...] for multi-turn
    
    Returns: {question, answer, citations, search_used, new_articles, mode}
    """
    if not settings.openai_api_key:
        return await _fallback_chat(question, mode)

    intent = _classify_query_intent(question)

    # Mode: search only
    if mode == "search":
        return await _search_only(question, intent, chat_history)

    # Mode: knowledge base only
    if mode == "knowledge":
        return await _knowledge_only(question, knowledge_base_id, intent, chat_history)

    # Mode: hybrid (default)
    return await _hybrid_chat(question, intent, force_search, chat_history)


async def _search_only(question: str, intent: dict, chat_history: list[dict] | None = None) -> dict:
    """Web search only mode."""
    if not (settings.tavily_api_key or settings.serper_api_key):
        return {
            "question": question,
            "answer": "未配置搜索引擎，无法进行联网搜索。",
            "citations": [],
            "search_used": False,
            "new_articles": 0,
            "mode": "search",
        }

    search_results = await search_web(question, max_results=5)
    web_citations = [
        {"title": r.get("title", ""), "source": r.get("source", ""),
         "url": r.get("url", ""), "relevance_score": None,
         "snippet": r.get("snippet", ""),
         "published_at": "", "topic": ""}
        for r in search_results
    ]

    # LLM synthesis
    llm = _get_llm()
    if llm:
        try:
            messages = _build_messages(
                system_prompt="你是 ResearchPilot 研究助手。基于互联网搜索结果回答问题，引用来源，标注信息时效性。",
                question=question,
                context=web_citations,
                context_label="互联网搜索结果",
                chat_history=chat_history,
            )
            response = llm.invoke(messages)
            answer = response.content
        except Exception as e:
            logger.error("LLM failed in search mode: %s", e)
            answer = _format_raw_citations(web_citations)
    else:
        answer = _format_raw_citations(web_citations)

    return {"question": question, "answer": answer, "citations": web_citations,
            "search_used": True, "new_articles": 0, "mode": "search"}


async def _knowledge_only(
    question: str,
    knowledge_base_id: int | None = None,
    intent: dict | None = None,
    chat_history: list[dict] | None = None,
) -> dict:
    """Knowledge base RAG only mode."""
    from app.db.models import KnowledgeBaseModel
    from app.db.session import SessionLocal

    kb_type = None
    if knowledge_base_id is not None:
        with SessionLocal() as db:
            kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == knowledge_base_id).first()
            if kb:
                kb_type = kb.kb_type

    # Use HyDE for analytical questions in knowledge mode
    use_hyde = intent is not None and intent.get("is_analytical", False)
    rag_result = await rag_query(question, top_k=5, kb_id=knowledge_base_id, kb_type=kb_type, use_hyde=use_hyde)
    
    return {
        "question": question,
        "answer": rag_result.get("answer", ""),
        "citations": rag_result.get("citations", []),
        "search_used": False,
        "new_articles": 0,
        "mode": "knowledge",
    }


async def _hybrid_chat(
    question: str,
    intent: dict,
    force_search: bool = False,
    chat_history: list[dict] | None = None,
) -> dict:
    """Hybrid mode: RAG + web search in parallel, LLM synthesis.

    Pipeline (optimized):
    1. Classify intent
    2. Run RAG query and web search **in parallel** (asyncio.gather)
    3. Build citations from both sources
    4. LLM synthesis combining both sources
    5. Auto-ingest search results **in background** (non-blocking)
    """
    import asyncio

    # Step 1: Decide if web search is needed (based on intent alone)
    needs_search = force_search or intent.get("needs_fresh_info", False)

    # Step 2: Run RAG + search in parallel
    use_hyde = intent.get("is_analytical", False)

    async def _do_rag():
        try:
            # retrieval_only=True: skip LLM answer generation in RAG,
            # we'll do a single LLM call in hybrid synthesis instead
            return await rag_query(question, top_k=5, use_hyde=use_hyde, retrieval_only=True)
        except Exception as e:
            logger.error("RAG query failed in hybrid: %s", e)
            return {"answer": "", "citations": [], "context_block": ""}

    async def _do_search():
        if not needs_search or not (settings.tavily_api_key or settings.serper_api_key):
            return [], False
        try:
            results = await search_web(question, max_results=5)
            return results, True
        except Exception as e:
            logger.error("Web search failed in hybrid: %s", e)
            return [], False

    rag_result, (search_results, search_used) = await asyncio.gather(_do_rag(), _do_search())

    rag_answer = rag_result.get("answer", "")
    rag_citations = rag_result.get("citations", [])
    rag_context = rag_result.get("context_block", "")

    # Step 3: If search was not needed and RAG is sufficient, return directly
    if not search_used:
        # Re-check: if RAG has no useful results, we still need search
        rag_has_results = len(rag_citations) > 0 and bool(rag_context or rag_answer)
        rag_insufficient_answer = rag_answer and any(s in rag_answer for s in INSUFFICIENT_SIGNALS)
        needs_search_after_rag = not rag_has_results or rag_insufficient_answer

        if not needs_search_after_rag:
            # Need to generate RAG answer if we skipped it in retrieval_only mode
            if not rag_answer and rag_context:
                llm = _get_llm()
                if llm:
                    try:
                        msgs = _build_messages(
                            system_prompt="你是 ResearchPilot 研究助手。基于提供的参考资料回答问题。引用来源，如有多条参考，标注各信息出处。如果参考资料不足以回答问题，请如实说明。",
                            question=question,
                            context=[{"source": c.get("source",""), "title": c.get("title",""), "snippet": c.get("snippet","")} for c in rag_citations],
                            context_label="本地知识库",
                        )
                        resp = llm.invoke(msgs)
                        rag_answer = resp.content
                    except Exception as e:
                        logger.error("RAG answer gen failed: %s", e)
                        rag_answer = "（LLM 生成失败，请参考下方引用来源）"
            return {
                "question": question, "answer": rag_answer, "citations": rag_citations,
                "search_used": False, "new_articles": 0, "mode": "hybrid",
            }
        # RAG insufficient → do search now
        try:
            search_results = await search_web(question, max_results=5)
            search_used = True
        except Exception as e:
            logger.error("Web search failed after RAG insufficient: %s", e)
            # Return whatever RAG gave us
            if not rag_answer and rag_context:
                rag_answer = "本地知识库信息不足，联网搜索也失败。请参考下方引用。"
            return {
                "question": question, "answer": rag_answer, "citations": rag_citations,
                "search_used": False, "new_articles": 0, "mode": "hybrid",
            }

    # Build web citations
    web_citations = [
        {
            "title": r.get("title", "未知标题"),
            "source": r.get("source", "未知来源"),
            "url": r.get("url", ""), "relevance_score": None,
            "snippet": r.get("snippet", ""),
            "published_at": "", "topic": "",
        }
        for r in search_results
    ]

    # Step 4: LLM synthesis (single LLM call combining RAG context + web results)
    llm = _get_llm()
    if llm:
        try:
            # Use RAG context_block directly if available (from retrieval_only mode)
            # Otherwise check if RAG answer signals insufficient
            if rag_context:
                local_ctx = rag_context
            elif rag_answer:
                insufficient = any(s in rag_answer for s in INSUFFICIENT_SIGNALS)
                local_ctx = rag_answer if not insufficient else "本地知识库中暂无相关记录。"
            else:
                local_ctx = "本地知识库中暂无相关记录。"

            messages = _build_messages(
                system_prompt=(
                    "你是 ResearchPilot 研究助手，结合本地知识库和互联网最新资讯回答。"
                    "引用来源，区分本地与网络信息。标注信息的时效性。"
                    "如果本地和网络信息有冲突，以更新的网络信息为准并说明。"
                ),
                question=question,
                local_context=local_ctx,
                web_context=web_citations,
                chat_history=chat_history,
            )
            response = llm.invoke(messages)
            final_answer = response.content
        except Exception as e:
            logger.error("LLM combination failed: %s", e)
            final_answer = (rag_answer or "") + "\n\n--- 互联网搜索补充 ---\n\n" + _format_raw_citations(web_citations)
    else:
        final_answer = (rag_answer or "") + "\n\n--- 互联网搜索补充 ---\n\n" + _format_raw_citations(web_citations)

    # Step 5: Auto-ingest in background (non-blocking) — fire and forget
    new_article_count = 0  # Will be 0 in response; actual ingestion happens async
    if search_results:
        async def _bg_ingest():
            count = 0
            for result in search_results[:2]:  # Limit to 2 to avoid overload
                url = result.get("url", "")
                if not url:
                    continue
                try:
                    from app.services.ingestion import ingest_url
                    article = await ingest_url(url=url, topic_name="对话采集", auto_bookmark=False)
                    if article is not None:
                        count += 1
                except Exception:
                    pass
            if count > 0:
                logger.info("Background ingest: %d articles from hybrid chat", count)

        try:
            asyncio.create_task(_bg_ingest())
        except Exception:
            pass  # Best effort

    return {
        "question": question, "answer": final_answer,
        "citations": rag_citations + web_citations,
        "search_used": True, "new_articles": new_article_count, "mode": "hybrid",
    }


# --- Message Building Helpers ---

def _build_messages(
    system_prompt: str,
    question: str,
    context: list[dict] | None = None,
    context_label: str = "参考资料",
    local_context: str | None = None,
    web_context: list[dict] | None = None,
    chat_history: list[dict] | None = None,
) -> list:
    """Build LangChain message list with proper structure.
    
    Handles both single-context (search/knowledge mode) and
    dual-context (hybrid mode with local + web) scenarios.
    """
    messages = [SystemMessage(content=system_prompt)]
    
    # Add chat history for multi-turn context
    if chat_history:
        for msg in chat_history[-6:]:  # Keep last 6 messages for context window
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
    
    # Build user message with context
    user_parts = [f"问题：{question}"]
    
    if local_context is not None and web_context is not None:
        # Hybrid mode: dual context
        user_parts.append(f"\n\n--- 本地知识库 ---\n{local_context}")
        web_ctx = "\n\n".join(
            f"[{c['source']}] {c['title']}\n{c.get('snippet', '')}" for c in web_context
        )
        user_parts.append(f"\n\n--- 互联网搜索 ---\n{web_ctx}")
    elif context is not None:
        # Single context mode
        ctx = "\n\n".join(
            f"[{c.get('source', '')}] {c.get('title', '')}\n{c.get('snippet', '')}" for c in context
        )
        user_parts.append(f"\n\n--- {context_label} ---\n{ctx}")
    
    messages.append(HumanMessage(content="".join(user_parts)))
    return messages


def _format_raw_citations(citations: list[dict]) -> str:
    """Format citations as readable text when LLM is unavailable."""
    return "\n\n".join(
        f"[{c.get('source', '')}] {c.get('title', '')}: {c.get('snippet', '')}" for c in citations
    )


async def _fallback_chat(question: str, mode: str) -> dict:
    """Fallback when no LLM is configured."""
    from app.services.rag.engine import _fallback_db_query
    result = await _fallback_db_query(question)
    return {
        "question": question, "answer": result["answer"],
        "citations": result["citations"], "search_used": False,
        "new_articles": 0, "mode": mode,
    }
