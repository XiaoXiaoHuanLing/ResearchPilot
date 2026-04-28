"""检索评估指标计算。

支持 Recall@K / Precision@K / MRR / NDCG@K / Hit Rate / AutoMerging 触发率 / 延迟。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def evaluate_single(
    reference_ids: list[str],
    retrieved_ids: list[str],
    parent_id: str | None = None,
    k: int = 5,
    child_map: dict[str, list[str]] | None = None,
) -> dict:
    """评估单个查询的检索指标

    Args:
        reference_ids: ground truth 节点 ID 列表
        retrieved_ids: 检索返回的节点 ID 列表（按相关度排序）
        parent_id: 参考节点的父节点 ID（AutoMerging 命中判定用）
        k: top-K
        child_map: 父节点 → 子节点 ID 列表映射（用于父节点部分命中判定）

    Returns:
        {recall, precision, mrr, ndcg, hit, auto_merging_hit}
    """
    if not reference_ids:
        return {"recall": 0.0, "precision": 0.0, "mrr": 0.0, "ndcg": 0.0, "hit": False, "auto_merging_hit": False}

    top_k_ids = retrieved_ids[:k]
    ref_set = set(reference_ids)

    # 判定命中（含 AutoMerging 逻辑）
    hit_ids = set()
    auto_merging_hit = False

    for i, rid in enumerate(top_k_ids):
        if rid in ref_set:
            hit_ids.add(rid)
        elif parent_id and rid == parent_id:
            # 父节点命中 → AutoMerging 生效
            auto_merging_hit = True
            # 父节点包含所有子节点，命中了父节点等于命中了其下属于 ref_set 的子节点
            if child_map and rid in child_map:
                parent_children_in_ref = set(child_map[rid]) & ref_set
                hit_ids.update(parent_children_in_ref)
            else:
                # 无法确定父节点包含哪些子节点，保守地仅标记为 auto_merging_hit
                pass
        elif child_map and rid in child_map:
            # 父节点部分命中
            children_of_retrieved = set(child_map[rid])
            if children_of_retrieved & ref_set:
                hit_ids.update(children_of_retrieved & ref_set)

    # Recall@K
    recall = len(hit_ids & ref_set) / len(ref_set) if ref_set else 0.0

    # Precision@K
    precision = len(hit_ids) / k if k > 0 else 0.0

    # MRR
    mrr = 0.0
    for i, rid in enumerate(top_k_ids):
        if rid in ref_set or (parent_id and rid == parent_id):
            mrr = 1.0 / (i + 1)
            break

    # NDCG@K
    ndcg = _compute_ndcg(top_k_ids, ref_set, parent_id)

    # Hit
    hit = len(hit_ids & ref_set) > 0 or auto_merging_hit

    return {
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "mrr": round(mrr, 4),
        "ndcg": round(ndcg, 4),
        "hit": hit,
        "auto_merging_hit": auto_merging_hit,
    }


def evaluate_batch(
    results: list[dict],
    group_by: str | None = None,
) -> dict:
    """批量评估汇总

    Args:
        results: 每个查询的评估结果列表，每个含 {qa_id, question_type, difficulty, metrics, latency_ms}
        group_by: 按 question_type / difficulty 分组汇总

    Returns:
        {overall: {recall, precision, mrr, ndcg, hit_rate, auto_merging_rate, latency_p50, latency_p95},
         by_group: {...}}
    """
    overall = _aggregate_metrics(results)

    by_group = {}
    if group_by:
        groups = {}
        for r in results:
            key = r.get(group_by, "unknown")
            groups.setdefault(key, []).append(r)

        for key, group_results in groups.items():
            by_group[key] = _aggregate_metrics(group_results)

    return {"overall": overall, "by_group": by_group}


def _compute_ndcg(retrieved_ids: list[str], ref_set: set[str], parent_id: str | None = None) -> float:
    """计算 NDCG@K"""
    import math

    # DCG
    dcg = 0.0
    for i, rid in enumerate(retrieved_ids):
        if rid in ref_set or (parent_id and rid == parent_id):
            dcg += 1.0 / math.log2(i + 2)  # log2(rank+1), rank从1开始

    # IDCG（理想情况下所有相关文档排在最前面）
    idcg = 0.0
    n_relevant = len(ref_set)
    for i in range(min(n_relevant, len(retrieved_ids))):
        idcg += 1.0 / math.log2(i + 2)

    return dcg / idcg if idcg > 0 else 0.0


def _aggregate_metrics(results: list[dict]) -> dict:
    """汇总一组查询的指标"""
    if not results:
        return {}

    n = len(results)
    metrics_list = [r["metrics"] for r in results if "metrics" in r]
    latencies = [r.get("latency_ms", 0) for r in results]

    if not metrics_list:
        return {"count": n}

    recall_vals = [m["recall"] for m in metrics_list]
    precision_vals = [m["precision"] for m in metrics_list]
    mrr_vals = [m["mrr"] for m in metrics_list]
    ndcg_vals = [m["ndcg"] for m in metrics_list]
    hit_vals = [1 if m["hit"] else 0 for m in metrics_list]
    am_vals = [1 if m.get("auto_merging_hit") else 0 for m in metrics_list]

    # 延迟统计
    latencies_sorted = sorted(latencies)
    n_lat = len(latencies_sorted)
    # 使用 nearest-rank 百分位数
    p50_idx = min(int(n_lat * 0.5), n_lat - 1) if n_lat else 0
    p95_idx = min(int(n_lat * 0.95), n_lat - 1) if n_lat else 0

    return {
        "count": n,
        "recall": round(sum(recall_vals) / n, 4),
        "precision": round(sum(precision_vals) / n, 4),
        "mrr": round(sum(mrr_vals) / n, 4),
        "ndcg": round(sum(ndcg_vals) / n, 4),
        "hit_rate": round(sum(hit_vals) / n, 4),
        "auto_merging_rate": round(sum(am_vals) / n, 4),
        "latency_p50": latencies_sorted[p50_idx] if n_lat else 0,
        "latency_p95": latencies_sorted[p95_idx] if n_lat else 0,
    }
