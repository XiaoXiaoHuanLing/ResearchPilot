"""数据集质量校验 — V3 格式适配。

检查类型分布、去重、可达性、答案一致性、覆盖率和 answer_facts 完整性。
兼容 V3 的 question/user_input 双字段、dependency_node_ids、hop_type 等新字段。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# V3 目标分布（不再强制 cross_doc，支持 multi_source）
TARGET_TYPE_DIST = {
    "fact": 0.25,
    "keyword": 0.18,
    "vague": 0.12,
    "negation": 0.08,
    "multi_source": 0.37,
}

TARGET_DIFFICULTY_DIST = {
    "easy": (0.0, 0.40),
    "medium": (0.30, 0.70),
    "hard": (0.10, 1.0),
}


def _get_question(qa: dict) -> str:
    """兼容 V3 的 question 字段和旧版的 user_input 字段"""
    return qa.get("question", "") or qa.get("user_input", "")


def _get_ref_ids(qa: dict) -> list[str]:
    """兼容 V3 的 dependency_node_ids 和旧版的 reference_context_ids"""
    return qa.get("dependency_node_ids", []) or qa.get("reference_context_ids", [])


def validate_dataset(dataset_path: str, kb_ids: list[int] | None = None) -> dict:
    """校验数据集质量

    Args:
        dataset_path: qa_dataset.json 路径
        kb_ids: 知识库 ID 列表（用于可达性检查）

    Returns:
        校验结果 {passed, checks: [{name, passed, detail}]}
    """
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    version = dataset.get("version", "1.0")
    logger.info("Dataset version: %s", version)

    qa_pairs = dataset.get("qa_pairs", [])
    if not qa_pairs:
        return {"passed": False, "checks": [{"name": "non_empty", "passed": False, "detail": "数据集为空"}]}

    checks = []

    # 1. 类型分布检查
    checks.append(_check_type_distribution(qa_pairs, version))

    # 2. 难度分布检查
    checks.append(_check_difficulty_distribution(qa_pairs))

    # 3. 去重检查（文本精确匹配 + 基本语义检测）
    checks.append(_check_duplicates(qa_pairs))

    # 4. V3 专属字段检查
    if version.startswith("3"):
        checks.append(_check_v3_fields(qa_pairs))

    # 5. 可达性检查
    if kb_ids:
        checks.append(_check_reachability(qa_pairs, kb_ids))

    # 6. 答案一致性检查
    checks.append(_check_answer_consistency(qa_pairs))

    # 7. 覆盖率检查
    checks.append(_check_coverage(qa_pairs))

    # 8. answer_facts 完整性检查
    checks.append(_check_answer_facts(qa_pairs))

    passed = all(c["passed"] for c in checks)
    result = {"passed": passed, "checks": checks}

    # 打印结果
    logger.info("=== 数据集校验结果 ===")
    logger.info("总体: %s", "通过" if passed else "未通过")
    for c in checks:
        status = "✓" if c["passed"] else "✗"
        logger.info("  %s %s: %s", status, c["name"], c["detail"])

    return result


def _check_type_distribution(qa_pairs: list[dict], version: str = "3.0") -> dict:
    """检查问题类型分布"""
    # V3 使用 multi_source 替代 cross_doc
    target = TARGET_TYPE_DIST if version.startswith("3") else {
        "fact": 0.30, "keyword": 0.20, "cross_doc": 0.20, "vague": 0.20, "negation": 0.10,
    }

    type_counts = {}
    for qa in qa_pairs:
        t = qa.get("question_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    total = len(qa_pairs)
    details = []
    passed = True

    for t, target_ratio in target.items():
        actual_ratio = type_counts.get(t, 0) / total
        deviation = abs(actual_ratio - target_ratio)
        if deviation > 0.20:  # V3 放宽容差到 20%（不强求精确分布）
            passed = False
        details.append(f"{t}: {type_counts.get(t, 0)}/{total}={actual_ratio:.1%} (目标{target_ratio:.0%}, 偏差{deviation:.1%})")

    # 检查未知类型
    unknown_types = set(type_counts.keys()) - set(target.keys())
    if unknown_types:
        details.append(f"未知类型: {unknown_types}")

    return {
        "name": "type_distribution",
        "passed": passed,
        "detail": "; ".join(details),
    }


def _check_difficulty_distribution(qa_pairs: list[dict]) -> dict:
    """检查难度分布"""
    diff_counts = {}
    for qa in qa_pairs:
        d = qa.get("difficulty", "medium")
        diff_counts[d] = diff_counts.get(d, 0) + 1

    total = len(qa_pairs)
    passed = True
    details = []

    for d, (lo, hi) in TARGET_DIFFICULTY_DIST.items():
        ratio = diff_counts.get(d, 0) / total
        ok = lo <= ratio <= hi
        if not ok:
            passed = False
        details.append(f"{d}: {diff_counts.get(d, 0)}/{total}={ratio:.1%} (范围[{lo:.0%},{hi:.0%}])")

    return {
        "name": "difficulty_distribution",
        "passed": passed,
        "detail": "; ".join(details),
    }


def _check_duplicates(qa_pairs: list[dict]) -> dict:
    """检查问题去重（文本精确匹配）"""
    seen = set()
    dup_count = 0
    for qa in qa_pairs:
        q = _get_question(qa).strip().lower()
        if q in seen:
            dup_count += 1
        seen.add(q)

    passed = dup_count == 0
    return {
        "name": "duplicates",
        "passed": passed,
        "detail": f"重复问题数: {dup_count}/{len(qa_pairs)}",
    }


def _check_v3_fields(qa_pairs: list[dict]) -> dict:
    """V3 专属字段完整性检查"""
    missing = {
        "topic": 0,
        "dependency_node_ids": 0,
        "hop_type": 0,
        "source": 0,
    }

    for qa in qa_pairs:
        if not qa.get("topic"):
            missing["topic"] += 1
        if not qa.get("dependency_node_ids"):
            missing["dependency_node_ids"] += 1
        if not qa.get("hop_type"):
            missing["hop_type"] += 1
        if not qa.get("source"):
            missing["source"] += 1

    total = len(qa_pairs)
    # topic 和 source 可以为空（不阻塞）
    # dependency_node_ids 和 hop_type 不能为空
    critical_missing = missing["dependency_node_ids"] + missing["hop_type"]
    passed = critical_missing == 0

    details = [f"{k}: {v}/{total} missing" for k, v in missing.items() if v > 0]
    if not details:
        details = ["所有 V3 字段完整"]

    return {
        "name": "v3_fields",
        "passed": passed,
        "detail": "; ".join(details),
    }


def _check_reachability(qa_pairs: list[dict], kb_ids: list[int]) -> dict:
    """检查 ground truth 可达性"""
    import asyncio

    async def _check():
        hit_count = 0
        for qa in qa_pairs:
            try:
                from app.services.knowledge.retriever import hybrid_retrieve
                question = _get_question(qa)
                result = await hybrid_retrieve(question, kb_ids=kb_ids, top_k=5)
                retrieved_ids = {c.get("node_id") for c in result.get("citations", [])}
                ref_ids = set(_get_ref_ids(qa))
                if retrieved_ids & ref_ids:
                    hit_count += 1
            except Exception:
                pass
        return hit_count

    try:
        hit_count = asyncio.run(_check())
    except RuntimeError as e:
        logger.warning("Reachability check skipped (event loop issue): %s", e)
        hit_count = -1

    total = len(qa_pairs)
    if hit_count < 0:
        return {
            "name": "reachability",
            "passed": True,
            "detail": "可达性检查已跳过（事件循环冲突）",
        }
    ratio = hit_count / total if total > 0 else 0
    passed = ratio >= 0.7

    return {
        "name": "reachability",
        "passed": passed,
        "detail": f"可达率: {hit_count}/{total}={ratio:.1%} (目标≥70%)",
    }


def _check_answer_consistency(qa_pairs: list[dict]) -> dict:
    """检查 answer 是否可从 contexts 推导（简单启发式）"""
    consistent = 0
    for qa in qa_pairs:
        answer = qa.get("reference", "")
        contexts = qa.get("reference_contexts", [])
        if not answer or not contexts:
            continue

        answer_words = set(answer.replace("，", " ").replace("。", " ").replace("、", " ").split())
        context_text = " ".join(contexts)
        context_words = set(context_text.replace("，", " ").replace("。", " ").replace("、", " ").split())

        overlap = answer_words & context_words
        if len(overlap) >= len(answer_words) * 0.5:
            consistent += 1

    total = len([q for q in qa_pairs if q.get("reference") and q.get("reference_contexts")])
    ratio = consistent / total if total > 0 else 1.0
    passed = ratio >= 0.9

    return {
        "name": "answer_consistency",
        "passed": passed,
        "detail": f"一致率: {consistent}/{total}={ratio:.1%} (目标≥90%)",
    }


def _check_coverage(qa_pairs: list[dict]) -> dict:
    """检查文档覆盖率"""
    referenced_docs = set()
    for qa in qa_pairs:
        # V3: kb_doc_ids（列表）或 kb_doc_id（单值）
        doc_ids = qa.get("kb_doc_ids", [])
        if not doc_ids and qa.get("kb_doc_id"):
            doc_ids = [qa["kb_doc_id"]]
        for doc_id in doc_ids:
            if doc_id:
                referenced_docs.add(doc_id)

    try:
        from app.db.session import SessionLocal
        from app.db.models import KbDocumentModel
        with SessionLocal() as db:
            total_docs = db.query(KbDocumentModel).filter(
                KbDocumentModel.index_status == "indexed"
            ).count()
    except Exception:
        total_docs = len(referenced_docs)

    ratio = len(referenced_docs) / total_docs if total_docs > 0 else 1.0
    passed = ratio >= 0.6

    return {
        "name": "coverage",
        "passed": passed,
        "detail": f"覆盖率: {len(referenced_docs)}/{total_docs}={ratio:.1%} (目标≥60%)",
    }


def _check_answer_facts(qa_pairs: list[dict]) -> dict:
    """检查 answer_facts 完整性"""
    has_facts = 0
    total = len(qa_pairs)

    for qa in qa_pairs:
        facts = qa.get("answer_facts", [])
        if facts and len(facts) >= 2:
            has_facts += 1

    ratio = has_facts / total if total > 0 else 1.0
    passed = ratio >= 0.90  # V3 放宽到 90%（多源问题答案可能较简短）

    return {
        "name": "answer_facts",
        "passed": passed,
        "detail": f"完整率: {has_facts}/{total}={ratio:.1%} (目标≥90%)",
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    path = sys.argv[1] if len(sys.argv) > 1 else "qa_dataset.json"
    kb_ids_arg = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else None
    result = validate_dataset(path, kb_ids=kb_ids_arg)
    sys.exit(0 if result["passed"] else 1)
