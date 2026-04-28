"""数据集构建主入口 — V3.1 三轮制。

用法:
  python build_dataset.py --kb-ids 1,2 --output qa_dataset.json --count 80
  python build_dataset.py --kb-ids 1 --count 50 --skip-round3     # 跳过多源合成
  python build_dataset.py --kb-ids 1 --count 30 --dry-run         # 只跑 Round 1 不写文件
  python build_dataset.py --kb-ids 1 --resume                      # 从 checkpoint 续建

三轮流程:
  Round 1: 单文档问题生成（相邻节点分组 + 6约束 + topic + 常识内置过滤）
  Round 2: 质量清洗（语义去重合并 + 过广检测 + 代表性筛选）
  Round 3: 多源问题合成（主题交叉发现 + LLM合成 + 单源验证）[可选]
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="构建 RAG 评估数据集 (V3.1)")
    parser.add_argument("--kb-ids", type=str, required=True, help="知识库 ID 列表，逗号分隔")
    parser.add_argument("--output", type=str, default="qa_dataset.json", help="输出文件路径")
    parser.add_argument("--count", type=int, default=80, help="目标 QA 对数量")
    parser.add_argument("--skip-round3", action="store_true", help="跳过 Round 3 多源合成")
    parser.add_argument("--dry-run", action="store_true", help="只跑 Round 1 不写文件")
    parser.add_argument("--resume", action="store_true", help="从 checkpoint 续建")
    parser.add_argument("--group-size", type=int, default=5, help="每组节点数（默认5）")
    parser.add_argument("--overlap", type=int, default=1, help="组间重叠节点数（默认1）")
    parser.add_argument("--dedup-threshold", type=float, default=0.88, help="语义去重 embedding 阈值（默认0.88）")
    parser.add_argument("--max-multi-source", type=int, default=30, help="多源问题上限（默认30）")
    args = parser.parse_args()

    # 日志
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # 确保后端代码在 import 路径中
    backend_dir = Path(__file__).resolve().parent.parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    # 加载环境变量
    from dotenv import load_dotenv
    env_path = backend_dir / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    # LLM 配置：优先级 EVAL_LLM_MODEL > DASHSCOPE_MODEL_NAME > 默认
    eval_model = (
        os.environ.get("EVAL_LLM_MODEL")
        or os.environ.get("DASHSCOPE_MODEL_NAME")
        or "qwen3.5-plus-2026-02-15"
    )
    llm_config = {
        "api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
        "base_url": os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "model": eval_model,
    }
    logger.info("Primary eval model: %s", eval_model)
    logger.info("Fallback models: %s", os.environ.get("DASHSCOPE_MODEL_NAME_FALLBACK", "none"))

    if not llm_config["api_key"]:
        logger.error("DASHSCOPE_API_KEY 环境变量未设置")
        sys.exit(1)

    kb_ids = [int(x.strip()) for x in args.kb_ids.split(",")]

    # checkpoint 路径
    checkpoint_path = str(Path(args.output).with_suffix(".ckpt.json"))
    if not args.resume:
        # 新建时清除旧 checkpoint
        if Path(checkpoint_path).exists():
            Path(checkpoint_path).unlink()
            logger.info("Cleared old checkpoint")

    # ══════════════════════════════════════════
    # Step 1: 加载文档节点
    # ══════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Step 1: 加载文档节点...")
    logger.info("=" * 60)

    try:
        from app.main import app
        logger.info("应用初始化完成")
    except Exception as e:
        logger.warning("应用初始化失败: %s", e)

    from eval.dataset_builder.extract_qa import load_nodes_from_docstore

    all_nodes = load_nodes_from_docstore()
    leaf_nodes = [n for n in all_nodes if n.get("is_leaf")]

    # 按 kb_ids 过滤
    if kb_ids:
        leaf_nodes = [n for n in leaf_nodes if n.get("metadata", {}).get("kb_id") in kb_ids]

    # 按 kb_doc_id 分组
    doc_nodes: dict[int, list[dict]] = {}
    for node in leaf_nodes:
        doc_id = node.get("metadata", {}).get("kb_doc_id")
        if doc_id:
            doc_nodes.setdefault(doc_id, []).append(node)

    logger.info("加载了 %d 个叶子节点，来自 %d 个文档", len(leaf_nodes), len(doc_nodes))

    if not leaf_nodes:
        logger.error("没有找到叶子节点，请确认知识库中有已索引的文档")
        sys.exit(1)

    # ══════════════════════════════════════════
    # Round 1: 单文档问题生成
    # ══════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Round 1: 单文档问题生成")
    logger.info("=" * 60)

    from eval.dataset_builder.extract_qa import generate_round1_questions

    qa_pairs, node_store = generate_round1_questions(
        all_doc_nodes=doc_nodes,
        llm_config=llm_config,
        target_total=args.count,
        group_size=args.group_size,
        overlap=args.overlap,
        checkpoint_path=checkpoint_path if not args.dry_run else None,
    )

    logger.info("Round 1 完成: %d 条问题", len(qa_pairs))

    if args.dry_run:
        logger.info("Dry run 模式，不写入文件")
        for qa in qa_pairs:
            logger.info("  [%s] %s (%s, %s)", qa["id"], qa["question"][:60], qa["question_type"], qa.get("topic", ""))
        return

    if not qa_pairs:
        logger.error("Round 1 未生成任何问题，请检查知识库内容")
        sys.exit(1)

    # ══════════════════════════════════════════
    # Round 2: 质量清洗
    # ══════════════════════════════════════════
    logger.info("=" * 60)
    logger.info("Round 2: 质量清洗")
    logger.info("=" * 60)

    from eval.dataset_builder.verify import run_round2

    qa_pairs = run_round2(qa_pairs, llm_config, node_store=node_store, dedup_threshold=args.dedup_threshold)

    logger.info("Round 2 完成: %d 条问题", len(qa_pairs))

    if not qa_pairs:
        logger.error("Round 2 后无问题剩余，请检查质量标准是否过严")
        sys.exit(1)

    # ══════════════════════════════════════════
    # Round 3: 多源问题合成（可选）
    # ══════════════════════════════════════════
    multi_source_qa = []

    if not args.skip_round3:
        logger.info("=" * 60)
        logger.info("Round 3: 多源问题合成")
        logger.info("=" * 60)

        from eval.dataset_builder.verify import run_round3

        multi_source_qa = run_round3(qa_pairs, llm_config, node_store=node_store, max_multi_source=args.max_multi_source)

        logger.info("Round 3 完成: %d 条多源问题", len(multi_source_qa))
    else:
        logger.info("跳过 Round 3（--skip-round3）")

    # ══════════════════════════════════════════
    # 合并最终数据集
    # ══════════════════════════════════════════
    if multi_source_qa:
        synthesized_ids = set()
        for ms in multi_source_qa:
            for sid in ms.get("synthesized_from", []):
                synthesized_ids.add(sid)

        for qa in qa_pairs:
            if qa["id"] in synthesized_ids:
                qa["used_in_synthesis"] = True
            else:
                qa["used_in_synthesis"] = False

    final_qa = qa_pairs + multi_source_qa

    # 重新分配 ID
    for i, qa in enumerate(final_qa):
        qa["id"] = f"q{i + 1:03d}"

    # 统计
    type_counts = {}
    difficulty_counts = {}
    hop_type_counts = {}
    source_counts = {}
    for qa in final_qa:
        t = qa.get("question_type", "unknown")
        d = qa.get("difficulty", "medium")
        h = qa.get("hop_type", "single")
        s = qa.get("source", "generated")
        type_counts[t] = type_counts.get(t, 0) + 1
        difficulty_counts[d] = difficulty_counts.get(d, 0) + 1
        hop_type_counts[h] = hop_type_counts.get(h, 0) + 1
        source_counts[s] = source_counts.get(s, 0) + 1

    # 组装数据集
    dataset = {
        "version": "3.0",
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "source_kb_ids": kb_ids,
        "qa_pairs": final_qa,
        "stats": {
            "total": len(final_qa),
            "by_type": type_counts,
            "by_difficulty": difficulty_counts,
            "by_hop_type": hop_type_counts,
            "by_source": source_counts,
        },
    }

    # 写入文件
    output_path = Path(args.output)
    if not output_path.is_absolute():
        # If path contains directory separators, resolve from CWD; otherwise from script dir
        if "/" in args.output or "\\" in args.output:
            output_path = Path.cwd() / output_path
        else:
            output_path = Path(__file__).resolve().parent / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    logger.info("=" * 60)
    logger.info("数据集已写入 %s", output_path)
    logger.info("总计 %d 条问题", len(final_qa))
    logger.info("  单文档: %d 条", len(qa_pairs))
    logger.info("  多源: %d 条", len(multi_source_qa))
    logger.info("  类型分布: %s", type_counts)
    logger.info("  难度分布: %s", difficulty_counts)
    logger.info("  来源分布: %s", source_counts)
    logger.info("=" * 60)

    # 清理 checkpoint
    if Path(checkpoint_path).exists():
        Path(checkpoint_path).unlink()
        logger.info("Checkpoint cleaned up")

    # 可选：质量校验
    try:
        from eval.dataset_builder.validate_dataset import validate_dataset
        logger.info("Running dataset validation...")
        result = validate_dataset(str(output_path), kb_ids=kb_ids)
        if not result["passed"]:
            logger.warning("数据集校验未通过，请检查详情")
    except Exception as e:
        logger.warning("Validation skipped: %s", e)


if __name__ == "__main__":
    main()
