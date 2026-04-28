"""报告生成器。

将评估结果输出为 JSON + Markdown 格式。
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_report(
    results: dict,
    output_dir: str | Path,
    dataset_info: dict | None = None,
) -> tuple[Path, Path]:
    """生成评估报告

    Args:
        results: {retrieval: {...}, answer: {...}, e2e: {...}}
        output_dir: 输出目录
        dataset_info: 数据集元信息

    Returns:
        (json_path, md_path)
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    # 写 JSON
    json_path = output_dir / "summary.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    # 写 Markdown
    md_path = output_dir / "summary.md"
    md_content = _build_markdown(results, dataset_info)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    # 写明细文件
    details_dir = output_dir / "details"
    details_dir.mkdir(exist_ok=True)

    if "retrieval" in results and results["retrieval"]:
        with open(details_dir / "retrieval_compare.json", "w", encoding="utf-8") as f:
            json.dump(results["retrieval"], f, ensure_ascii=False, indent=2, default=str)

    if "answer" in results and results["answer"]:
        with open(details_dir / "answer_ragas.json", "w", encoding="utf-8") as f:
            json.dump(results["answer"], f, ensure_ascii=False, indent=2, default=str)

    if "e2e" in results and results["e2e"]:
        with open(details_dir / "e2e_detail.json", "w", encoding="utf-8") as f:
            json.dump(results["e2e"], f, ensure_ascii=False, indent=2, default=str)

    logger.info("Report generated: %s, %s", json_path, md_path)
    return json_path, md_path


def _build_markdown(results: dict, dataset_info: dict | None) -> str:
    """构建 Markdown 报告"""
    lines = []
    lines.append(f"# RAG 评估报告 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    if dataset_info:
        lines.append(f"**数据集**: {dataset_info.get('total', '?')} 条 QA 对")
        if dataset_info.get("by_type"):
            type_str = " / ".join(f"{k}:{v}" for k, v in dataset_info["by_type"].items())
            lines.append(f"**类型分布**: {type_str}")
        lines.append("")

    # ── 检索评估 ──
    if "retrieval" in results and results["retrieval"]:
        lines.append("## 检索评估")
        lines.append("")

        modes = results["retrieval"].get("modes", [])
        if modes:
            # 总体对比表
            lines.append("### 总体指标")
            lines.append("")
            header = "| Mode | Recall@K | Precision@K | MRR | NDCG | Hit Rate | Latency P50 |"
            sep = "|------|----------|-------------|-----|------|----------|-------------|"
            lines.append(header)
            lines.append(sep)

            for mode in modes:
                name = mode.get("name", "?")
                r = mode.get("recall", 0)
                p = mode.get("precision", 0)
                mrr = mode.get("mrr", 0)
                ndcg = mode.get("ndcg", 0)
                hr = mode.get("hit_rate", 0)
                lat = mode.get("latency_p50", 0)
                lines.append(f"| {name} | {r:.4f} | {p:.4f} | {mrr:.4f} | {ndcg:.4f} | {hr:.4f} | {lat}ms |")
            lines.append("")

            # 按问题类型拆分
            for mode in modes:
                by_type = mode.get("by_question_type", {})
                if by_type:
                    lines.append(f"### 按问题类型 — {mode.get('name', '')}")
                    lines.append("")
                    header = "| Type | Recall | Precision | MRR | Hit Rate |"
                    sep = "|------|--------|-----------|-----|----------|"
                    lines.append(header)
                    lines.append(sep)
                    for t, m in sorted(by_type.items()):
                        lines.append(f"| {t} | {m.get('recall', 0):.4f} | {m.get('precision', 0):.4f} | {m.get('mrr', 0):.4f} | {m.get('hit_rate', 0):.4f} |")
                    lines.append("")

    # ── 回答评估 ──
    if "answer" in results and results["answer"]:
        ans = results["answer"]
        overall = ans.get("overall", {})

        lines.append("## 回答评估 (RAGAS)")
        lines.append("")

        if overall:
            lines.append("| Metric | Score |")
            lines.append("|--------|-------|")
            metric_labels = {
                "faithfulness": "Faithfulness",
                "answer_relevancy": "Answer Relevancy",
                "context_recall": "Context Recall",
                "context_precision": "Context Precision",
            }
            for key, label in metric_labels.items():
                if key in overall:
                    lines.append(f"| {label} | {overall[key]:.4f} |")
            lines.append("")

    # ── 端到端评估 ──
    if "e2e" in results and results["e2e"]:
        e2e = results["e2e"]
        overall = e2e.get("overall", {})

        lines.append("## 端到端评估")
        lines.append("")

        if overall:
            lines.append("| 指标 | 均值 | P50 | P95 |")
            lines.append("|------|------|-----|-----|")

            for metric in ["fact_coverage", "key_phrase_recall", "latency_ms"]:
                mean_key = f"{metric}_mean"
                p50_key = f"{metric}_p50"
                p95_key = f"{metric}_p95"
                if mean_key in overall:
                    label_map = {
                        "fact_coverage": "事实覆盖率",
                        "key_phrase_recall": "关键词召回",
                        "latency_ms": "延迟(ms)",
                    }
                    label = label_map.get(metric, metric)
                    lines.append(f"| {label} | {overall[mean_key]} | {overall.get(p50_key, '-')} | {overall.get(p95_key, '-')} |")
            lines.append("")

            if "token_total_tokens_mean" in overall:
                lines.append(f"**平均 Token 用量**: 输入 {overall.get('token_input_tokens_mean', '-')} / 输出 {overall.get('token_output_tokens_mean', '-')} / 总计 {overall.get('token_total_tokens_mean', '-')}")
                if "token_estimated_cost_cny_mean" in overall:
                    lines.append(f"**平均成本**: ¥{overall['token_estimated_cost_cny_mean']}")
                lines.append("")

    # ── 建议 ──
    lines.append("## 建议")
    lines.append("")
    suggestions = _generate_suggestions(results)
    if suggestions:
        for s in suggestions:
            lines.append(f"- {s}")
    else:
        lines.append("暂无自动建议，请根据指标数据手动分析。")

    return "\n".join(lines)


def _generate_suggestions(results: dict) -> list[str]:
    """基于评估结果生成调参建议"""
    suggestions = []

    # 检索建议
    retrieval = results.get("retrieval", {})
    modes = retrieval.get("modes", [])

    if len(modes) >= 2:
        # 找最优模式
        best = max(modes, key=lambda m: m.get("recall", 0))
        dense_modes = [m for m in modes if "dense" in m.get("name", "")]
        hybrid_modes = [m for m in modes if "hybrid" in m.get("name", "")]

        if dense_modes and hybrid_modes:
            dense_recall = max(m.get("recall", 0) for m in dense_modes)
            hybrid_recall = max(m.get("recall", 0) for m in hybrid_modes)
            if hybrid_recall > dense_recall + 0.1:
                suggestions.append(f"混合检索召回率({hybrid_recall:.2f})显著优于纯Dense({dense_recall:.2f})，确认 BM25 是必要组件")
            elif hybrid_recall <= dense_recall + 0.02:
                suggestions.append(f"混合检索优势不明显(Δ={hybrid_recall - dense_recall:.2f})，检查 BM25 索引是否正常")

        # 按类型分析
        for mode in modes:
            by_type = mode.get("by_question_type", {})
            keyword_metrics = by_type.get("keyword", {})
            fact_metrics = by_type.get("fact", {})
            if keyword_metrics and fact_metrics:
                kw_recall = keyword_metrics.get("recall", 0)
                fact_recall = fact_metrics.get("recall", 0)
                if kw_recall < fact_recall - 0.2:
                    suggestions.append(f"keyword 型查询召回({kw_recall:.2f})远低于 fact 型({fact_recall:.2f})，检查 BM25 分词效果")

    # 回答评估建议
    answer = results.get("answer", {})
    overall = answer.get("overall", {})

    if overall:
        faithfulness = overall.get("faithfulness", 1.0)
        if faithfulness < 0.7:
            suggestions.append(f"Faithfulness 偏低({faithfulness:.2f})，答案存在幻觉风险，检查 LLM prompt 或检索上下文质量")

        context_recall = overall.get("context_recall", 1.0)
        if context_recall < 0.7:
            suggestions.append(f"Context Recall 偏低({context_recall:.2f})，检索未覆盖回答所需信息，考虑增大 top_k 或改进分块策略")

        context_precision = overall.get("context_precision", 1.0)
        if context_precision < 0.6:
            suggestions.append(f"Context Precision 偏低({context_precision:.2f})，排序质量不足，考虑加入 Reranker 提升排序精度")

    # 端到端建议
    e2e = results.get("e2e", {})
    e2e_overall = e2e.get("overall", {})
    if e2e_overall:
        latency_mean = e2e_overall.get("latency_ms_mean", 0)
        if latency_mean > 3000:
            suggestions.append(f"端到端延迟较高(均值{latency_mean}ms)，检查 LLM 推理速度或考虑流式输出")

    return suggestions
