"""RAG 评估统一入口。

重构后，`backend/eval` 只保留三大块职责：
1. dataset_builder：数据集构建与校验
2. metrics：指标计算
3. reporting：报告生成

当前入口先完成结构收敛和兼容调度，后续再继续替换旧的内部实现细节。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def main() -> None:
    """CLI 入口。"""

    parser = argparse.ArgumentParser(description="ResearchPilot RAG 评估工具")
    parser.add_argument("--config", type=str, default=None, help="配置文件路径，默认使用 eval/eval_config.yaml")
    parser.add_argument("--stage", type=str, default=None, help="评估阶段：retrieval / answer / e2e / all")
    parser.add_argument("--dataset", type=str, default=None, help="覆盖配置中的数据集路径")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录，默认使用 eval/results/<timestamp>")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出详细日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    _load_env(backend_dir)
    config = _load_config(args.config, backend_dir)
    stages = _resolve_stages(args.stage, config)
    dataset = _load_dataset(args.dataset or config.get("dataset", {}).get("path"), backend_dir)

    results = run_evaluation(dataset=dataset, config=config, stages=stages)

    output_dir = args.output_dir or str(backend_dir / "eval" / "results" / datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    dataset_info = _build_dataset_info(dataset)
    from eval.reporting import generate_report_files

    json_path, md_path = generate_report_files(results, output_dir, dataset_info)
    logger.info("评估完成")
    logger.info("JSON 报告: %s", json_path)
    logger.info("Markdown 报告: %s", md_path)


def run_evaluation(dataset: dict[str, Any], config: dict[str, Any], stages: list[str]) -> dict[str, Any]:
    """调度评估阶段。

    当前实现先兼容旧 retrieval / answer / e2e 调用，
    但结果统一整理为新的三大块结构：
    - retrieval
    - generation
    - e2e
    """

    results: dict[str, Any] = {}
    kb_ids = config.get("dataset", {}).get("kb_ids") or dataset.get("source_kb_ids", [1])
    llm_config = {
        "api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
        "base_url": os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "model": os.environ.get("EVAL_LLM_MODEL", os.environ.get("DASHSCOPE_MODEL_NAME", "qwen3.5-flash-2026-02-23")),
    }

    if "retrieval" in stages:
        from eval.metrics import run_retrieval_comparison
        import asyncio

        retrieval_config = config.get("retrieval", {})
        raw = asyncio.run(
            run_retrieval_comparison(
                dataset=dataset,
                modes=retrieval_config.get(
                    "compare_modes",
                    [{"name": "hybrid_default", "params": {"retrieval_mode": "hybrid", "auto_merging_thresh": 0.5, "top_k": 5}}],
                ),
                group_by_fields=["query_type", "difficulty"],
            )
        )
        results["retrieval"] = raw

    if "answer" in stages:
        from eval.metrics import run_answer_evaluation
        import asyncio

        answer_config = config.get("answer", {})
        raw = asyncio.run(
            run_answer_evaluation(
                dataset=dataset,
                kb_ids=kb_ids,
                ragas_metrics=answer_config.get("ragas_metrics", ["faithfulness", "context_recall"]),
                generate_prompt=answer_config.get("generate_prompt"),
                llm_config=llm_config,
                top_k=answer_config.get("top_k", 5),
            )
        )
        results["generation"] = raw

    if "e2e" in stages:
        from eval.metrics import run_e2e_evaluation
        import asyncio

        e2e_config = config.get("e2e", {})
        raw = asyncio.run(
            run_e2e_evaluation(
                dataset=dataset,
                chat_endpoint=e2e_config.get("chat_endpoint", "http://localhost:8000/api/chat"),
                extra_metrics=e2e_config.get("extra_metrics", ["fact_coverage", "key_phrase_recall", "token_cost", "latency"]),
                llm_config=llm_config,
                kb_ids=kb_ids,
            )
        )
        results["e2e"] = raw

    return results


def _build_dataset_info(dataset: dict[str, Any]) -> dict[str, Any]:
    """整理报告所需的数据集摘要。"""

    if "samples" in dataset:
        return {
            "dataset_size": dataset.get("dataset_size", len(dataset.get("samples", []))),
            "difficulty_counts": dataset.get("difficulty_counts", {}),
            "query_type_counts": dataset.get("query_type_counts", {}),
        }

    qa_pairs = dataset.get("qa_pairs", [])
    difficulty_counts: dict[str, int] = {}
    query_type_counts: dict[str, int] = {}
    for item in qa_pairs:
        difficulty = item.get("difficulty", "medium")
        query_type = item.get("query_type") or item.get("question_type", "semantic")
        difficulty_counts[difficulty] = difficulty_counts.get(difficulty, 0) + 1
        query_type_counts[query_type] = query_type_counts.get(query_type, 0) + 1

    return {
        "dataset_size": len(qa_pairs),
        "difficulty_counts": difficulty_counts,
        "query_type_counts": query_type_counts,
    }


def _load_env(backend_dir: Path) -> None:
    """加载后端 .env。"""

    from dotenv import load_dotenv

    env_path = backend_dir / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def _load_config(config_path: str | None, backend_dir: Path) -> dict[str, Any]:
    """加载配置文件。"""

    if config_path is None:
        for candidate in [backend_dir / "eval" / "eval_config.yaml", Path("eval_config.yaml")]:
            if candidate.exists():
                config_path = str(candidate)
                break

    if config_path is None:
        return _default_config()

    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as file:
            return yaml.safe_load(file) or {}
    except Exception as exc:
        logger.error("加载配置失败: %s", exc)
        return _default_config()


def _load_dataset(dataset_path: str | None, backend_dir: Path) -> dict[str, Any]:
    """加载数据集文件。"""

    path = Path(dataset_path or "eval/qa_dataset.json")
    if not path.is_absolute():
        path = backend_dir / path

    if not path.exists():
        raise FileNotFoundError(f"数据集不存在: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _default_config() -> dict[str, Any]:
    """默认配置。"""

    return {
        "dataset": {"path": "eval/qa_dataset.json", "kb_ids": [1]},
        "retrieval": {"enabled": True},
        "answer": {"enabled": True},
        "e2e": {"enabled": False},
    }


def _resolve_stages(stage_arg: str | None, config: dict[str, Any]) -> list[str]:
    """决定要运行的评估阶段。"""

    if stage_arg:
        if stage_arg == "all":
            return ["retrieval", "answer", "e2e"]
        return [item.strip() for item in stage_arg.split(",") if item.strip()]

    stages: list[str] = []
    if config.get("retrieval", {}).get("enabled", True):
        stages.append("retrieval")
    if config.get("answer", {}).get("enabled", True):
        stages.append("answer")
    if config.get("e2e", {}).get("enabled", False):
        stages.append("e2e")
    return stages or ["retrieval", "answer"]


if __name__ == "__main__":
    main()
