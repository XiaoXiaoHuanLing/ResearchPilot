"""RAG 评估主入口。

一条命令跑完全链路评估：检索 → 回答 → 端到端。

用法:
  python rag_eval.py                                          # 跑所有启用的阶段
  python rag_eval.py --config eval_config.yaml                # 指定配置文件
  python rag_eval.py --stage retrieval                        # 只跑检索评估
  python rag_eval.py --stage answer                           # 只跑回答评估
  python rag_eval.py --stage e2e                              # 只跑端到端评估
  python rag_eval.py --stage retrieval,answer                  # 跑检索+回答
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="ResearchPilot RAG 评估工具")
    parser.add_argument("--config", type=str, default=None, help="配置文件路径（默认 eval/eval_config.yaml）")
    parser.add_argument("--stage", type=str, default=None, help="评估阶段：retrieval / answer / e2e / all（逗号分隔，默认跑所有启用的）")
    parser.add_argument("--dataset", type=str, default=None, help="覆盖配置文件中的数据集路径")
    parser.add_argument("--output-dir", type=str, default=None, help="输出目录（默认 eval/results/<timestamp>）")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    # 日志
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(message)s")

    # 确保后端代码在 import 路径中
    backend_dir = Path(__file__).resolve().parent.parent
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    # 加载环境变量
    from dotenv import load_dotenv
    env_path = backend_dir / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    # 加载配置
    config = _load_config(args.config)
    logger.info("配置加载完成")

    # 确定要跑的阶段
    stages = _resolve_stages(args.stage, config)
    logger.info("评估阶段: %s", stages)

    # 加载数据集
    dataset_path = args.dataset or config.get("dataset", {}).get("path", "eval/dataset_builder/qa_dataset.json")
    dataset_path = Path(dataset_path)
    if not dataset_path.is_absolute():
        dataset_path = backend_dir / dataset_path

    if not dataset_path.exists():
        logger.error("数据集文件不存在: %s", dataset_path)
        logger.info("请先运行: python eval/dataset_builder/build_dataset.py --kb-ids 1 --output %s", dataset_path)
        sys.exit(1)

    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    qa_count = len(dataset.get("qa_pairs", []))
    logger.info("数据集: %d 条 QA 对", qa_count)

    if qa_count == 0:
        logger.error("数据集中没有 QA 对")
        sys.exit(1)

    # LLM 配置
    llm_config = {
        "api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
        "base_url": os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "model": os.environ.get("EVAL_LLM_MODEL", os.environ.get("DASHSCOPE_MODEL_NAME", "qwen3.5-flash-2026-02-23")),
    }

    kb_ids = config.get("dataset", {}).get("kb_ids") or [1]

    # 运行评估
    results = {}

    async def _run():
        nonlocal results

        # 初始化应用（确保 DB、ChromaDB、Docstore 加载）
        try:
            from app.main import app
            from app.db.session import SessionLocal
            logger.info("应用初始化完成")
        except Exception as e:
            logger.warning("应用初始化失败（可能影响数据库查询）: %s", e)

        if "retrieval" in stages:
            logger.info("=" * 60)
            logger.info("检索评估开始")
            logger.info("=" * 60)
            from eval.retrieval.compare import run_retrieval_comparison

            retrieval_config = config.get("retrieval", {})
            modes = retrieval_config.get("compare_modes", [
                {"name": "hybrid_default", "params": {"retrieval_mode": "hybrid", "auto_merging_thresh": 0.5, "top_k": 5}}
            ])
            group_by = retrieval_config.get("group_by", ["question_type"])

            results["retrieval"] = await run_retrieval_comparison(
                dataset=dataset,
                modes=modes,
                group_by_types=group_by,
            )
            logger.info("检索评估完成")

        if "answer" in stages:
            logger.info("=" * 60)
            logger.info("回答评估开始")
            logger.info("=" * 60)
            from eval.answer.evaluator import run_answer_evaluation

            answer_config = config.get("answer", {})
            ragas_metrics = answer_config.get("ragas_metrics", ["faithfulness", "context_recall"])
            generate_prompt = answer_config.get("generate_prompt")
            top_k = answer_config.get("top_k", 5)

            results["answer"] = await run_answer_evaluation(
                dataset=dataset,
                kb_ids=kb_ids,
                ragas_metrics=ragas_metrics,
                generate_prompt=generate_prompt,
                llm_config=llm_config,
                top_k=top_k,
            )
            logger.info("回答评估完成")

        if "e2e" in stages:
            logger.info("=" * 60)
            logger.info("端到端评估开始")
            logger.info("=" * 60)
            from eval.e2e.evaluator import run_e2e_evaluation

            e2e_config = config.get("e2e", {})
            chat_endpoint = e2e_config.get("chat_endpoint", "http://localhost:8000/api/chat")
            extra_metrics = e2e_config.get("extra_metrics", ["fact_coverage", "key_phrase_recall", "token_cost", "latency"])

            results["e2e"] = await run_e2e_evaluation(
                dataset=dataset,
                chat_endpoint=chat_endpoint,
                extra_metrics=extra_metrics,
                llm_config=llm_config,
                kb_ids=kb_ids,
            )
            logger.info("端到端评估完成")

    # 使用 asyncio.run 作为唯一入口，Windows 兼容
    asyncio.run(_run())

    # 生成报告
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    output_dir = args.output_dir or str(backend_dir / "eval" / "results" / timestamp)
    output_dir = Path(output_dir)

    dataset_info = dataset.get("stats", {})
    dataset_info["source_kb_ids"] = dataset.get("source_kb_ids", [])

    from eval.report.generator import generate_report
    json_path, md_path = generate_report(results, output_dir, dataset_info=dataset_info)

    logger.info("=" * 60)
    logger.info("评估完成！")
    logger.info("JSON 报告: %s", json_path)
    logger.info("Markdown 报告: %s", md_path)
    logger.info("=" * 60)


def _load_config(config_path: str | None) -> dict:
    """加载配置文件"""
    if config_path is None:
        # 搜索默认位置
        backend_dir = Path(__file__).resolve().parent.parent
        default_paths = [
            backend_dir / "eval" / "eval_config.yaml",
            Path("eval_config.yaml"),
        ]
        for p in default_paths:
            if p.exists():
                config_path = str(p)
                break

    if config_path is None:
        logger.warning("未找到配置文件，使用默认配置")
        return _default_config()

    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        logger.info("配置文件: %s", config_path)
        return config
    except ImportError:
        logger.error("pyyaml 未安装，请运行: pip install pyyaml")
        return _default_config()
    except Exception as e:
        logger.error("加载配置文件失败: %s", e)
        return _default_config()


def _default_config() -> dict:
    """默认配置"""
    return {
        "dataset": {"path": "eval/dataset_builder/qa_dataset.json", "kb_ids": [1]},
        "retrieval": {
            "enabled": True,
            "compare_modes": [
                {"name": "hybrid_default", "params": {"retrieval_mode": "hybrid", "auto_merging_thresh": 0.5, "top_k": 5}},
            ],
        },
        "answer": {
            "enabled": True,
            "ragas_metrics": ["faithfulness", "context_recall"],
        },
        "e2e": {"enabled": False},
    }


def _resolve_stages(stage_arg: str | None, config: dict) -> list[str]:
    """确定要运行的评估阶段"""
    if stage_arg:
        if stage_arg == "all":
            return ["retrieval", "answer", "e2e"]
        return [s.strip() for s in stage_arg.split(",")]

    # 从配置中读取启用的阶段
    stages = []
    if config.get("retrieval", {}).get("enabled", True):
        stages.append("retrieval")
    if config.get("answer", {}).get("enabled", True):
        stages.append("answer")
    if config.get("e2e", {}).get("enabled", False):
        stages.append("e2e")

    return stages or ["retrieval", "answer"]


if __name__ == "__main__":
    main()
