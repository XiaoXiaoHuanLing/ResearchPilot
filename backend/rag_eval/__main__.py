"""CLI entry point: generate / evaluate / e2e / report / compare."""
import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def cmd_generate(args):
    from rag_eval.dataset import generate_dataset
    jsonl_path = asyncio.run(generate_dataset(kb_id=args.kb_id, testset_size=args.size))
    print(f"\nDataset saved to: {jsonl_path}")


def cmd_evaluate(args):
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stderr)
    from rag_eval.evaluate import run_evaluation
    result_dir = asyncio.run(run_evaluation(
        dataset_path=args.dataset_path,
        kb_id=args.kb_id,
        top_k=args.top_k,
        retrieval_mode=args.mode,
        eval_model=args.model or None,
    ))
    print(f"\nResults saved to: {result_dir}")


def cmd_e2e(args):
    from rag_eval.dataset import generate_dataset
    from rag_eval.evaluate import run_evaluation

    jsonl_path = asyncio.run(generate_dataset(kb_id=args.kb_id, testset_size=args.size))
    print(f"\nDataset saved to: {jsonl_path}")

    result_dir = asyncio.run(run_evaluation(
        dataset_path=jsonl_path,
        kb_id=args.kb_id,
        top_k=args.top_k,
        retrieval_mode=args.mode,
        eval_model=args.model or None,
    ))
    print(f"\nResults saved to: {result_dir}")


def cmd_report(args):
    from rag_eval.report import generate_report
    report_path = generate_report(args.results_dir)
    print(f"\nReport saved to: {report_path}")


def cmd_compare(args):
    from rag_eval.report import generate_comparison_report
    report_path = generate_comparison_report(args.result_dirs)
    print(f"\nComparison report saved to: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="RAG Evaluation Pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    # generate
    p_gen = sub.add_parser("generate", help="Generate evaluation dataset")
    p_gen.add_argument("--kb-id", type=int, default=2)
    p_gen.add_argument("--size", type=int, default=200)
    p_gen.set_defaults(func=cmd_generate)

    # evaluate
    p_eval = sub.add_parser("evaluate", help="Run RAG evaluation")
    p_eval.add_argument("dataset_path", type=str, help="Path to dataset JSONL")
    p_eval.add_argument("--kb-id", type=int, default=2)
    p_eval.add_argument("--top-k", type=int, default=8)
    p_eval.add_argument("--mode", type=str, default="dense",
                         choices=["dense", "hybrid", "agent"])
    p_eval.add_argument("--model", type=str, default="",
                         help="RAGAS eval LLM model (default: auto from fallback chain)")
    p_eval.set_defaults(func=cmd_evaluate)

    # e2e
    p_e2e = sub.add_parser("e2e", help="Generate + Evaluate end-to-end")
    p_e2e.add_argument("--kb-id", type=int, default=2)
    p_e2e.add_argument("--size", type=int, default=200)
    p_e2e.add_argument("--top-k", type=int, default=8)
    p_e2e.add_argument("--mode", type=str, default="dense",
                        choices=["dense", "hybrid", "agent"])
    p_e2e.add_argument("--model", type=str, default="")
    p_e2e.set_defaults(func=cmd_e2e)

    # report
    p_rep = sub.add_parser("report", help="Generate Markdown report")
    p_rep.add_argument("results_dir", type=str)
    p_rep.set_defaults(func=cmd_report)

    # compare
    p_cmp = sub.add_parser("compare", help="Compare results from different retrieval modes")
    p_cmp.add_argument("result_dirs", type=str, nargs="+")
    p_cmp.set_defaults(func=cmd_compare)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
