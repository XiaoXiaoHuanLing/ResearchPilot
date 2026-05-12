"""Reranker 服务 — 基于 BGE-reranker-v2-m3 交叉编码器的精确重排序。

用于 RAG Agent 蒸馏前对 RecallPool 节点重排，以及 hybrid 检索后重排。
模型: BAAI/bge-reranker-v2-m3 (568M, 中英文, max_length=512)
部署: 本地 CrossEncoder (sentence-transformers)，GPU 加速。
"""

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ─── 单例缓存 ───

_reranker_instance = None


@dataclass
class RerankResult:
    """重排结果条目"""
    index: int           # 原始文档列表中的索引
    score: float         # 交叉编码器相关性分数
    text: str            # 原文


def _get_reranker():
    """获取缓存的 CrossEncoder 实例（懒加载）"""
    global _reranker_instance
    if _reranker_instance is not None:
        return _reranker_instance

    try:
        from sentence_transformers import CrossEncoder

        logger.info("Loading BGE-reranker-v2-m3 CrossEncoder...")
        start = time.time()
        _reranker_instance = CrossEncoder(
            "BAAI/bge-reranker-v2-m3",
            max_length=512,
        )
        elapsed = time.time() - start
        logger.info("Reranker loaded in %.1fs", elapsed)
        return _reranker_instance
    except ImportError:
        logger.error(
            "sentence-transformers not installed. "
            "Install with: pip install sentence-transformers torch"
        )
        return None
    except Exception as e:
        logger.error("Failed to load reranker: %s", e)
        return None


async def rerank(
    query: str,
    documents: list[str],
    top_n: int | None = None,
) -> list[RerankResult]:
    """对文档列表执行交叉编码器重排。

    Args:
        query: 查询问题
        documents: 待重排的文档文本列表
        top_n: 返回前 N 个结果（None=全部返回）

    Returns:
        按 score 降序排列的 RerankResult 列表
    """
    if not documents:
        return []

    reranker = _get_reranker()
    if reranker is None:
        # 降级：返回原始顺序
        logger.warning("Reranker unavailable, returning original order")
        return [
            RerankResult(index=i, score=0.0, text=doc)
            for i, doc in enumerate(documents)
        ]

    # 构造 query-document 对
    pairs = [[query, doc] for doc in documents]

    try:
        start = time.time()
        scores = reranker.predict(pairs, show_progress_bar=False)
        elapsed = time.time() - start
        logger.info(
            "Rerank: %d docs in %.2fs (%.0f docs/s)",
            len(documents), elapsed, len(documents) / max(elapsed, 0.01),
        )
    except Exception as e:
        logger.error("Rerank predict failed: %s", e)
        return [
            RerankResult(index=i, score=0.0, text=doc)
            for i, doc in enumerate(documents)
        ]

    # 构建结果，按分数降序
    results = [
        RerankResult(index=i, score=float(scores[i]), text=documents[i])
        for i in range(len(documents))
    ]
    results.sort(key=lambda r: r.score, reverse=True)

    if top_n is not None:
        results = results[:top_n]

    return results


def rerank_sync(
    query: str,
    documents: list[str],
    top_n: int | None = None,
) -> list[RerankResult]:
    """同步版本的重排（用于非 async 上下文）。"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        # 已在 event loop 中，用 run_in_executor
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return loop.run_in_executor(
                pool,
                lambda: _rerank_sync_impl(query, documents, top_n),
            )
    except RuntimeError:
        # 无 event loop，直接同步执行
        return _rerank_sync_impl(query, documents, top_n)


def _rerank_sync_impl(
    query: str,
    documents: list[str],
    top_n: int | None = None,
) -> list[RerankResult]:
    """同步重排实现"""
    if not documents:
        return []

    reranker = _get_reranker()
    if reranker is None:
        return [
            RerankResult(index=i, score=0.0, text=doc)
            for i, doc in enumerate(documents)
        ]

    pairs = [[query, doc] for doc in documents]

    try:
        scores = reranker.predict(pairs, show_progress_bar=False)
    except Exception as e:
        logger.error("Rerank predict failed: %s", e)
        return [
            RerankResult(index=i, score=0.0, text=doc)
            for i, doc in enumerate(documents)
        ]

    results = [
        RerankResult(index=i, score=float(scores[i]), text=documents[i])
        for i in range(len(documents))
    ]
    results.sort(key=lambda r: r.score, reverse=True)

    if top_n is not None:
        results = results[:top_n]

    return results
