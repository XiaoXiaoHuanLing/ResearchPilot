"""Agent 全链路评估 — 指标计算模块。

4 类指标:
  - 效率指标 (Efficiency)
  - 鲁棒性指标 (Robustness)
  - 用户体验指标 (User Experience)
  - 端到端任务完成指标 (End-to-End Task)

输入: list[AgentTrace] + 可选 RAGAS 分数
输出: MetricsReport (dict-like)
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Optional

from agent_eval.trace_collector import AgentTrace, ToolCallRecord

logger = logging.getLogger(__name__)

# ─── 辅助 ──────────────────────────────────────────────────────


def _safe_mean(vals: list[float | int | None]) -> float | None:
    """NaN-safe 均值，跳过 None/NaN。"""
    clean = []
    for v in vals:
        if v is None:
            continue
        fv = float(v)
        if not math.isnan(fv):
            clean.append(fv)
    return round(sum(clean) / len(clean), 4) if clean else None


def _safe_rate(numerator: int, denominator: int) -> float | None:
    """安全比率计算。"""
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


# ─── 指标计算 ──────────────────────────────────────────────────


def compute_efficiency_metrics(traces: list[AgentTrace]) -> dict:
    """效率指标: 工具调用/迭代/耗时/转化率/冗余率"""
    if not traces:
        return {}

    tool_counts = [len(t.tool_calls) for t in traces]
    tokens = [t.total_tokens for t in traces if t.total_tokens > 0]

    # 迭代轮数: 按 search_knowledge 出现次数推断
    iteration_rounds = []
    for t in traces:
        search_count = sum(1 for tc in t.tool_calls if tc.tool_name == "search_knowledge")
        iteration_rounds.append(max(1, search_count))

    # 检索→蒸馏转化率: 蒸馏事实点数/召回节点数
    # 事实点从 final_answer 中解析（"事实1:"/"事实2:" 格式）
    distill_rates = []
    for t in traces:
        total_retrieved = sum(
            1 for tc in t.tool_calls
            if tc.tool_name in ("search_knowledge", "get_recall_nodes")
        )
        fact_count = _count_facts(t.final_answer)
        if total_retrieved > 0:
            distill_rates.append(fact_count / total_retrieved)

    # 工具冗余率: 同一工具同一输入重复调用
    redundancy_rates = []
    for t in traces:
        if not t.tool_calls:
            redundancy_rates.append(0.0)
            continue
        # 按 (tool_name, input_preview) 去重
        seen = set()
        redundant = 0
        for tc in t.tool_calls:
            key = (tc.tool_name, tc.input_preview[:50])
            if key in seen:
                redundant += 1
            seen.add(key)
        redundancy_rates.append(redundant / len(t.tool_calls))

    return {
        "tool_call_count": {
            "mean": _safe_mean(tool_counts),
            "min": min(tool_counts),
            "max": max(tool_counts),
            "p50": _percentile(tool_counts, 50),
            "p90": _percentile(tool_counts, 90),
        },
        "iteration_rounds": {
            "mean": _safe_mean(iteration_rounds),
            "min": min(iteration_rounds),
            "max": max(iteration_rounds),
        },
        "llm_tokens_used": {
            "mean": _safe_mean(tokens) if tokens else None,
            "total": sum(tokens) if tokens else 0,
        },
        "retrieval_distill_rate": {
            "mean": _safe_mean(distill_rates) if distill_rates else None,
        },
        "tool_redundancy_rate": {
            "mean": _safe_mean(redundancy_rates) if redundancy_rates else None,
        },
        # 工具调用分布
        "tool_call_distribution": _tool_call_distribution(traces),
    }


def compute_robustness_metrics(traces: list[AgentTrace]) -> dict:
    """鲁棒性指标: 完成率/错误恢复/降级率/超时/reranker可用率"""
    if not traces:
        return {}

    total = len(traces)
    completed = sum(1 for t in traces if t.task_completed)
    has_error = sum(1 for t in traces if t.error)
    has_fallback = sum(1 for t in traces if t.fallback_triggered)

    # 工具错误恢复率: 工具报错后仍完成任务的次数 / 工具报错总次数
    tool_errors = 0
    tool_error_recovered = 0
    for t in traces:
        tool_err_count = sum(1 for tc in t.tool_calls if not tc.success)
        if tool_err_count > 0:
            tool_errors += tool_err_count
            if t.task_completed:
                tool_error_recovered += 1  # 至少完成了一个任务

    # 超时率: end_time - start_time > 300s
    timeouts = sum(1 for t in traces if (t.end_time - t.start_time) > 300)

    # Reranker 可用率
    reranker_total = sum(1 for t in traces if t.reranker_called)
    reranker_ok = sum(1 for t in traces if t.reranker_called and t.reranker_succeeded)

    return {
        "task_completion_rate": _safe_rate(completed, total),
        "task_completed": completed,
        "task_total": total,
        "tool_error_recovery_rate": _safe_rate(tool_error_recovered, tool_errors) if tool_errors > 0 else None,
        "tool_error_count": tool_errors,
        "fallback_trigger_rate": _safe_rate(has_fallback, total),
        "fallback_count": has_fallback,
        "timeout_rate": _safe_rate(timeouts, total),
        "timeout_count": timeouts,
        "reranker_availability_rate": _safe_rate(reranker_ok, reranker_total) if reranker_total > 0 else None,
        "reranker_called": reranker_total,
        "reranker_succeeded": reranker_ok,
        "error_rate": _safe_rate(has_error, total),
    }


def compute_ux_metrics(traces: list[AgentTrace]) -> dict:
    """用户体验指标: TTFT/总延迟/检索阶段/生成阶段"""
    if not traces:
        return {}

    # TTFT
    ttfts = []
    for t in traces:
        if t.first_token_time is not None and t.first_token_time > t.start_time:
            ttfts.append((t.first_token_time - t.start_time) * 1000)  # ms

    # 总延迟
    e2e_latencies = [(t.end_time - t.start_time) * 1000 for t in traces]

    # 检索阶段耗时: 从第一个search工具到最后一个rerank/get_recall_nodes
    retrieval_phases = []
    generation_phases = []
    for t in traces:
        search_tools = [tc for tc in t.tool_calls if tc.tool_name in (
            "search_knowledge", "search_web", "fetch_page",
            "get_recall_nodes", "rerank_recall_pool",
        )]
        if search_tools:
            ret_start = search_tools[0].timestamp
            ret_end = max(tc.timestamp + tc.duration_ms for tc in search_tools)
            retrieval_phases.append(ret_end - ret_start)
            # 生成阶段 = 总 - 检索
            total_ms = (t.end_time - t.start_time) * 1000
            gen_ms = total_ms - (ret_end - ret_start)
            generation_phases.append(max(0, gen_ms))
        else:
            # 无检索工具，全部算生成
            generation_phases.append((t.end_time - t.start_time) * 1000)

    return {
        "ttft_ms": {
            "mean": _safe_mean(ttfts) if ttfts else None,
            "p50": _percentile(ttfts, 50) if ttfts else None,
            "p90": _percentile(ttfts, 90) if ttfts else None,
            "min": min(ttfts) if ttfts else None,
            "max": max(ttfts) if ttfts else None,
        },
        "e2e_latency_ms": {
            "mean": _safe_mean(e2e_latencies),
            "p50": _percentile(e2e_latencies, 50),
            "p90": _percentile(e2e_latencies, 90),
            "min": min(e2e_latencies),
            "max": max(e2e_latencies),
        },
        "retrieval_phase_ms": {
            "mean": _safe_mean(retrieval_phases) if retrieval_phases else None,
            "p50": _percentile(retrieval_phases, 50) if retrieval_phases else None,
        },
        "generation_phase_ms": {
            "mean": _safe_mean(generation_phases) if generation_phases else None,
            "p50": _percentile(generation_phases, 50) if generation_phases else None,
        },
    }


def compute_e2e_metrics(
    traces: list[AgentTrace],
    ragas_scores: list[dict] | None = None,
    expected_routings: list[list[str]] | None = None,
) -> dict:
    """端到端任务完成指标: 准确性/来源标注/路由/多源综合"""
    if not traces:
        return {}

    total = len(traces)

    # 答案准确性: 有 reference 时用 RAGAS FC, 否则用 task_completed 粗估
    accuracy = None
    if ragas_scores:
        fc_vals = [s.get("factual_correctness(mode=f1)") or s.get("factual_correctness")
                   for s in ragas_scores]
        accuracy = _safe_mean(fc_vals)
    else:
        # 无 RAGAS 时，用 task_completed 做粗估
        completed = sum(1 for t in traces if t.task_completed and len(t.final_answer) >= 50)
        accuracy = _safe_rate(completed, total)

    # 信息来源标注率: 检查 final_answer 中是否包含来源标注
    citation_covered = 0
    for t in traces:
        if any(kw in t.final_answer for kw in ("来源", "出处", "参考", "引用", "文档", "[来源")):
            citation_covered += 1

    # 路由准确率
    routing_accuracy = None
    if expected_routings:
        correct = 0
        for t, expected in zip(traces, expected_routings):
            if t.sub_agents_used == sorted(expected):
                correct += 1
            elif set(t.sub_agents_used) >= set(expected):
                # 超出预期但包含预期路由，算部分正确
                correct += 0.5
        routing_accuracy = _safe_rate(int(correct * 2), total * 2)  # 0.5权重处理

    # 多源综合质量: 使用了多个 sub_agent 时的完成率
    multi_source_traces = [t for t in traces if len(t.sub_agents_used) >= 2]
    multi_source_completed = sum(1 for t in multi_source_traces if t.task_completed)

    return {
        "answer_accuracy": accuracy,
        "citation_coverage_rate": _safe_rate(citation_covered, total),
        "routing_accuracy": routing_accuracy,
        "multi_source_quality": {
            "total_multi_source": len(multi_source_traces),
            "completed": multi_source_completed,
            "completion_rate": _safe_rate(multi_source_completed, len(multi_source_traces))
            if multi_source_traces else None,
        },
    }


# ─── 聚合计算 ──────────────────────────────────────────────────


def compute_all_metrics(
    traces: list[AgentTrace],
    ragas_scores: list[dict] | None = None,
    expected_routings: list[list[str]] | None = None,
    group_by: str | None = None,  # "scenario" / "difficulty"
    records: list[dict] | None = None,
) -> dict:
    """计算全部指标 + 可选分组统计。"""
    result = {
        "efficiency": compute_efficiency_metrics(traces),
        "robustness": compute_robustness_metrics(traces),
        "ux": compute_ux_metrics(traces),
        "e2e": compute_e2e_metrics(traces, ragas_scores, expected_routings),
        "sample_count": len(traces),
    }

    # 分组统计
    if group_by and records:
        group_metrics = {}
        groups: dict[str, list[int]] = defaultdict(list)

        for i, rec in enumerate(records):
            key = rec.get(group_by, "unknown")
            groups[key].append(i)

        for key, indices in groups.items():
            group_traces = [traces[i] for i in indices if i < len(traces)]
            group_ragas = None
            group_routings = None
            if ragas_scores:
                group_ragas = [ragas_scores[i] for i in indices if i < len(ragas_scores)]
            if expected_routings:
                group_routings = [expected_routings[i] for i in indices if i < len(expected_routings)]

            group_metrics[key] = {
                "efficiency": compute_efficiency_metrics(group_traces),
                "robustness": compute_robustness_metrics(group_traces),
                "ux": compute_ux_metrics(group_traces),
                "e2e": compute_e2e_metrics(group_traces, group_ragas, group_routings),
                "sample_count": len(group_traces),
            }

        result["grouped"] = group_metrics

    return result


# ─── 内部辅助 ──────────────────────────────────────────────────


def _count_facts(text: str) -> int:
    """统计蒸馏事实点数量（"事实1:"/"事实2:"格式）。"""
    import re
    if not text:
        return 0
    # 中文事实点格式
    matches = re.findall(r'事实\s*\d+\s*[:：]', text)
    if matches:
        return len(matches)
    # 编号列表格式
    matches = re.findall(r'^\s*\d+\s*[.、：:]\s*', text, re.MULTILINE)
    if matches:
        return len(matches)
    # 1段也算1个事实
    return 1 if len(text) >= 50 else 0


def _tool_call_distribution(traces: list[AgentTrace]) -> dict[str, int]:
    """工具调用次数分布。"""
    counter = Counter()
    for t in traces:
        for tc in t.tool_calls:
            counter[tc.tool_name] += 1
    return dict(counter.most_common())


def _percentile(vals: list[float], p: int) -> float | None:
    """计算百分位数。"""
    if not vals:
        return None
    sorted_vals = sorted(vals)
    idx = max(0, int(len(sorted_vals) * p / 100) - 1)
    return round(sorted_vals[idx], 2)
