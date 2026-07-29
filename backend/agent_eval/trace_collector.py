"""Agent 全链路评估 — Trace 收集器。

在 Agent 执行过程中，通过 astream_events 收集结构化 trace，
包括工具调用记录、时间线、路由决策、错误信息等。

不侵入 Agent 代码，仅在 API 层（chat.py）的事件循环中埋点。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── 数据结构 ───────────────────────────────────────────────────


@dataclass
class ToolCallRecord:
    """单次工具调用记录"""
    tool_name: str
    timestamp: float          # 相对于 trace 开始的毫秒数
    duration_ms: float        # 工具执行耗时
    input_preview: str        # 输入参数摘要（前200字）
    output_preview: str       # 输出摘要（前200字）
    success: bool
    error: str | None = None
    round: int = 0            # 第几轮迭代（按同类型工具出现次数推断）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentTrace:
    """一次 Agent 执行的完整 trace"""
    sample_id: str
    scenario: str = "unknown"

    # 时间线
    start_time: float = 0.0   # 绝对时间戳
    end_time: float = 0.0
    first_token_time: float | None = None  # TTFT（首token时间戳）

    # 工具调用记录
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    # 路由决策
    sub_agents_used: list[str] = field(default_factory=list)
    routing_correct: bool | None = None  # 与 expected_routing 对比

    # 结果
    final_answer: str = ""
    task_completed: bool = False
    error: str | None = None

    # 资源消耗
    total_tokens: int = 0

    # Reranker 状态
    reranker_called: bool = False
    reranker_succeeded: bool = False

    # Fallback 状态
    fallback_triggered: bool = False
    fallback_reason: str = ""

    # 原始事件流（可选，用于深度分析）
    raw_events: list[dict] | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> AgentTrace:
        tool_calls = [ToolCallRecord(**tc) for tc in data.get("tool_calls", [])]
        data["tool_calls"] = tool_calls
        return cls(**data)


# ─── 实时 Trace 收集器 ─────────────────────────────────────────


class TraceCollector:
    """在 astream_events 循环中实时收集 Agent trace。

    用法:
        collector = TraceCollector(sample_id="agent_001", scenario="kb_complex")
        async for event in agent.astream_events(...):
            collector.process_event(event)
            # ... 原有事件处理逻辑 ...
        trace = collector.finalize(final_answer="...", task_completed=True)
    """

    def __init__(self, sample_id: str, scenario: str = "unknown"):
        self.sample_id = sample_id
        self.scenario = scenario
        self._start_time = time.time()
        self._first_token_time: float | None = None
        self._tool_start_times: dict[str, float] = {}  # tool_run_id → start_time
        self._tool_calls: list[ToolCallRecord] = []
        self._sub_agents_used: set[str] = set()
        self._reranker_called = False
        self._reranker_succeeded = False
        self._fallback_triggered = False
        self._fallback_reason = ""
        self._total_tokens = 0
        self._error: str | None = None
        self._raw_events: list[dict] = []
        self._round_counter: dict[str, int] = {}  # tool_name → count

    def process_event(self, event: dict) -> None:
        """处理单个 astream_event，更新 trace 状态。"""
        kind = event.get("event", "")
        name = event.get("name", "")
        data = event.get("data", {})
        ts = time.time()

        # ── 首 token ──
        if kind == "on_chat_model_stream":
            chunk = data.get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                if isinstance(chunk.content, str) and chunk.content.strip():
                    if self._first_token_time is None:
                        self._first_token_time = ts
                elif isinstance(chunk.content, list):
                    for part in chunk.content:
                        if isinstance(part, dict) and part.get("type") == "text" and part.get("text", "").strip():
                            if self._first_token_time is None:
                                self._first_token_time = ts
                            break

        # ── 工具开始 ──
        elif kind == "on_tool_start":
            run_id = event.get("run_id", name + str(ts))
            self._tool_start_times[run_id] = ts

            # 路由记录
            if name in ("search_web", "fetch_page", "get_search_content"):
                self._sub_agents_used.add("searcher")
            elif name in ("search_knowledge", "get_recall_nodes", "rerank_recall_pool", "list_active_kbs"):
                self._sub_agents_used.add("retriever")

            # Reranker 状态
            if name == "rerank_recall_pool":
                self._reranker_called = True

            # 轮次计数
            count = self._round_counter.get(name, 0) + 1
            self._round_counter[name] = count

        # ── 工具结束 ──
        elif kind == "on_tool_end":
            run_id = event.get("run_id", name + str(ts))
            start = self._tool_start_times.pop(run_id, ts)
            duration = (ts - start) * 1000  # ms

            raw_output = data.get("output", "")
            output_str = str(raw_output.content if hasattr(raw_output, "content") else raw_output)
            output_preview = output_str.replace("\n", " ")[:200]

            input_data = event.get("data", {}).get("input", {})
            input_str = json.dumps(input_data, ensure_ascii=False) if isinstance(input_data, dict) else str(input_data)
            input_preview = input_str[:200]

            # Reranker 成功检测
            success = True
            error = None
            if "⚠️" in output_preview or "失败" in output_preview or "error" in output_preview.lower():
                if name == "rerank_recall_pool":
                    self._reranker_succeeded = False
                    success = False
                    error = output_preview[:100]

            if name == "rerank_recall_pool" and "✅" in output_preview:
                self._reranker_succeeded = True

            self._tool_calls.append(ToolCallRecord(
                tool_name=name,
                timestamp=(start - self._start_time) * 1000,
                duration_ms=round(duration, 1),
                input_preview=input_preview,
                output_preview=output_preview,
                success=success,
                error=error,
                round=self._round_counter.get(name, 1),
            ))

        # ── 错误事件 ──
        elif kind == "on_tool_error" or kind == "on_chat_model_error":
            err = str(data.get("error", ""))[:300]
            self._error = err

            # Fallback 检测
            if any(kw in err for kw in ("403", "429", "quota", "AllocationQuota", "rate_limit")):
                self._fallback_triggered = True
                self._fallback_reason = "api_quota"

        # ── 错误（从 exception 中检测）──
        elif kind == "on_chain_error":
            err = str(data.get("error", ""))[:300]
            if err and not self._error:
                self._error = err

    def finalize(
        self,
        final_answer: str = "",
        task_completed: bool = False,
        error: str | None = None,
        total_tokens: int = 0,
    ) -> AgentTrace:
        """结束 trace 收集，返回结构化 AgentTrace。"""
        return AgentTrace(
            sample_id=self.sample_id,
            scenario=self.scenario,
            start_time=self._start_time,
            end_time=time.time(),
            first_token_time=self._first_token_time,
            tool_calls=self._tool_calls,
            sub_agents_used=sorted(self._sub_agents_used),
            routing_correct=None,  # 由 runner 根据 expected_routing 设置
            final_answer=final_answer,
            task_completed=task_completed,
            error=error or self._error,
            total_tokens=total_tokens or self._total_tokens,
            reranker_called=self._reranker_called,
            reranker_succeeded=self._reranker_succeeded,
            fallback_triggered=self._fallback_triggered,
            fallback_reason=self._fallback_reason,
            raw_events=None,  # 不保存原始事件（太大），按需开启
        )


# ─── Trace 存储 ──────────────────────────────────────────────────

_TRACE_DIR: Path | None = None


def _get_trace_dir() -> Path:
    global _TRACE_DIR
    if _TRACE_DIR is None:
        _TRACE_DIR = Path(__file__).resolve().parent / "data" / "traces"
        _TRACE_DIR.mkdir(parents=True, exist_ok=True)
    return _TRACE_DIR


def save_trace(trace: AgentTrace, result_dir: Path | None = None) -> Path:
    """保存 trace JSON 到文件。"""
    if result_dir:
        path = result_dir / f"trace_{trace.sample_id}.json"
    else:
        path = _get_trace_dir() / f"trace_{trace.sample_id}.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(trace.to_dict(), f, ensure_ascii=False, indent=2)

    return path


def load_trace(path: Path) -> AgentTrace:
    """从文件加载 trace。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return AgentTrace.from_dict(data)


def load_traces(result_dir: Path) -> list[AgentTrace]:
    """从结果目录加载所有 trace。"""
    traces = []
    for fp in sorted(result_dir.glob("trace_*.json")):
        try:
            traces.append(load_trace(fp))
        except Exception as e:
            logger.warning("Failed to load trace %s: %s", fp, e)
    return traces
