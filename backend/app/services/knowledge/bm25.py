"""BM25 检索节点加载 + 缓存管理。

BM25Retriever 需要叶子节点在内存中。
启动时主动从 docstore 预加载，入库/删除后延迟刷新（避免查询中间态降级）。
"""

import logging

logger = logging.getLogger(__name__)

_leaf_nodes_cache = None
_bm25_retriever_cache = None


def load_leaf_nodes_for_bm25() -> list:
    """加载叶子节点到内存（BM25用）

    Returns:
        叶子节点列表
    """
    global _leaf_nodes_cache

    if _leaf_nodes_cache is not None:
        return _leaf_nodes_cache

    from app.services.knowledge.docstore import get_leaf_nodes_from_docstore
    _leaf_nodes_cache = get_leaf_nodes_from_docstore()

    if not _leaf_nodes_cache:
        logger.warning("No leaf nodes loaded for BM25 — hybrid search will be degraded")
    else:
        logger.info("BM25 cache: %d leaf nodes loaded", len(_leaf_nodes_cache))

    return _leaf_nodes_cache


def preload_bm25():
    """启动时主动预加载 BM25 缓存"""
    global _leaf_nodes_cache, _bm25_retriever_cache
    _leaf_nodes_cache = None
    _bm25_retriever_cache = None

    leaf_nodes = load_leaf_nodes_for_bm25()
    if leaf_nodes:
        _bm25_retriever_cache = _build_bm25(leaf_nodes)
        if _bm25_retriever_cache:
            logger.info("BM25 preloaded: %d nodes", len(leaf_nodes))
    else:
        logger.info("BM25 preload: no nodes yet (will load on first query after indexing)")


def invalidate_bm25_cache():
    """入库/删除后标记缓存需要刷新（延迟到下次查询时重建）"""
    global _leaf_nodes_cache, _bm25_retriever_cache
    _leaf_nodes_cache = None
    _bm25_retriever_cache = None
    logger.info("BM25 cache invalidated (will rebuild on next query)")


def get_cached_bm25_retriever(similarity_top_k: int = 15):
    """获取缓存的 BM25 检索器（避免每次查询重建）

    Args:
        similarity_top_k: top-k 数量

    Returns:
        BM25Retriever 实例，或 None
    """
    global _bm25_retriever_cache

    if _bm25_retriever_cache is not None:
        return _bm25_retriever_cache

    leaf_nodes = load_leaf_nodes_for_bm25()
    if not leaf_nodes:
        return None

    _bm25_retriever_cache = _build_bm25(leaf_nodes, similarity_top_k)
    return _bm25_retriever_cache


def _build_bm25(leaf_nodes: list, similarity_top_k: int = 15):
    """构建 BM25Retriever"""
    if not leaf_nodes:
        return None

    try:
        from llama_index.retrievers.bm25 import BM25Retriever
        retriever = BM25Retriever.from_defaults(
            nodes=leaf_nodes,
            similarity_top_k=similarity_top_k,
        )
        logger.info("BM25 retriever built with %d nodes, top_k=%d", len(leaf_nodes), similarity_top_k)
        return retriever
    except ImportError:
        logger.warning("llama-index-retrievers-bm25 not installed, BM25 unavailable")
        return None
    except Exception as e:
        logger.error("BM25 retriever build failed: %s", e)
        return None


def build_bm25_retriever(leaf_nodes: list | None = None, similarity_top_k: int = 15):
    """构建BM25Retriever（兼容旧接口，优先用缓存）

    Args:
        leaf_nodes: 叶子节点列表（None=从缓存加载）
        similarity_top_k: 返回的top-k数量

    Returns:
        BM25Retriever实例，或None
    """
    if leaf_nodes is None:
        return get_cached_bm25_retriever(similarity_top_k)
    return _build_bm25(leaf_nodes, similarity_top_k)
