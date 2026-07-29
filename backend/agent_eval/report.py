"""Agent 全链路评估 — 报告生成器。

从评估结果生成 Markdown 报告:
  - 总体指标概览
  - 效率/鲁棒性/UX/端到端 四维指标表
  - 按场景分组指标
  - 基线对比（Agent vs Dense vs Hybrid）
  - 优化建议
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

logger = logging.getLogger(__name__)


def _fmt(val) -> str:
    """格式化指标值，NaN/None → N/A。"""
    if val is None:
        return "N/A"
    if isinstance(val, float):
        if math.isnan(val):
            return "N/A"
        if abs(val) < 10:
            return f"{val:.4f}"
        if abs(val) < 1000:
            return f"{val:.1f}"
        return f"{val:.0f}"
    return str(val)


def _fmt_ms(val) -> str:
    """格式化毫秒值。"""
    if val is None:
        return "N/A"
    if isinstance(val, float) and math.isnan(val):
        return "N/A"
    v = float(val)
    if v < 1000:
        return f"{v:.0f}ms"
    return f"{v / 1000:.1f}s"


def _fmt_rate(val) -> str:
    """格式化比率值。"""
    if val is None:
        return "N/A"
    if isinstance(val, float) and math.isnan(val):
        return "N/A"
    return f"{float(val) * 100:.1f}%"


def generate_agent_report(result_dir: str | Path) -> Path:
    """从评估结果目录生成 Markdown 报告。"""
    result_dir = Path(result_dir)
    summary_path = result_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"summary.json not found: {summary_path}")

    with open(summary_path, encoding="utf-8") as f:
        summary = json.load(f)

    lines = _build_report(summary)
    report_path = result_dir / "agent_eval_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("Agent eval report saved: %s", report_path)
    return report_path


def _build_report(s: dict) -> list[str]:
    lines = []

    # ── 标题 ──
    lines.append("# Agent 全链路评估报告\n")
    lines.append(f"> 生成时间: {s.get('timestamp', 'N/A')} | 样本数: {s.get('dataset_size', 'N/A')} | KB: {s.get('kb_id', 'N/A')}\n")

    metrics = s.get("metrics", {})

    # ── 总体概览 ──
    lines.append("## 总体概览\n")
    lines.append("| 维度 | 核心指标 | 值 |")
    lines.append("|------|---------|-----|")

    rob = metrics.get("robustness", {})
    eff = metrics.get("efficiency", {})
    ux = metrics.get("ux", {})
    e2e = metrics.get("e2e", {})

    lines.append(f"| 鲁棒性 | 任务完成率 | {_fmt_rate(rob.get('task_completion_rate'))} |")
    lines.append(f"| 鲁棒性 | Reranker可用率 | {_fmt_rate(rob.get('reranker_availability_rate'))} |")
    lines.append(f"| 效率 | 平均工具调用 | {_fmt(eff.get('tool_call_count', {}).get('mean'))} |")
    lines.append(f"| 效率 | 平均迭代轮数 | {_fmt(eff.get('iteration_rounds', {}).get('mean'))} |")
    lines.append(f"| UX | 平均总延迟 | {_fmt_ms(ux.get('e2e_latency_ms', {}).get('mean'))} |")
    lines.append(f"| UX | P90延迟 | {_fmt_ms(ux.get('e2e_latency_ms', {}).get('p90'))} |")
    lines.append(f"| UX | 首Token延迟 | {_fmt_ms(ux.get('ttft_ms', {}).get('mean'))} |")
    lines.append(f"| 端到端 | 答案准确性 | {_fmt(e2e.get('answer_accuracy'))} |")
    lines.append(f"| 端到端 | 路由准确率 | {_fmt_rate(e2e.get('routing_accuracy'))} |")
    lines.append("")

    # ── 效率指标详表 ──
    lines.append("## 效率指标 (Efficiency)\n")
    tc = eff.get("tool_call_count", {})
    ir = eff.get("iteration_rounds", {})
    lines.append("| 指标 | 均值 | 最小 | 最大 | P50 | P90 |")
    lines.append("|------|------|------|------|-----|-----|")
    lines.append(f"| 工具调用次数 | {_fmt(tc.get('mean'))} | {_fmt(tc.get('min'))} | {_fmt(tc.get('max'))} | {_fmt(tc.get('p50'))} | {_fmt(tc.get('p90'))} |")
    lines.append(f"| 迭代轮数 | {_fmt(ir.get('mean'))} | {_fmt(ir.get('min'))} | {_fmt(ir.get('max'))} | - | - |")
    lines.append("")

    # 转化率 & 冗余率
    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 检索→蒸馏转化率 | {_fmt(eff.get('retrieval_distill_rate', {}).get('mean'))} |")
    lines.append(f"| 工具冗余率 | {_fmt(eff.get('tool_redundancy_rate', {}).get('mean'))} |")
    lines.append(f"| LLM Token消耗(均值) | {_fmt(eff.get('llm_tokens_used', {}).get('mean'))} |")
    lines.append("")

    # 工具调用分布
    dist = eff.get("tool_call_distribution", {})
    if dist:
        lines.append("### 工具调用分布\n")
        lines.append("| 工具 | 调用次数 |")
        lines.append("|------|---------|")
        for tool, count in sorted(dist.items(), key=lambda x: -x[1]):
            lines.append(f"| {tool} | {count} |")
        lines.append("")

    # ── 鲁棒性指标 ──
    lines.append("## 鲁棒性指标 (Robustness)\n")
    lines.append("| 指标 | 值 | 详情 |")
    lines.append("|------|-----|------|")
    lines.append(f"| 任务完成率 | {_fmt_rate(rob.get('task_completion_rate'))} | {rob.get('task_completed', 0)}/{rob.get('task_total', 0)} |")
    lines.append(f"| 工具错误恢复率 | {_fmt_rate(rob.get('tool_error_recovery_rate'))} | 工具错误{rob.get('tool_error_count', 0)}次 |")
    lines.append(f"| 降级触发率 | {_fmt_rate(rob.get('fallback_trigger_rate'))} | 降级{rob.get('fallback_count', 0)}次 |")
    lines.append(f"| 超时率 | {_fmt_rate(rob.get('timeout_rate'))} | 超时{rob.get('timeout_count', 0)}次 |")
    lines.append(f"| Reranker可用率 | {_fmt_rate(rob.get('reranker_availability_rate'))} | {rob.get('reranker_succeeded', 0)}/{rob.get('reranker_called', 0)} |")
    lines.append(f"| 错误率 | {_fmt_rate(rob.get('error_rate'))} | - |")
    lines.append("")

    # ── UX指标 ──
    lines.append("## 用户体验指标 (User Experience)\n")
    e2e_lat = ux.get("e2e_latency_ms", {})
    ttft = ux.get("ttft_ms", {})
    ret_ph = ux.get("retrieval_phase_ms", {})
    gen_ph = ux.get("generation_phase_ms", {})

    lines.append("| 指标 | 均值 | P50 | P90 | 最小 | 最大 |")
    lines.append("|------|------|-----|-----|------|------|")
    lines.append(f"| 总延迟 | {_fmt_ms(e2e_lat.get('mean'))} | {_fmt_ms(e2e_lat.get('p50'))} | {_fmt_ms(e2e_lat.get('p90'))} | {_fmt_ms(e2e_lat.get('min'))} | {_fmt_ms(e2e_lat.get('max'))} |")
    lines.append(f"| 首Token延迟 | {_fmt_ms(ttft.get('mean'))} | {_fmt_ms(ttft.get('p50'))} | {_fmt_ms(ttft.get('p90'))} | {_fmt_ms(ttft.get('min'))} | {_fmt_ms(ttft.get('max'))} |")
    lines.append(f"| 检索阶段 | {_fmt_ms(ret_ph.get('mean'))} | {_fmt_ms(ret_ph.get('p50'))} | - | - | - |")
    lines.append(f"| 生成阶段 | {_fmt_ms(gen_ph.get('mean'))} | {_fmt_ms(gen_ph.get('p50'))} | - | - | - |")
    lines.append("")

    # ── 端到端指标 ──
    lines.append("## 端到端任务完成指标 (End-to-End)\n")
    msq = e2e.get("multi_source_quality", {})
    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 答案准确性 | {_fmt(e2e.get('answer_accuracy'))} |")
    lines.append(f"| 信息来源标注率 | {_fmt_rate(e2e.get('citation_coverage_rate'))} |")
    lines.append(f"| 路由准确率 | {_fmt_rate(e2e.get('routing_accuracy'))} |")
    lines.append(f"| 多源综合完成率 | {_fmt_rate(msq.get('completion_rate'))} | ({msq.get('completed', 0)}/{msq.get('total_multi_source', 0)}) |")
    lines.append("")

    # ── 按场景分组 ──
    grouped = metrics.get("grouped", {})
    if grouped:
        lines.append("## 按场景分组\n")
        for scenario, gm in sorted(grouped.items()):
            lines.append(f"### {scenario} ({gm.get('sample_count', 0)} 样本)\n")
            grob = gm.get("robustness", {})
            geff = gm.get("efficiency", {})
            gux = gm.get("ux", {})

            lines.append("| 指标 | 值 |")
            lines.append("|------|-----|")
            lines.append(f"| 任务完成率 | {_fmt_rate(grob.get('task_completion_rate'))} |")
            lines.append(f"| 平均工具调用 | {_fmt(geff.get('tool_call_count', {}).get('mean'))} |")
            lines.append(f"| 平均迭代轮数 | {_fmt(geff.get('iteration_rounds', {}).get('mean'))} |")
            lines.append(f"| 平均延迟 | {_fmt_ms(gux.get('e2e_latency_ms', {}).get('mean'))} |")
            lines.append(f"| 首Token延迟 | {_fmt_ms(gux.get('ttft_ms', {}).get('mean'))} |")
            lines.append("")

    # ── 基线对比 ──
    baselines = s.get("baselines", {})
    if baselines.get("dense") or baselines.get("hybrid"):
        lines.append("## 基线对比\n")
        lines.append("| 模式 | 样本数 | 平均延迟 |")
        lines.append("|------|--------|---------|")

        dense = baselines.get("dense", [])
        hybrid = baselines.get("hybrid", [])

        if dense:
            avg_d = sum(d.get("latency_ms", 0) for d in dense) / max(len(dense), 1)
            lines.append(f"| Dense | {len(dense)} | {_fmt_ms(avg_d)} |")
        if hybrid:
            avg_h = sum(h.get("latency_ms", 0) for h in hybrid) / max(len(hybrid), 1)
            lines.append(f"| Hybrid | {len(hybrid)} | {_fmt_ms(avg_h)} |")

        # Agent 延迟
        e2e_lat = ux.get("e2e_latency_ms", {})
        lines.append(f"| Agent | {metrics.get('sample_count', 0)} | {_fmt_ms(e2e_lat.get('mean'))} |")
        lines.append("")

    # ── 优化建议 ──
    lines.append("## 优化建议\n")
    suggestions = _generate_suggestions(metrics)
    if suggestions:
        for sug in suggestions:
            lines.append(f"- {sug}")
    else:
        lines.append("所有指标表现良好，暂无紧急优化项。")
    lines.append("")

    return lines


def _generate_suggestions(metrics: dict) -> list[str]:
    """基于指标值生成优化建议。"""
    sugs = []

    rob = metrics.get("robustness", {})
    eff = metrics.get("efficiency", {})
    ux = metrics.get("ux", {})

    # 鲁棒性
    completion = rob.get("task_completion_rate")
    if completion is not None and completion < 0.9:
        sugs.append(f"任务完成率偏低 ({_fmt_rate(completion)}) → 排查失败样本，加强工具容错")

    reranker_avail = rob.get("reranker_availability_rate")
    if reranker_avail is not None and reranker_avail < 0.8:
        sugs.append(f"Reranker可用率偏低 ({_fmt_rate(reranker_avail)}) → 预加载CrossEncoder模型，启动时warmup")

    fallback = rob.get("fallback_trigger_rate")
    if fallback is not None and fallback > 0.1:
        sugs.append(f"降级触发率偏高 ({_fmt_rate(fallback)}) → 排查API稳定性，增加本地模型备选")

    # 效率
    tool_mean = eff.get("tool_call_count", {}).get("mean")
    if tool_mean is not None and tool_mean > 8:
        sugs.append(f"平均工具调用偏高 ({tool_mean:.1f}) → 优化Prompt减少冗余调用，合并检索步骤")

    redundancy = eff.get("tool_redundancy_rate", {}).get("mean")
    if redundancy is not None and redundancy > 0.1:
        sugs.append(f"工具冗余率偏高 ({_fmt(redundancy)}) → 添加去重逻辑，避免重复查询同一关键词")

    distill_rate = eff.get("retrieval_distill_rate", {}).get("mean")
    if distill_rate is not None and distill_rate < 0.5:
        sugs.append(f"检索→蒸馏转化率偏低 ({_fmt(distill_rate)}) → 优化蒸馏Prompt，提升事实点质量")

    # UX
    e2e_mean = ux.get("e2e_latency_ms", {}).get("mean")
    if e2e_mean is not None:
        if e2e_mean > 30000:
            sugs.append(f"总延迟偏高 ({_fmt_ms(e2e_mean)}) → 引入Redis缓存，优化检索并行度")
        elif e2e_mean > 15000:
            sugs.append(f"总延迟中等 ({_fmt_ms(e2e_mean)}) → 可优化Reranker预加载和LLM连接复用")

    ttft_mean = ux.get("ttft_ms", {}).get("mean")
    if ttft_mean is not None and ttft_mean > 5000:
        sugs.append(f"首Token延迟偏高 ({_fmt_ms(ttft_mean)}) → 优化Agent初始化，减少首次调用前处理")

    return sugs
