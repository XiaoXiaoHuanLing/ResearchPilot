"""RAG engine using LlamaIndex + ChromaDB — V2 Optimized.

Key improvements over V1:
1. Semantic chunking via SentenceSplitter (respects sentence boundaries)
2. Rich metadata: source, url, published_at, topic carried into vector store
3. Separated concerns: prepare_doc / insert_doc / delete_doc
4. Index status tracking via DB field (article.rag_indexed, kb_document.indexed)
5. Batch reindex with progress logging
6. Hybrid retrieval: dense vector + metadata filtering
7. Reranking via similarity score threshold filtering
8. Query transformation (HyDE) for better recall
9. Structured citation output with full provenance
10. Fallback DB keyword search preserved

Single collection + metadata design remains:
- metadata field "kb_id": identifies which KB a document belongs to
- metadata field "kb_type": "bookmarks" or "upload"
- Cross-KB query: single query, unified ranking
- Per-KB query: metadata filter on kb_id
"""

import logging
from typing import Any, Optional

import chromadb
from llama_index.core import VectorStoreIndex, StorageContext, Settings as LlamaSettings, Document
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.vector_stores import MetadataFilter, MetadataFilters
# Note: LlamaOpenAI is not used — we use LangChain ChatOpenAI instead
# (LlamaIndex's LLM wrapper has a model name whitelist that rejects non-OpenAI models)
from llama_index.vector_stores.chroma import ChromaVectorStore
from openai import OpenAI
from pydantic import PrivateAttr

from app.core.config import settings

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "researchpilot_all"

# --- RAG Configuration ---
CHUNK_SIZE = 512           # Smaller chunks for better precision
CHUNK_OVERLAP = 80         # ~15% overlap for context continuity
SIMILARITY_TOP_K = 10      # Retrieve candidates for reranking (reduced from 20)
FINAL_TOP_K = 5            # Final results after score filtering
MIN_RELEVANCE_SCORE = 0.3  # Minimum similarity score to include


# --- Custom Embedding (supports Alibaba models) ---

class CompatibleOpenAIEmbedding(BaseEmbedding):
    """Custom embedding that bypasses LlamaIndex model name whitelist.
    
    Supports Alibaba text-embedding-v3 and other non-standard models
    by using the OpenAI client directly.
    """
    _client: Any = PrivateAttr()
    _model: str = PrivateAttr()

    def __init__(self, model: str, api_key: str, api_base: str, **kwargs):
        super().__init__(model_name=model, **kwargs)
        self._client = OpenAI(api_key=api_key, base_url=api_base or None)
        self._model = model

    def _get_query_embedding(self, query: str) -> list[float]:
        resp = self._client.embeddings.create(model=self._model, input=[query])
        return resp.data[0].embedding

    def _get_text_embedding(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(model=self._model, input=[text])
        return resp.data[0].embedding

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        results = []
        for i in range(0, len(texts), 20):
            batch = texts[i:i + 20]
            resp = self._client.embeddings.create(model=self._model, input=batch)
            results.extend([d.embedding for d in resp.data])
        return results

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return self._get_text_embedding(text)


# --- LlamaIndex config ---

_embed_model: Any = None
_node_parser: SentenceSplitter | None = None


def _configure_llama_settings() -> None:
    global _embed_model, _node_parser
    if settings.embedding_configured:
        # LlamaIndex LLM is NOT used for query answer generation anymore.
        # We use LangChain ChatOpenAI instead (see rag_query), because:
        # 1. LlamaIndex OpenAI wrapper has a model name whitelist that rejects non-OpenAI models
        # 2. Proxy APIs return content=None in non-streaming mode
        # LlamaSettings.llm is set to None — only embedding + retriever are used.
        LlamaSettings.llm = None
        _embed_model = CompatibleOpenAIEmbedding(
            model=settings.embedding_model, api_key=settings.embedding_api_key,
            api_base=settings.embedding_base_url,
        )
        LlamaSettings.embed_model = _embed_model
    else:
        LlamaSettings.llm = None
        LlamaSettings.embed_model = None
    
    # Semantic chunking: SentenceSplitter respects sentence boundaries
    _node_parser = SentenceSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        paragraph_separator="\n\n",
    )
    LlamaSettings.chunk_size = CHUNK_SIZE
    LlamaSettings.chunk_overlap = CHUNK_OVERLAP


_configure_llama_settings()


# --- ChromaDB single collection ---

_chroma_client: Any = None
_vector_store: Any = None
_index: Any = None


def _get_chroma_client() -> Any:
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    return _chroma_client


def _get_vector_store() -> ChromaVectorStore:
    global _vector_store
    if _vector_store is None:
        client = _get_chroma_client()
        collection = client.get_or_create_collection(_COLLECTION_NAME)
        _vector_store = ChromaVectorStore(chroma_collection=collection)
    return _vector_store


def _get_index() -> Optional[VectorStoreIndex]:
    global _index
    if not settings.llm_configured:
        return None
    if _index is None:
        vs = _get_vector_store()
        sc = StorageContext.from_defaults(vector_store=vs)
        _index = VectorStoreIndex.from_vector_store(vs, storage_context=sc)
    return _index


def _invalidate_index() -> None:
    """Invalidate the cached index after data changes."""
    global _index
    _index = None


def get_collection_stats() -> dict:
    """Get ChromaDB collection statistics."""
    try:
        client = _get_chroma_client()
        collection = client.get_or_create_collection(_COLLECTION_NAME)
        count = collection.count()
        return {
            "collection_name": _COLLECTION_NAME,
            "total_chunks": count,
            "embedding_model": settings.embedding_model,
            "chunk_size": CHUNK_SIZE,
            "chunk_overlap": CHUNK_OVERLAP,
        }
    except Exception as e:
        return {"error": str(e)}


# --- Document ID helpers ---

def _doc_id(article_id: int, kb_type: str = "bookmarks") -> str:
    """Generate a unique doc_id that encodes kb_type to avoid collisions."""
    if kb_type == "upload":
        return f"kbdoc_{article_id}"
    return f"article_{article_id}"


# --- Public API: Document Preparation ---

def prepare_document(
    article_id: int,
    title: str,
    content: str,
    kb_id: int | None = None,
    kb_type: str = "bookmarks",
    source: str = "",
    url: str = "",
    published_at: str = "",
    topic: str = "",
) -> Document:
    """Prepare a LlamaIndex Document with rich metadata.
    
    This is a pure function — no side effects, no indexing.
    Separates document creation from insertion for testability.
    """
    text = f"标题：{title}\n\n{content}"
    doc_id = _doc_id(article_id, kb_type)
    
    metadata = {
        "article_id": article_id,
        "title": title,
        "kb_id": kb_id or 0,
        "kb_type": kb_type,
        # Rich metadata for citation provenance
        "source": source,
        "url": url,
        "published_at": published_at,
        "topic": topic,
    }
    
    return Document(text=text, doc_id=doc_id, metadata=metadata)


# --- Public API: Index Operations ---

async def index_article(
    article_id: int, title: str, content: str,
    kb_id: int | None = None, kb_type: str = "bookmarks",
    source: str = "", url: str = "", published_at: str = "", topic: str = "",
) -> int:
    """Index a document into the single collection with rich metadata.

    Uses SentenceSplitter for semantic chunking.
    
    Args:
        article_id: Document ID (negative for KB uploads)
        title: Document title
        content: Full text content (will be semantically chunked)
        kb_id: Knowledge base ID (0 for default bookmarks)
        kb_type: "bookmarks" or "upload"
        source: Content source (domain name)
        url: Original URL
        published_at: Publication date string
        topic: Topic name

    Returns: number of chunks created, or 0 on failure.
    """
    if not settings.llm_configured:
        logger.warning("No API key configured, skipping index for %s", _doc_id(article_id, kb_type))
        return 0

    try:
        idx = _get_index()
        if idx is None:
            return 0

        doc = prepare_document(
            article_id=article_id, title=title, content=content,
            kb_id=kb_id, kb_type=kb_type,
            source=source, url=url, published_at=published_at, topic=topic,
        )

        # Delete existing doc first (for re-indexing / idempotency)
        try:
            idx.delete_ref_doc(doc.doc_id)
        except Exception:
            pass

        # Count chunks before and after
        client = _get_chroma_client()
        collection = client.get_or_create_collection(_COLLECTION_NAME)
        count_before = collection.count()

        # Insert with semantic chunking
        idx.insert(doc)

        count_after = collection.count()
        chunks_created = count_after - count_before

        logger.info(
            "Indexed %s (kb=%s, kb_id=%s, source=%s) → %d chunks",
            doc.doc_id, kb_type, kb_id, source, chunks_created,
        )
        return chunks_created

    except Exception as e:
        logger.error("Failed to index doc %s: %s", _doc_id(article_id, kb_type), e)
        return 0


# --- Public API: Query ---

async def rag_query(
    question: str, top_k: int = FINAL_TOP_K,
    kb_id: int | None = None, kb_type: str | None = None,
    use_hyde: bool = False, retrieval_only: bool = False,
) -> dict:
    """Run a RAG query with improved retrieval pipeline.

    Pipeline: Query → (optional HyDE) → Dense retrieval (top_k=10) → Score filtering → top 5
    
    - No filter: query all documents in the single collection
    - kb_id specified: filter by metadata kb_id
    - kb_type specified: filter by metadata kb_type
    - use_hyde: generate hypothetical document embedding for better recall
    - retrieval_only: skip LLM answer generation, return citations only (for hybrid mode)
    
    Returns: {question, answer, citations: [{article_id, title, source, url, published_at, relevance_score, snippet}]}
    """
    if not settings.llm_configured:
        return await _fallback_db_query(question)

    try:
        idx = _get_index()
        if idx is None:
            return {"question": question, "answer": "RAG 索引不可用。", "citations": []}

        # Check document count
        client = _get_chroma_client()
        collection = client.get_or_create_collection(_COLLECTION_NAME)
        if collection.count() == 0:
            return {"question": question, "answer": "知识库中暂无索引文档。", "citations": []}

        # Build filters if needed
        filters = None
        if kb_id is not None or kb_type is not None:
            filter_list = []
            if kb_id is not None:
                filter_list.append(MetadataFilter(key="kb_id", value=kb_id))
            if kb_type is not None:
                filter_list.append(MetadataFilter(key="kb_type", value=kb_type))
            filters = MetadataFilters(filters=filter_list)

        # Optional: HyDE (Hypothetical Document Embeddings) for better recall
        query_str = question
        if use_hyde and settings.llm_configured:
            try:
                query_str = await _generate_hyde(question)
            except Exception as e:
                logger.warning("HyDE generation failed, using original query: %s", e)
                query_str = question

        # Retrieve with higher top_k for reranking
        # NOTE: Use as_retriever() instead of as_query_engine() because:
        # 1. LlamaIndex's LLM has model name whitelist that rejects non-OpenAI models
        # 2. Proxy APIs (e.g. gpt-5.4 via tunnel) return content=None in non-streaming mode
        # So we do retrieval-only with LlamaIndex, then generate answer with LangChain
        logger.info("RAG query: question=%s, kb_id=%s, use_hyde=%s", question[:50], kb_id, use_hyde)
        retriever = idx.as_retriever(
            similarity_top_k=SIMILARITY_TOP_K,
            filters=filters,
        )
        source_nodes = retriever.retrieve(query_str)
        logger.info("RAG query: retrieved %d nodes", len(source_nodes))

        # Build citations with score filtering (simple reranking)
        citations = []
        context_texts = []
        for node in source_nodes:
            score = float(node.score) if node.score is not None else 0.0
            # Filter by minimum relevance score
            if score < MIN_RELEVANCE_SCORE:
                continue
            
            metadata = node.node.metadata or {}
            citations.append({
                "article_id": metadata.get("article_id"),
                "title": metadata.get("title", "未知标题"),
                "source": metadata.get("source", ""),
                "url": metadata.get("url", ""),
                "published_at": metadata.get("published_at", ""),
                "topic": metadata.get("topic", ""),
                "relevance_score": score,
                "snippet": node.node.text[:300] if node.node.text else "",
            })
            # Collect context for LLM answer generation
            context_texts.append(
                f"[来源：{metadata.get('source', '未知')}] {metadata.get('title', '未知标题')}\n{node.node.text[:600]}"
            )

        # Sort by relevance score (descending) and take top_k
        citations.sort(key=lambda c: c.get("relevance_score", 0) or 0, reverse=True)
        citations = citations[:top_k]

        # If retrieval_only mode, skip LLM answer generation (used by hybrid mode)
        if retrieval_only:
            context_block = "\n\n---\n\n".join(context_texts[:top_k])
            return {"question": question, "answer": "", "citations": citations, "context_block": context_block}

        # Generate answer using LangChain (bypasses LlamaIndex model whitelist issue)
        answer = ""
        if context_texts and settings.llm_configured:
            try:
                from langchain_openai import ChatOpenAI
                from langchain_core.messages import SystemMessage, HumanMessage

                # Use unified OpenAI-compatible config for RAG answer generation
                rag_llm_model = settings.llm_model
                rag_llm_key = settings.llm_api_key
                rag_llm_base = settings.llm_base_url

                logger.info(
                    "RAG answer gen: model=%s, base=%s, key=%s..., ctx=%d",
                    rag_llm_model, rag_llm_base, rag_llm_key[:10] if rag_llm_key else "NONE", len(context_texts),
                )

                llm = ChatOpenAI(
                    model=rag_llm_model, api_key=rag_llm_key,
                    base_url=rag_llm_base,
                    streaming=True,
                    temperature=0.3, max_tokens=1500, timeout=60,
                )
                context_block = "\n\n---\n\n".join(context_texts[:top_k])
                messages = [
                    SystemMessage(content=(
                        "你是 ResearchPilot 研究助手。基于提供的参考资料回答问题。"
                        "引用来源，如有多条参考，标注各信息出处。"
                        "如果参考资料不足以回答问题，请如实说明。"
                    )),
                    HumanMessage(content=f"问题：{question}\n\n参考资料：\n{context_block}"),
                ]
                llm_response = llm.invoke(messages)
                answer = llm_response.content or ""
                logger.info("RAG answer generated: len=%d, preview=%s", len(answer), repr(answer[:100]))
            except Exception as e:
                logger.error("LangChain RAG answer generation failed: %s", e)
                answer = "（LLM 生成失败，请参考下方引用来源）"
        elif not context_texts:
            answer = "未找到相关参考资料。"

        return {"question": question, "answer": answer, "citations": citations}

    except Exception as e:
        logger.error("RAG query failed: %s", e)
        return await _fallback_db_query(question)


async def _generate_hyde(question: str) -> str:
    """Generate a Hypothetical Document Embedding (HyDE).
    
    Instead of embedding the question directly, we first ask the LLM to
    generate a hypothetical answer, then embed that answer. This often
    produces better retrieval because the hypothetical answer's embedding
    is closer to the actual document embeddings than the question's.
    """
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage
    
    llm = ChatOpenAI(
        model=settings.llm_model, api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        streaming=True,
        temperature=0.0, max_tokens=500, timeout=30,
    )
    response = llm.invoke([
        SystemMessage(content="请用2-3句话简要回答以下问题。即使不确定也要给出一个合理的假设性回答。"),
        HumanMessage(content=question),
    ])
    return response.content


async def _fallback_db_query(question: str) -> dict:
    """Fallback: keyword-based search when vector search is unavailable."""
    from app.db.session import SessionLocal
    from app.db.models import ArticleModel
    from sqlalchemy import or_

    with SessionLocal() as db:
        keywords = [w for w in question.replace("？", "").replace("?", "").split() if len(w) > 1]
        query = db.query(ArticleModel)
        if keywords:
            pattern = "%".join(keywords[:5])
            query = query.filter(
                ArticleModel.title.ilike(f"%{pattern}%")
                | ArticleModel.summary.ilike(f"%{pattern}%")
                | ArticleModel.content.ilike(f"%{pattern}%")
            )
        articles = query.order_by(ArticleModel.id.desc()).limit(5).all()
        citations = [
            {"article_id": a.id, "title": a.title, "source": a.source, "url": a.url,
             "published_at": a.published_at, "topic": a.topic,
             "relevance_score": None, "snippet": a.summary[:300]}
            for a in articles
        ]
        return {"question": question, "answer": f"（关键词检索）检索到 {len(articles)} 条。", "citations": citations}


# --- Public API: Batch Operations ---

async def reindex_all_articles(db) -> dict:
    """Re-index all bookmarked articles with rich metadata.
    
    Returns: {total, success, failed, details}
    """
    from app.db.models import ArticleModel
    
    articles = db.query(ArticleModel).filter(ArticleModel.bookmarked == True).all()  # noqa: E712
    results = {"total": len(articles), "success": 0, "failed": 0, "details": []}
    
    logger.info("Starting reindex of %d bookmarked articles", len(articles))
    
    for i, article in enumerate(articles):
        try:
            chunks = await index_article(
                article_id=article.id,
                title=article.title,
                content=article.content,
                kb_id=0,
                kb_type="bookmarks",
                source=article.source,
                url=article.url,
                published_at=article.published_at,
                topic=article.topic,
            )
            if chunks > 0:
                results["success"] += 1
                results["details"].append({"id": article.id, "title": article.title, "chunks": chunks, "status": "ok"})
            else:
                results["failed"] += 1
                results["details"].append({"id": article.id, "title": article.title, "chunks": 0, "status": "no_chunks"})
        except Exception as e:
            results["failed"] += 1
            results["details"].append({"id": article.id, "title": article.title, "error": str(e), "status": "error"})
            logger.warning("Failed to re-index article %d: %s", article.id, e)
        
        # Progress log every 5 articles
        if (i + 1) % 5 == 0:
            logger.info("Reindex progress: %d/%d", i + 1, len(articles))
    
    logger.info("Reindex complete: %d success, %d failed out of %d", results["success"], results["failed"], results["total"])
    return results


async def delete_article_from_index(article_id: int, kb_id: int | None = None, kb_type: str = "bookmarks") -> None:
    """Remove a document from the vector store."""
    if not settings.llm_configured:
        return
    try:
        idx = _get_index()
        if idx is not None:
            doc_id = _doc_id(article_id, kb_type)
            idx.delete_ref_doc(doc_id)
    except Exception as e:
        logger.warning("Failed to delete %s from index: %s", _doc_id(article_id, kb_type), e)


async def delete_kb_from_index(kb_id: int) -> None:
    """Delete all documents belonging to a specific upload KB.

    Uses ChromaDB's native where filter to delete in bulk.
    """
    if not settings.llm_configured:
        return
    try:
        client = _get_chroma_client()
        collection = client.get_or_create_collection(_COLLECTION_NAME)
        collection.delete(where={"kb_id": kb_id})
        _invalidate_index()
        logger.info("Deleted all documents with kb_id=%d from collection", kb_id)
    except Exception as e:
        logger.warning("Failed to delete KB %d from index: %s", kb_id, e)
