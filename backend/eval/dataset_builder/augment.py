"""数据增强 — V3 格式适配。

同义改写：对已有问题生成变体，共享同一 dependency_node_ids 和 topic。
跨文档组合：已废弃（V3 的多源问题通过 Round 3 合成，不再使用此模块）。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

REWRITE_PROMPT = """将以下问题改写为语义相同但表述不同的变体。保持核心含义不变。
只输出改写后的问题，不要解释。

原问题：{question}

变体{num}："""


def _get_question(qa: dict) -> str:
    """兼容 V3 的 question 字段和旧版的 user_input 字段"""
    return qa.get("question", "") or qa.get("user_input", "")


def augment_rewrite(
    dataset_path: str,
    output_path: str | None = None,
    factor: int = 2,
    llm_config: dict | None = None,
) -> str:
    """同义改写增强

    对每个问题生成 factor 个变体，共享同一 ground truth。

    Args:
        dataset_path: 原始数据集路径
        output_path: 输出路径（默认覆盖原文件）
        factor: 每个问题生成几个变体
        llm_config: LLM 配置

    Returns:
        输出文件路径
    """
    output_path = output_path or dataset_path

    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    qa_pairs = dataset.get("qa_pairs", [])
    new_pairs = []

    for qa in qa_pairs:
        new_pairs.append(qa)
        if not llm_config:
            continue

        for i in range(factor):
            try:
                from eval.dataset_builder.extract_qa import call_llm
                question = _get_question(qa)
                variant = call_llm(
                    REWRITE_PROMPT.format(question=question, num=i + 1),
                    llm_config,
                    temperature=0.8,
                )
                if variant and variant != question:
                    new_qa = dict(qa)
                    new_qa["id"] = f"{qa.get('id', 'q')}_rw{i + 1}"
                    new_qa["question"] = variant
                    new_qa["user_input"] = variant  # 向后兼容
                    new_qa["source"] = "rewritten"
                    new_pairs.append(new_qa)
            except Exception as e:
                logger.warning("Rewrite failed for %s: %s", qa.get("id"), e)

    dataset["qa_pairs"] = new_pairs
    dataset["stats"] = _compute_stats(new_pairs)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    logger.info("Rewrite augmentation: %d → %d QA pairs", len(qa_pairs), len(new_pairs))
    return output_path


def augment_cross_compose(
    dataset_path: str,
    output_path: str | None = None,
    max_new: int = 15,
    llm_config: dict | None = None,
) -> str:
    """[DEPRECATED] 跨文档组合题生成。

    V3 的多源问题通过 Round 3 (verify.py) 的主题交叉合成机制生成，
    不再使用此函数。保留接口签名以防旧调用报错。
    """
    logger.warning("augment_cross_compose is deprecated in V3. Use Round 3 synthesis instead.")
    output_path = output_path or dataset_path

    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    # 不做任何修改，直接写回
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    return output_path


def _compute_stats(qa_pairs: list[dict]) -> dict:
    """计算数据集统计信息（V3 格式）"""
    total = len(qa_pairs)

    by_type = {}
    by_difficulty = {}
    by_hop_type = {}
    by_source = {}

    for qa in qa_pairs:
        t = qa.get("question_type", "unknown")
        d = qa.get("difficulty", "medium")
        h = qa.get("hop_type", "single")
        s = qa.get("source", "generated")

        by_type[t] = by_type.get(t, 0) + 1
        by_difficulty[d] = by_difficulty.get(d, 0) + 1
        by_hop_type[h] = by_hop_type.get(h, 0) + 1
        by_source[s] = by_source.get(s, 0) + 1

    return {
        "total": total,
        "by_type": by_type,
        "by_difficulty": by_difficulty,
        "by_hop_type": by_hop_type,
        "by_source": by_source,
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    path = sys.argv[1]
    method = sys.argv[2] if len(sys.argv) > 2 else "rewrite"

    if method == "rewrite":
        augment_rewrite(path, factor=2)
    elif method == "cross-compose":
        augment_cross_compose(path)
