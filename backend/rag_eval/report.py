"""RAG evaluation report generator.

Reads evaluation results → Markdown report (single mode or multi-mode comparison).
"""

import json
import logging
import math
from pathlib import Path

logger = logging.getLogger(__name__)

_MODE_LABEL = {
    "dense": "Dense Vector",
    "hybrid": "Hybrid (Dense+BM25→RRF→AutoMerging)",
    "agent": "Agent (multi-turn + Reranker + distillation)",
}

# Human-readable metric descriptions
_METRIC_DESC = {
    "context_precision": "检索排序质量 (AP)",
    "context_recall": "召回完整度",
    "faithfulness": "忠实度 (无幻觉)",
    "answer_relevancy": "答案相关度",
    "factual_correctness(mode=f1)": "事实正确性 (F1)",
    "noise_sensitivity": "噪声敏感度",
}

_METRIC_SHORT = {
    "context_precision": "CP",
    "context_recall": "CR",
    "faithfulness": "Faith",
    "factual_correctness(mode=f1)": "FC",
}


def _fmt(val) -> str:
    """Format a metric value, handling NaN/None gracefully."""
    if val is None:
        return "N/A"
    if isinstance(val, float) and math.isnan(val):
        return "N/A"
    return f"{val:.4f}"


def generate_report(result_dir: str | Path) -> Path:
    """Generate Markdown report from evaluation result directory."""
    result_dir = Path(result_dir)
    summary = _load_summary(result_dir)
    lines = _build_single_report(summary)
    return _write_report(result_dir / "summary.md", lines)


def generate_comparison_report(result_dirs: list[str | Path]) -> Path:
    """Generate cross-mode comparison report from multiple result directories."""
    if len(result_dirs) < 2:
        raise ValueError("Comparison requires ≥ 2 result directories")

    summaries = [_load_summary(Path(rd)) for rd in result_dirs]
    lines = _build_comparison_report(summaries)

    comparison_dir = Path(result_dirs[0]).parent / "comparisons"
    comparison_dir.mkdir(parents=True, exist_ok=True)
    ts = summaries[0].get("timestamp", "unknown")
    return _write_report(comparison_dir / f"comparison_{ts}.md", lines)


# ─── Internal ──────────────────────────────────────────────────────

def _load_summary(result_dir: Path) -> dict:
    sp = result_dir / "summary.json"
    if not sp.exists():
        raise FileNotFoundError(f"summary.json not found: {sp}")
    with open(sp, encoding="utf-8") as f:
        return json.load(f)


def _build_single_report(s: dict) -> list[str]:
    lines = []
    mode = s.get("retrieval_mode", "dense")
    mode_label = _MODE_LABEL.get(mode, mode)

    # Header
    lines.append("# RAG Evaluation Report\n")
    lines.append("## Overview\n")
    lines.extend(_info_table(s, mode_label))

    # RAGAS scores
    ragas = s.get("ragas_scores", {})
    if ragas:
        lines.append("## RAGAS Scores (0–1, higher is better)\n")
        lines.append("| Metric | Score | Description |")
        lines.append("|--------|-------|-------------|")
        for key, val in ragas.items():
            desc = _METRIC_DESC.get(key, "")
            lines.append(f"| {key} | {_fmt(val)} | {desc} |")
        lines.append("")

    # Type/difficulty breakdown
    type_scores = s.get("type_scores", {})
    if type_scores:
        lines.append("## Breakdown by Question Type & Difficulty\n")
        lines.extend(_breakdown_table(type_scores))

    # Suggestions
    lines.append("## Improvement Suggestions\n")
    suggestions = _suggest(ragas, type_scores)
    lines.extend(f"- {sug}" for sug in suggestions) if suggestions else lines.append("All metrics look good — no immediate improvements needed.")
    lines.append("")
    return lines


def _build_comparison_report(summaries: list[dict]) -> list[str]:
    lines = []
    lines.append("# RAG Retrieval Mode Comparison\n")

    # Info table
    mode_labels = [_MODE_LABEL.get(s.get("retrieval_mode", "?"), s.get("retrieval_mode", "?")) for s in summaries]
    lines.append("## Overview\n")
    lines.append("| | " + " | ".join(mode_labels) + " |")
    lines.append("|--|" + "|".join(["------" for _ in summaries]) + "|")
    for field, label in [("dataset_size", "Samples"), ("top_k", "Top-K"),
                          ("eval_model", "Eval Model"), ("rag_elapsed_seconds", "RAG (s)"),
                          ("eval_elapsed_seconds", "Eval (s)")]:
        row = f"| {label} |"
        for s in summaries:
            row += f" {s.get(field, 'N/A')} |"
        lines.append(row)
    lines.append("")

    # Score comparison
    all_keys = list(dict.fromkeys(k for s in summaries for k in s.get("ragas_scores", {})))
    lines.append("## RAGAS Score Comparison\n")
    header = "| Metric |" + "|".join(f" {ml} |" for ml in mode_labels)
    lines.append(header.replace("| ", "|").replace(" |", "|"))
    sep = "|------|" + "|".join(["------" for _ in summaries]) + "|"
    lines.append(sep)

    for key in all_keys:
        row = f"| {key} |"
        for s in summaries:
            val = s.get("ragas_scores", {}).get(key)
            row += f" {_fmt(val)} |"
        lines.append(row)
    lines.append("")

    # Delta analysis (2-mode case)
    if len(summaries) == 2:
        lines.append("## Delta Analysis\n")
        s1, s2 = summaries
        m1, m2 = mode_labels
        lines.append(f"| Metric | {m1} | {m2} | Δ | Verdict |")
        lines.append("|--------|------|------|-----|---------|")
        for key in all_keys:
            v1, v2 = s1.get("ragas_scores", {}).get(key), s2.get("ragas_scores", {}).get(key)
            if v1 is not None and v2 is not None and not math.isnan(v1) and not math.isnan(v2):
                d = v2 - v1
                sign = "+" if d > 0 else ""
                verdict = ("✅ strong" if d > 0.05 else "📈 slight" if d > 0.02
                           else "➡️ even" if d > -0.02 else "📉 slight" if d > -0.05
                           else "❌ regressed")
                lines.append(f"| {key} | {_fmt(v1)} | {_fmt(v2)} | {sign}{d:.4f} | {verdict} |")
        lines.append("")

        # Type breakdown comparison
        ts1, ts2 = s1.get("type_scores", {}), s2.get("type_scores", {})
        common = sorted(set(ts1) & set(ts2))
        if common:
            lines.append("## Type/Difficulty Breakdown Comparison\n")
            focus = ["context_precision", "context_recall", "factual_correctness(mode=f1)"]
            for group in common:
                lines.append(f"### {group}\n")
                lines.append(f"| Metric | {m1} | {m2} | Δ |")
                lines.append("|--------|------|------|-----|")
                for mk in focus:
                    v1, v2 = ts1[group].get(mk), ts2[group].get(mk)
                    if v1 is not None and v2 is not None:
                        d = v2 - v1
                        sign = "+" if d > 0 else ""
                        lines.append(f"| {mk} | {_fmt(v1)} | {_fmt(v2)} | {sign}{d:.4f} |")
                lines.append("")

    return lines


def _info_table(s: dict, mode_label: str) -> list[str]:
    lines = [
        "| Item | Value |",
        "|------|-------|",
        f"| Samples | {s.get('dataset_size', 'N/A')} |",
        f"| KB ID | {s.get('kb_id', 'N/A')} |",
        f"| Mode | **{mode_label}** |",
        f"| Top-K | {s.get('top_k', 'N/A')} |",
        f"| Eval Model | {s.get('eval_model', 'N/A')} |",
        f"| RAG Time | {s.get('rag_elapsed_seconds', 'N/A')}s |",
        f"| Eval Time | {s.get('eval_elapsed_seconds', 'N/A')}s |",
        f"| Timestamp | {s.get('timestamp', 'N/A')} |",
        "",
    ]
    return lines


def _breakdown_table(type_scores: dict) -> list[str]:
    # Determine metric columns from first entry
    first = next(iter(type_scores.values()), {})
    metric_keys = [k for k in first if k != "count"]

    header = "| Group | n | " + " | ".join(_METRIC_SHORT.get(k, k) for k in metric_keys) + " |"
    sep = "|-------|---|" + "|".join(["------" for _ in metric_keys]) + "|"

    lines = [header, sep]
    for group, scores in type_scores.items():
        row = f"| {group} | {scores.get('count', 0)} |"
        for mk in metric_keys:
            row += f" {_fmt(scores.get(mk))} |"
        lines.append(row)
    lines.append("")
    return lines


def _suggest(ragas: dict, type_scores: dict = None) -> list[str]:
    """Generate improvement suggestions based on metric values."""
    sug = []

    cp = ragas.get("context_precision")
    if cp is not None and not math.isnan(cp) and cp < 0.6:
        sug.append(f"CP 偏低 ({cp:.2f}) → 加 Reranker 或调检索排序")

    cr = ragas.get("context_recall")
    if cr is not None and not math.isnan(cr) and cr < 0.7:
        sug.append(f"CR 偏低 ({cr:.2f}) → 增 top_k 或改善 Embedding")

    faith = ragas.get("faithfulness")
    if faith is not None and not math.isnan(faith) and faith < 0.7:
        sug.append(f"Faithfulness 偏低 ({faith:.2f}) → 加强上下文约束 prompt")

    fc_key = next((k for k in ragas if k.startswith("factual_correctness")), None)
    fc = ragas.get(fc_key) if fc_key else None
    if fc is not None and not math.isnan(fc) and fc < 0.6:
        sug.append(f"FC 偏低 ({fc:.2f}) → 改善检索质量或参考答案精度")

    # Type-specific suggestions
    if type_scores:
        hard = type_scores.get("difficulty:hard", {})
        if hard.get("count", 0) > 0:
            hcp = hard.get("context_precision")
            hcr = hard.get("context_recall")
            if hcp is not None and not math.isnan(hcp) and hcp < 0.2:
                sug.append(f"Hard CP 极低 ({hcp:.2f}) → 多跳检索/父节点聚合")
            if hcr is not None and not math.isnan(hcr) and hcr < 0.4:
                sug.append(f"Hard CR 极低 ({hcr:.2f}) → 增 top_k 或 HyDE")

        neg = type_scores.get("type:negation", {})
        if neg.get("count", 0) > 0:
            ncp = neg.get("context_precision")
            if ncp is not None and not math.isnan(ncp) and ncp < 0.3:
                sug.append(f"Negation CP 极低 ({ncp:.2f}) → 查询改写/同义扩展")

    return sug


def _write_report(path: Path, lines: list[str]) -> Path:
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info("Report saved: %s", path)
    return path
