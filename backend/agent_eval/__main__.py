"""Agent 全链路评估 — CLI 入口。"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 确保后端目录在 sys.path 中
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def main():
    parser = argparse.ArgumentParser(description="Agent 全链路评估")
    parser.add_argument("--dataset", type=str, default=None, help="Agent评估数据集JSONL路径")
    parser.add_argument("--kb-id", type=int, default=1, help="知识库ID (默认1)")
    parser.add_argument("--max-samples", type=int, default=None, help="限制样本数")
    parser.add_argument("--skip-scenarios", nargs="*", default=None, help="跳过的场景类型")
    parser.add_argument("--no-baselines", action="store_true", help="跳过基线对比")
    parser.add_argument("--no-ragas", action="store_true", help="跳过RAGAS评估")
    parser.add_argument("--verbose", action="store_true", help="详细日志")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from agent_eval import run_agent_evaluation
    asyncio.run(run_agent_evaluation(
        dataset_path=args.dataset,
        kb_id=args.kb_id,
        run_baselines=not args.no_baselines,
        run_ragas=not args.no_ragas,
        max_samples=args.max_samples,
        skip_scenarios=args.skip_scenarios,
    ))


if __name__ == "__main__":
    main()
