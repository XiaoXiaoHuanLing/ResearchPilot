"""Standalone RAGAS evaluation for existing results that have RAG data but no RAGAS scores.

Usage:
    python rag_eval/run_ragas_only.py <results_dir> [--model MODEL]

Example:
    python rag_eval/run_ragas_only.py rag_eval/data/results/20260511_160614_agent
    python rag_eval/run_ragas_only.py rag_eval/data/results/20260511_160614_agent --model qwen3-14b
"""
import argparse
import asyncio
import json
import time
import sys
from pathlib import Path

# Add backend to path
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


async def run_ragas_only(results_dir: str, eval_model: str | None = None):
    from rag_eval.evaluate import (
        _find_available_eval_model,
        _build_ragas_components,
        _safe_mean,
        _aggregate_by_group,
        logger,
    )
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stderr)

    rdir = Path(results_dir)
    samples_path = rdir / "samples.json"
    summary_path = rdir / "summary.json"

    if not samples_path.exists():
        print(f"ERROR: {samples_path} not found")
        return

    samples = json.loads(samples_path.read_text(encoding="utf-8"))
    config = json.loads((rdir / "config.json").read_text(encoding="utf-8")) if (rdir / "config.json").exists() else {}

    print(f"Loaded {len(samples)} samples from {results_dir}")
    print(f"Mode: {config.get('retrieval_mode', '?')}, top_k: {config.get('top_k', '?')}")

    # Find available eval model
    print(f"Finding available eval model (preferred: {eval_model or 'auto'})...")
    model = _find_available_eval_model(eval_model)
    print(f"Using eval model: {model}")

    # Build RAGAS components
    llm, embeddings, ragas_metrics, resolved_model = _build_ragas_components(eval_model)

    # Build RAGAS samples
    from ragas import SingleTurnSample, evaluate as ragas_evaluate
    from ragas.dataset_schema import EvaluationDataset

    ragas_samples = []
    for s in samples:
        ragas_samples.append(SingleTurnSample(
            user_input=s.get("user_input", ""),
            response=s.get("response", ""),
            reference=s.get("reference", ""),
            reference_contexts=s.get("reference_contexts", []),
            retrieved_contexts=s.get("retrieved_contexts", []),
        ))

    print(f"Running RAGAS evaluation: {len(ragas_samples)} samples, {len(ragas_metrics)} metrics...")

    dataset = EvaluationDataset(samples=ragas_samples)
    t0 = time.time()
    # ragas evaluate() is sync in some versions, async in others
    import inspect
    result = ragas_evaluate(
        dataset=dataset,
        metrics=ragas_metrics,
        llm=llm,
        embeddings=embeddings,
        raise_exceptions=False,
    )
    if inspect.isawaitable(result):
        result = await result
    eval_elapsed = time.time() - t0
    print(f"RAGAS done: {eval_elapsed:.1f}s")

    # Extract scores
    df = result.to_pandas()
    metric_names = [m.name for m in ragas_metrics]

    scores = {}
    for mn in metric_names:
        col = mn
        if col in df.columns:
            scores[col] = _safe_mean(df[col].dropna().tolist())

    # Type scores — use samples as records (they have question_type/difficulty)
    type_scores = _aggregate_by_group(samples, df, metric_names)

    # Update summary
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    summary["ragas_scores"] = scores
    summary["eval_model"] = resolved_model
    summary["eval_elapsed_seconds"] = eval_elapsed
    summary["type_scores"] = type_scores

    # Save per-sample scores back to samples.json
    for i, s in enumerate(samples):
        for mn in metric_names:
            if mn in df.columns:
                val = df.iloc[i].get(mn)
                s[mn] = float(val) if val is not None and str(val) != "nan" else None

    # Write files
    samples_path.write_text(json.dumps(samples, ensure_ascii=False, indent=1), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nRAGAS Scores:")
    for k, v in scores.items():
        print(f"  {k}: {v:.4f}" if v is not None else f"  {k}: null")
    print(f"\nResults updated in: {results_dir}")


def main():
    parser = argparse.ArgumentParser(description="Run RAGAS evaluation only on existing results")
    parser.add_argument("results_dir", type=str, help="Path to results directory")
    parser.add_argument("--model", type=str, default="", help="Preferred eval model")
    args = parser.parse_args()

    asyncio.run(run_ragas_only(args.results_dir, args.model or None))


if __name__ == "__main__":
    main()
