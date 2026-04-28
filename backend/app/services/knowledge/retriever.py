"""RAG混合检索服务 — Dense + BM25 → RRF → AutoMerging。

默认启用AutoMerging：小chunk精确检索 + 自动合并补足上下文。
检索器缓存：入库/删除后刷新，查询时复用，避免每次重建。
"""

import logging
import time

from app.core.config import settings

logger = logging.getLogger(__name__)

# ─── 检索器缓存 ───
_retriever_cache: dict | None = None
_retriever_cache_kb_ids: tuple | None = None
_retriever_cache_params: dict | None = None


def _get_cached_retriever(kb_ids: list[int], auto_merging_thresh: float = 0.5):
    """获取缓存的混合检索器（同一组 kb_ids + 参数 复用）"""
    global _retriever_cache, _retriever_cache_kb_ids, _retriever_cache_params

    kb_tuple = tuple(sorted(kb_ids))
    params = {"thresh": auto_merging_thresh}
    if (
        _retriever_cache is not None
        and _retriever_cache_kb_ids == kb_tuple
        and _retriever_cache_params == params
    ):
        return _retriever_cache

    result = _build_hybrid_retriever(kb_ids, auto_merging_thresh=auto_merging_thresh)
    if result is not None:
        _retriever_cache = result
        _retriever_cache_kb_ids = kb_tuple
        _retriever_cache_params = params
    return result


def invalidate_retriever_cache():
    """入库/删除后刷新检索器缓存"""
    global _retriever_cache, _retriever_cache_kb_ids, _retriever_cache_params
    _retriever_cache = None
    _retriever_cache_kb_ids = None
    _retriever_cache_params = None
    logger.info("Retriever cache invalidated")


def _build_hybrid_retriever(kb_ids: list[int], auto_merging_thresh: float = 0.5) -> dict | None:
    """构建完整的混合检索器（Dense + BM25 → RRF → AutoMerging）"""
    try:
        from llama_index.core.retrievers import QueryFusionRetriever, AutoMergingRetriever
        from llama_index.core import StorageContext
        from llama_index.core.vector_stores import MetadataFilter, MetadataFilters
        from app.services.knowledge.engine import _get_index
        from app.services.knowledge.bm25 import get_cached_bm25_retriever
        from app.services.knowledge.docstore import get_docstore

        idx = _get_index()
        if idx is None:
            return None

        # KB 过滤（单KB用 MetadataFilter，多KB走二次过滤）
        kb_filters = None
        if len(kb_ids) == 1:
            kb_filters = MetadataFilters(filters=[MetadataFilter(key="kb_id", value=kb_ids[0])])

        vector_retriever = idx.as_retriever(similarity_top_k=12, filters=kb_filters)

        bm25_retriever = get_cached_bm25_retriever(similarity_top_k=12)
        if bm25_retriever is None:
            return None

        # RRF融合（倒排排名融合，无需配置权重）
        fusion_retriever = QueryFusionRetriever(
            retrievers=[vector_retriever, bm25_retriever],
            num_queries=1,
            similarity_top_k=20,
            mode="reciprocal_rerank",
        )

        # AutoMerging
        docstore = get_docstore()
        storage_context = StorageContext.from_defaults(docstore=docstore)

        auto_merging = AutoMergingRetriever(
            fusion_retriever,
            storage_context=storage_context,
            simple_ratio_thresh=auto_merging_thresh,
        )

        return {
            "auto_merging_retriever": auto_merging,
            "kb_ids": kb_ids,
            "needs_post_filter": len(kb_ids) > 1,
        }
    except Exception as e:
        logger.error("Failed to build hybrid retriever: %s", e)
        return None


async def hybrid_retrieve(
    query: str,
    kb_ids: list[int] | None = None,
    top_k: int = 5,
    use_hyde: bool = False,
    retrieval_mode: str = "hybrid",
    auto_merging_thresh: float = 0.5,
) -> dict:
    """混合检索：Dense + BM25 → RRF融合 → AutoMerging合并

    Args:
        query: 查询问题
        kb_ids: 限定的KB ID列表（None=全部启用的KB）
        top_k: 最终返回的top-k数量
        use_hyde: 是否使用HyDE查询改写
        retrieval_mode: "hybrid" 走混合检索, "dense" 走纯Dense
        auto_merging_thresh: AutoMerging 合并阈值 (0.0~1.0)

    Returns:
        {query, citations, retrieval_mode, latency_ms, eval_meta}
    """
    start_time = time.time()

    target_kb_ids = kb_ids or _get_active_kb_ids()
    if not target_kb_ids:
        return {
            "query": query, "citations": [], "retrieval_mode": "none",
            "latency_ms": 0, "eval_meta": {"error": "no_active_kbs"},
        }

    # 纯 Dense 模式直接走降级路径
    if retrieval_mode == "dense":
        logger.info("Forced dense-only retrieval mode")
        result = await _dense_only_retrieve(query, target_kb_ids, top_k, use_hyde)
        latency_ms = int((time.time() - start_time) * 1000)
        result["latency_ms"] = latency_ms
        result["eval_meta"]["latency_ms"] = latency_ms
        return result

    # 1. 尝试混合检索
    result = _try_hybrid_retrieve(query, target_kb_ids, top_k, auto_merging_thresh)

    if result is not None:
        latency_ms = int((time.time() - start_time) * 1000)
        result["latency_ms"] = latency_ms
        result["eval_meta"]["latency_ms"] = latency_ms
        return result

    # 2. 降级：纯Dense检索
    logger.info("Hybrid retrieval unavailable, falling back to dense-only")
    result = await _dense_only_retrieve(query, target_kb_ids, top_k, use_hyde)
    latency_ms = int((time.time() - start_time) * 1000)
    result["latency_ms"] = latency_ms
    result["eval_meta"]["latency_ms"] = latency_ms
    return result


def _try_hybrid_retrieve(
    query: str,
    kb_ids: list[int],
    top_k: int,
    auto_merging_thresh: float = 0.5,
) -> dict | None:
    """使用缓存的混合检索器执行检索"""
    cached = _get_cached_retriever(kb_ids, auto_merging_thresh=auto_merging_thresh)
    if cached is None:
        return None

    retriever = cached["auto_merging_retriever"]

    try:
        source_nodes = retriever.retrieve(query)
    except Exception as e:
        logger.error("Hybrid retrieval execute failed: %s", e)
        # 缓存可能过期，清掉重建一次
        invalidate_retriever_cache()
        cached = _get_cached_retriever(kb_ids, auto_merging_thresh=auto_merging_thresh)
        if cached is None:
            return None
        retriever = cached["auto_merging_retriever"]
        source_nodes = retriever.retrieve(query)

    # 二次过滤：多KB时确保结果属于目标KB
    if cached.get("needs_post_filter"):
        filtered_nodes = []
        for sn in source_nodes:
            meta = sn.node.metadata or {}
            if meta.get("kb_id") in kb_ids:
                filtered_nodes.append(sn)
        source_nodes = filtered_nodes

    citations = _build_citations(source_nodes, top_k)

    return {
        "query": query,
        "citations": citations,
        "retrieval_mode": "hybrid_auto_merging",
        "eval_meta": {
            "retrieval_mode": "hybrid_auto_merging",
            "raw_retrieved": len(source_nodes),
            "final_citations": len(citations),
            "kb_ids": kb_ids,
        },
    }


async def _dense_only_retrieve(
    query: str,
    kb_ids: list[int],
    top_k: int,
    use_hyde: bool = False,
) -> dict:
    """降级：纯Dense检索"""
    from app.services.knowledge.engine import rag_query

    # 单KB直接查，多KB合并查
    if len(kb_ids) == 1:
        result = await rag_query(
            query, top_k=top_k, kb_id=kb_ids[0],
            use_hyde=use_hyde, retrieval_only=True,
        )
        citations = result.get("citations", [])
    else:
        all_citations = []
        for kb_id in kb_ids:
            result = await rag_query(
                query, top_k=top_k, kb_id=kb_id,
                use_hyde=use_hyde, retrieval_only=True,
            )
            all_citations.extend(result.get("citations", []))
        all_citations.sort(key=lambda c: c.get("relevance_score", 0) or 0, reverse=True)
        citations = all_citations[:top_k]

    return {
        "query": query,
        "citations": citations,
        "retrieval_mode": "dense_only",
        "eval_meta": {
            "retrieval_mode": "dense_only",
            "use_hyde": use_hyde,
            "kb_ids": kb_ids,
            "final_citations": len(citations),
        },
    }


def _build_citations(source_nodes: list, top_k: int) -> list[dict]:
    """从检索结果构建citations"""
    citations = []
    for node in source_nodes:
        score = float(node.score) if node.score is not None else 0.0
        metadata = node.node.metadata or {}

        citations.append({
            "node_id": node.node.node_id,
            "kb_doc_id": metadata.get("kb_doc_id"),
            "title": metadata.get("title", ""),
            "source": metadata.get("source", ""),
            "source_type": metadata.get("source_type", ""),
            "kb_id": metadata.get("kb_id"),
            "relevance_score": score,
            "snippet": node.node.text[:300] if node.node.text else "",
        })

    citations.sort(key=lambda c: c.get("relevance_score", 0) or 0, reverse=True)
    return citations[:top_k]


def _get_active_kb_ids() -> list[int]:
    """查询所有启用的知识库ID"""
    from app.db.session import SessionLocal
    from app.db.models import KnowledgeBaseModel

    try:
        with SessionLocal() as db:
            kbs = db.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.enabled == True  # noqa
            ).all()
            return [kb.id for kb in kbs] if kbs else []
    except Exception:
        return []
