"""RAG engine — V2: 向量库管理 + 查询。

入库链路已统一走 indexer.py（解析→SmartNodeParser→向量库+docstore+BM25）。
本模块仅负责：
- ChromaDB 连接管理
- 向量检索（rag_query）
- 向量删除（delete_doc_vectors, delete_kb_from_index）
- 统计信息
"""

import logging
from typing import Any, Optional

import chromadb
from llama_index.core import VectorStoreIndex, StorageContext, Settings as LlamaSettings
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.vector_stores import MetadataFilter, MetadataFilters
from llama_index.vector_stores.chroma import ChromaVectorStore
from openai import OpenAI
from pydantic import PrivateAttr

from app.core.config import settings

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "researchpilot_all"

# --- RAG Configuration ---
SIMILARITY_TOP_K = 10
FINAL_TOP_K = 5
MIN_RELEVANCE_SCORE = 0.3


# --- Custom Embedding (supports DashScope models) ---

class CompatibleOpenAIEmbedding(BaseEmbedding):
    """Custom embedding that bypasses LlamaIndex model name whitelist."""

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


def _configure_llama_settings() -> None:
    global _embed_model
    if settings.embedding_configured:
        LlamaSettings.llm = None
        _embed_model = CompatibleOpenAIEmbedding(
            model=settings.embedding_model, api_key=settings.embedding_api_key,
            api_base=settings.embedding_base_url,
        )
        LlamaSettings.embed_model = _embed_model
    else:
        LlamaSettings.llm = None
        LlamaSettings.embed_model = None


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
    if not settings.embedding_configured:
        return None
    if _index is None:
        vs = _get_vector_store()
        sc = StorageContext.from_defaults(vector_store=vs)
        _index = VectorStoreIndex.from_vector_store(vs, storage_context=sc)
    return _index


def _invalidate_index() -> None:
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
        }
    except Exception as e:
        return {"error": str(e)}


# ─── 向量删除 ──────────────────────────────────────────────────

async def delete_doc_vectors(kb_doc_id: int, kb_id: int) -> int:
    """从 ChromaDB 删除指定文档的所有向量（按 kb_doc_id metadata 匹配）

    Args:
        kb_doc_id: KbDocumentModel.id
        kb_id: 所属知识库ID

    Returns:
        删除后剩余向量数
    """
    if not settings.embedding_configured:
        return 0
    try:
        client = _get_chroma_client()
        collection = client.get_or_create_collection(_COLLECTION_NAME)
        # 按 kb_doc_id metadata 精确删除（kb_doc_id = KbDocumentModel.id 整数）
        collection.delete(where={"kb_doc_id": kb_doc_id})
        _invalidate_index()
        count = collection.count()
        logger.info("Deleted vectors for kb_doc_id=%d (remaining: %d)", kb_doc_id, count)
        return count
    except Exception as e:
        logger.warning("Failed to delete doc %d vectors: %s", kb_doc_id, e)
        return 0


async def delete_kb_from_index(kb_id: int) -> None:
    """Delete all documents belonging to a specific KB from the vector store."""
    if not settings.embedding_configured:
        return
    try:
        client = _get_chroma_client()
        collection = client.get_or_create_collection(_COLLECTION_NAME)
        collection.delete(where={"kb_id": kb_id})
        _invalidate_index()
        logger.info("Deleted all documents with kb_id=%d from collection", kb_id)
    except Exception as e:
        logger.warning("Failed to delete KB %d from index: %s", kb_id, e)# ─── RAG 查询 ──────────────────────────────────────────────────

async def rag_query(
    question: str, top_k: int = FINAL_TOP_K,
    kb_id: int | None = None,
    use_hyde: bool = False, retrieval_only: bool = False,
) -> dict:
    """Run a RAG query with retrieval pipeline.

    Pipeline: Query -> (optional HyDE) -> Dense retrieval (top_k=10) -> Score filtering -> top 5

    - kb_id specified: filter by metadata kb_id (只搜启用的KB)
    - use_hyde: generate hypothetical document embedding for better recall
    - retrieval_only: skip LLM answer generation, return citations only

    Returns: {question, answer, citations, eval_meta}
    """
    if not settings.embedding_configured:
        return await _fallback_db_query(question)

    try:
        idx = _get_index()
        if idx is None:
            return {"question": question, "answer": "RAG 索引不可用。", "citations": []}

        client = _get_chroma_client()
        collection = client.get_or_create_collection(_COLLECTION_NAME)
        if collection.count() == 0:
            return {"question": question, "answer": "知识库中暂无索引文档。", "citations": []}

        # Build filters: 按 kb_id 过滤（只搜启用的KB）
        filters = None
        if kb_id is not None:
            filters = MetadataFilters(filters=[MetadataFilter(key="kb_id", value=kb_id)])

        # Optional HyDE
        query_str = question
        if use_hyde and settings.llm_configured:
            try:
                query_str = await _generate_hyde(question)
            except Exception as e:
                logger.warning("HyDE generation failed: %s", e)
                query_str = question

        logger.info("RAG query: question=%s, kb_id=%s, use_hyde=%s", question[:50], kb_id, use_hyde)
        retriever = idx.as_retriever(
            similarity_top_k=SIMILARITY_TOP_K,
            filters=filters,
        )
        source_nodes = retriever.retrieve(query_str)
        logger.info("RAG query: retrieved %d nodes", len(source_nodes))

        # Build citations with score filtering
        citations = []
        context_texts = []
        for node in source_nodes:
            score = float(node.score) if node.score is not None else 0.0
            if score < MIN_RELEVANCE_SCORE:
                continue

            metadata = node.node.metadata or {}
            citations.append({
                "doc_id": metadata.get("doc_id"),
                "title": metadata.get("title", "未知标题"),
                "source": metadata.get("source", ""),
                "source_type": metadata.get("source_type", ""),
                "kb_id": metadata.get("kb_id"),
                "relevance_score": score,
                "snippet": node.node.text[:300] if node.node.text else "",
            })
            context_texts.append(
                f"[来源：{metadata.get('source', '未知')}] {metadata.get('title', '未知标题')}\n{node.node.text[:600]}"
            )

        citations.sort(key=lambda c: c.get("relevance_score", 0) or 0, reverse=True)
        citations = citations[:top_k]

        if retrieval_only:
            context_block = "\n\n---\n\n".join(context_texts[:top_k])
            return {
                "question": question, "answer": "", "citations": citations,
                "context_block": context_block,
            }

        # Generate answer using LangChain
        answer = ""
        if context_texts and settings.llm_configured:
            try:
                from langchain_openai import ChatOpenAI
                from langchain_core.messages import SystemMessage, HumanMessage

                llm = ChatOpenAI(
                    model=settings.llm_model, api_key=settings.llm_api_key,
                    base_url=settings.llm_base_url,
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
            except Exception as e:
                logger.error("LangChain RAG answer generation failed: %s", e)
                answer = "（LLM 生成失败，请参考下方引用来源）"
        elif not context_texts:
            answer = "未找到相关参考资料。"

        return {
            "question": question,
            "answer": answer,
            "citations": citations,
        }

    except Exception as e:
        logger.error("RAG query failed: %s", e)
        return await _fallback_db_query(question)


async def _generate_hyde(question: str) -> str:
    """Generate a Hypothetical Document Embedding (HyDE)."""
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
            {"doc_id": a.id, "title": a.title, "source": a.source,
             "relevance_score": None, "snippet": a.summary[:300]}
            for a in articles
        ]
        return {"question": question, "answer": f"（关键词检索）检索到 {len(articles)} 条。", "citations": citations}
