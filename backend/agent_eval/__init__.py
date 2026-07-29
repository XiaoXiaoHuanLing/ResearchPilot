"""Agent 全链路评估模块。

用法:
    # 快速评估（使用内置默认数据集）
    python -m agent_eval

    # 指定数据集
    python -m agent_eval --dataset path/to/dataset.jsonl

    # 限制样本数（调试用）
    python -m agent_eval --max-samples 5

    # 跳过基线和RAGAS（更快）
    python -m agent_eval --no-baselines --no-ragas
"""

from agent_eval.trace_collector import TraceCollector, AgentTrace, ToolCallRecord, save_trace, load_trace, load_traces
from agent_eval.metrics import compute_all_metrics, compute_efficiency_metrics, compute_robustness_metrics, compute_ux_metrics, compute_e2e_metrics
from agent_eval.dataset import load_agent_dataset, build_mixed_dataset, save_dataset, SCENARIOS
from agent_eval.runner import run_agent_evaluation
from agent_eval.report import generate_agent_report

__all__ = [
    # Trace
    "TraceCollector", "AgentTrace", "ToolCallRecord", "save_trace", "load_trace", "load_traces",
    # Metrics
    "compute_all_metrics", "compute_efficiency_metrics", "compute_robustness_metrics",
    "compute_ux_metrics", "compute_e2e_metrics",
    # Dataset
    "load_agent_dataset", "build_mixed_dataset", "save_dataset", "SCENARIOS",
    # Runner
    "run_agent_evaluation",
    # Report
    "generate_agent_report",
]
