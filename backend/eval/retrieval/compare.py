"""检索模式对比。

按配置文件的参数矩阵，一次跑多组实验自动对比。
"""

import asyncio
import json
import logging
import time
from typing import Any

from eval.retrieval.evaluator import evaluate_single, evaluate_batch

logger = logging.getLogger(__name__)


async def run_retrieval_comparison(
    dataset: dict,
    modes: list[dict],
    group_by_types: list[str] | None = None,
) -> dict:
    """运行检索模式对比

    Args:
        dataset: qa_dataset.json 加载后的数据
        modes: [{"name": "...", "params": {retrieval_mode, auto_merging_thresh, top_k}}]
        group_by_types: 分组统计维度，如 ["question_type", "difficulty"]

    Returns:
        {modes: [{name, overall, by_type, by_difficulty}]}
    """
    qa_pairs = dataset.get("qa_pairs", [])
    if not qa_pairs:
        return {"modes": []}

    # 构建父→子节点映射（用于 AutoMerging 部分命中判定）
    child_map = _build_child_map()

    results_by_mode = {}

    for mode in modes:
        mode_name = mode["name"]
        params = mode.get("params", {})
        logger.info("Running retrieval mode: %s (params=%s)", mode_name, params)

        mode_results = []

        for qa in qa_pairs:
            try:
                result = await _retrieve_single(qa, params, child_map)
                mode_results.append(result)
            except Exception as e:
                logger.warning("Failed to evaluate %s for mode %s: %s", qa.get("id"), mode_name, e)

        if mode_results:
            comparison = evaluate_batch(mode_results)
            comparison["name"] = mode_name

            # 按各维度分组
            if group_by_types:
                for gb in group_by_types:
                    grouped = evaluate_batch(mode_results, group_by=gb)
                    comparison[f"by_{gb}"] = grouped["by_group"]

            results_by_mode[mode_name] = comparison
            logger.info(
                "Mode %s: Recall=%.4f Precision=%.4f MRR=%.4f HitRate=%.4f",
                mode_name,
                comparison.get("recall", 0),
                comparison.get("precision", 0),
                comparison.get("mrr", 0),
                comparison.get("hit_rate", 0),
            )

    return {"modes": list(results_by_mode.values())}


async def _retrieve_single(qa: dict, params: dict, child_map: dict[str, list[str]] | None = None) -> dict:
    """对单个 QA 对执行检索并评估"""
    from app.services.knowledge.retriever import hybrid_retrieve

    retrieval_mode = params.get("retrieval_mode", "hybrid")
    top_k = params.get("top_k", 5)
    auto_merging_thresh = params.get("auto_merging_thresh", 0.5)
    kb_ids = qa.get("kb_ids")

    start_time = time.time()
    result = await hybrid_retrieve(
        query=qa["user_input"],
        kb_ids=kb_ids,
        top_k=top_k,
        retrieval_mode=retrieval_mode,
        auto_merging_thresh=auto_merging_thresh,
    )
    latency_ms = int((time.time() - start_time) * 1000)

    # 提取检索到的节点 ID
    retrieved_ids = [c["node_id"] for c in result.get("citations", []) if c.get("node_id")]

    # 评估指标
    reference_ids = qa.get("reference_context_ids", [])
    parent_id = qa.get("parent_context_id")

    metrics = evaluate_single(
        reference_ids=reference_ids,
        retrieved_ids=retrieved_ids,
        parent_id=parent_id,
        k=top_k,
        child_map=child_map,
    )

    return {
        "qa_id": qa.get("id"),
        "question_type": qa.get("question_type"),
        "difficulty": qa.get("difficulty"),
        "metrics": metrics,
        "latency_ms": result.get("latency_ms", latency_ms),
        "retrieval_mode_actual": result.get("retrieval_mode", ""),
        "num_retrieved": len(retrieved_ids),
    }


def _build_child_map() -> dict[str, list[str]] | None:
    """从 docstore 构建父→子节点 ID 映射"""
    try:
        from app.services.knowledge.docstore import get_docstore
        from llama_index.core.schema import NodeRelationship

        store = get_docstore()
        if not hasattr(store, "docs"):
            return None

        child_map = {}
        for key, node in store.docs.items():
            children = getattr(node, "children", None)
            if children and len(children) > 0:
                parent_id = getattr(node, "node_id", key)
                child_ids = []
                for child in children:
                    child_id = getattr(child, "node_id", None)
                    if child_id:
                        child_ids.append(child_id)
                if child_ids:
                    child_map[parent_id] = child_ids

        logger.info("Built child_map with %d parent entries", len(child_map))
        return child_map if child_map else None
    except Exception as e:
        logger.warning("Failed to build child_map: %s", e)
        return None
