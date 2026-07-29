"""Agent 全链路评估 — 执行器。

对每个测试样本:
  1. 调用 Chat Agent (astream_events)
  2. 收集 AgentTrace
  3. 可选: 跑 RAGAS 指标
  4. 保存结果

支持:
  - 对比基线 (dense / hybrid / agent)
  - 断点续跑
  - 模型切换 (sensenova 无限额度)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _MODULE_DIR.resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_DATA_DIR = _MODULE_DIR / "data"
_RESULTS_DIR = _DATA_DIR / "results"
_RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# Agent 执行 + Trace 收集
# ═══════════════════════════════════════════════════════════════


async def _run_agent_with_trace(
    question: str,
    sample_id: str,
    scenario: str,
    session_id: str | None = None,
) -> tuple[str, dict, object]:
    """调用 Chat Agent 并收集 trace。

    Returns:
        (final_answer, trace_dict, raw_events_summary)
    """
    from datetime import datetime, timezone
    from langchain_core.messages import HumanMessage

    if not session_id:
        session_id = f"eval_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{sample_id}"

    # 初始化 Agent + 上下文
    from app.services.chat.agent import get_chat_agent
    from app.services.chat.tools.thread_context import set_current_thread_id
    from app.services.chat.tools.reflexive_retriever import RecallPoolManager
    from app.services.chat.tools.search import SearchPoolManager

    set_current_thread_id(session_id)
    RecallPoolManager(session_id).reset_for_new_turn()
    SearchPoolManager(session_id).reset_for_new_turn()

    agent = get_chat_agent()
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 25}
    input_msg = {"messages": [HumanMessage(content=question)]}

    # Trace 收集器
    from agent_eval.trace_collector import TraceCollector
    collector = TraceCollector(sample_id=sample_id, scenario=scenario)

    final_answer = ""
    search_used = False
    kb_used = False

    try:
        async for event in agent.astream_events(input_msg, config=config, version="v2"):
            kind = event.get("event", "")
            collector.process_event(event)

            # 提取最终答案
            if kind == "on_chat_model_end":
                output = event.get("data", {}).get("output", {})
                if hasattr(output, "content") and output.content:
                    has_tool_calls = hasattr(output, "tool_calls") and output.tool_calls
                    if not has_tool_calls:
                        final_answer = output.content

            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                if tool_name in ("search_web", "fetch_page", "get_search_content"):
                    search_used = True
                if tool_name in ("search_knowledge", "get_recall_nodes", "rerank_recall_pool", "list_active_kbs"):
                    kb_used = True

    except Exception as e:
        err_msg = str(e)[:300]
        logger.error("Agent execution error for %s: %s", sample_id, err_msg)
        trace = collector.finalize(
            final_answer=final_answer,
            task_completed=False,
            error=err_msg,
        )
        return final_answer, trace.to_dict(), {"error": err_msg, "search_used": search_used, "kb_used": kb_used}

    # 判断任务完成
    task_completed = bool(final_answer) and len(final_answer) >= 20

    trace = collector.finalize(
        final_answer=final_answer,
        task_completed=task_completed,
    )

    # 设置路由正确性
    expected_routing = _get_expected_routing(scenario)
    actual_routing = trace.sub_agents_used
    if expected_routing is not None:
        trace.routing_correct = set(actual_routing) == set(expected_routing)

    return final_answer, trace.to_dict(), {"search_used": search_used, "kb_used": kb_used}


def _get_expected_routing(scenario: str) -> list[str] | None:
    """获取场景对应的预期路由。"""
    from agent_eval.dataset import SCENARIOS
    info = SCENARIOS.get(scenario, {})
    return info.get("expected_routing")


# ═══════════════════════════════════════════════════════════════
# Dense/Hybrid 基线执行
# ═══════════════════════════════════════════════════════════════


async def _run_dense_baseline(question: str, kb_id: int, top_k: int = 8) -> tuple[str, float]:
    """执行 Dense 基线，返回 (answer, latency_ms)。"""
    from app.services.knowledge.engine import rag_query

    t0 = time.time()
    result = await rag_query(question, top_k=top_k, kb_id=kb_id, retrieval_only=False)
    latency = (time.time() - t0) * 1000

    return result.get("answer", ""), latency


async def _run_hybrid_baseline(question: str, kb_id: int, top_k: int = 8) -> tuple[str, float]:
    """执行 Hybrid 基线，返回 (answer, latency_ms)。"""
    from app.services.knowledge.retriever import hybrid_retrieve
    from app.services.knowledge.engine import rag_query

    t0 = time.time()
    result = await hybrid_retrieve(query=question, kb_ids=[kb_id], top_k=top_k, retrieval_mode="hybrid")
    citations = result.get("citations", [])

    # 生成答案
    answer = ""
    if citations:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage
        from app.core.config import settings

        context_block = "\n\n---\n\n".join(
            f"[来源：{c.get('source', '?')}] {c.get('title', '?')}\n{c.get('snippet', '')[:600]}"
            for c in citations[:8]
        )

        try:
            llm = ChatOpenAI(
                model=settings.llm_model, api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                streaming=False, temperature=0.3, max_tokens=1500, timeout=60,
            )
            resp = await llm.ainvoke([
                SystemMessage(content="你是研究助手。基于参考资料回答，引用来源。"),
                HumanMessage(content=f"问题：{question}\n\n参考资料：\n{context_block}"),
            ])
            answer = resp.content or ""
        except Exception as e:
            logger.error("Hybrid baseline answer gen failed: %s", e)

    latency = (time.time() - t0) * 1000
    return answer, latency


# ═══════════════════════════════════════════════════════════════
# 主评估流程
# ═══════════════════════════════════════════════════════════════


async def run_agent_evaluation(
    dataset_path: str | Path | None = None,
    kb_id: int = 1,
    run_baselines: bool = True,
    run_ragas: bool = True,
    max_samples: int | None = None,
    skip_scenarios: list[str] | None = None,
) -> Path:
    """执行 Agent 全链路评估。

    Args:
        dataset_path: Agent 评估数据集路径（None 用内置默认）
        kb_id: 知识库ID（用于 KB 类问题和基线）
        run_baselines: 是否同时跑 dense/hybrid 基线
        run_ragas: 是否跑 RAGAS 指标
        max_samples: 限制样本数（调试用）
        skip_scenarios: 跳过的场景类型

    Returns:
        结果目录路径
    """
    from agent_eval.dataset import load_agent_dataset
    from agent_eval.trace_collector import AgentTrace, save_trace
    from agent_eval.metrics import compute_all_metrics

    # 加载数据集
    records = load_agent_dataset(dataset_path)

    # 过滤
    if skip_scenarios:
        records = [r for r in records if r.get("scenario") not in skip_scenarios]
    if max_samples:
        records = records[:max_samples]

    logger.info("Agent evaluation: %d samples, kb_id=%d, baselines=%s, ragas=%s",
                len(records), kb_id, run_baselines, run_ragas)

    # 断点续跑
    ckpt_path = _RESULTS_DIR / "_agent_checkpoint.json"
    traces_data: list[dict] = []
    baselines_data: dict = {"dense": [], "hybrid": []}
    start_idx = 0

    if ckpt_path.exists():
        try:
            with open(ckpt_path, encoding="utf-8") as f:
                ckpt = json.load(f)
            traces_data = ckpt.get("traces", [])
            baselines_data = ckpt.get("baselines", {"dense": [], "hybrid": []})
            start_idx = ckpt.get("next_idx", 0)
            logger.info("Resuming from checkpoint: %d done, starting at %d", len(traces_data), start_idx)
        except Exception as e:
            logger.warning("Checkpoint load failed: %s", e)

    # ── Phase 1: Agent 执行 + Trace 收集 ──
    t0 = time.time()

    for i, rec in enumerate(records):
        if i < start_idx:
            continue

        sample_id = rec.get("id", f"agent_{i:03d}")
        question = rec["user_input"]
        scenario = rec.get("scenario", "unknown")

        logger.info("[%d/%d] %s (scenario=%s): %s", i + 1, len(records), sample_id, scenario, question[:60])

        # Agent 执行
        try:
            answer, trace_dict, meta = await _run_agent_with_trace(
                question=question,
                sample_id=sample_id,
                scenario=scenario,
            )
            trace_dict["sample_id"] = sample_id
            trace_dict["scenario"] = scenario
            traces_data.append(trace_dict)

            # Dense 基线（仅 KB 类场景）
            if run_baselines and scenario in ("kb_simple", "kb_complex", "hybrid"):
                try:
                    dense_answer, dense_latency = await _run_dense_baseline(question, kb_id=kb_id)
                    baselines_data["dense"].append({
                        "sample_id": sample_id, "answer": dense_answer[:500],
                        "latency_ms": round(dense_latency, 1),
                    })
                except Exception as e:
                    logger.warning("Dense baseline failed for %s: %s", sample_id, e)

                try:
                    hybrid_answer, hybrid_latency = await _run_hybrid_baseline(question, kb_id=kb_id)
                    baselines_data["hybrid"].append({
                        "sample_id": sample_id, "answer": hybrid_answer[:500],
                        "latency_ms": round(hybrid_latency, 1),
                    })
                except Exception as e:
                    logger.warning("Hybrid baseline failed for %s: %s", sample_id, e)

        except Exception as e:
            logger.error("[%d/%d] FAILED: %s", i + 1, len(records), e)
            traces_data.append({
                "sample_id": sample_id, "scenario": scenario,
                "task_completed": False, "error": str(e)[:300],
                "tool_calls": [], "sub_agents_used": [],
            })

        # 限速 — sensenova RPM ~13/min, 每样本间隔5s避免429
        await asyncio.sleep(5)

        # Checkpoint
        if (i + 1) % 5 == 0:
            _save_checkpoint(ckpt_path, traces_data, baselines_data, i + 1)

    agent_elapsed = time.time() - t0
    logger.info("Agent execution done: %.1fs", agent_elapsed)

    # 清理 checkpoint
    if ckpt_path.exists():
        ckpt_path.unlink()

    # ── Phase 2: Metric 计算 ──
    traces = [AgentTrace.from_dict(td) for td in traces_data]
    expected_routings = [rec.get("expected_routing", []) for rec in records[:len(traces)]]

    # 可选 RAGAS
    ragas_scores = None
    if run_ragas:
        ragas_scores = await _run_ragas_for_traces(traces, records[:len(traces)], kb_id)

    all_metrics = compute_all_metrics(
        traces=traces,
        ragas_scores=ragas_scores,
        expected_routings=expected_routings,
        group_by="scenario",
        records=records[:len(traces)],
    )

    # ── Phase 3: 保存结果 ──
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    result_dir = _RESULTS_DIR / f"{timestamp}_agent_eval"
    result_dir.mkdir(parents=True, exist_ok=True)

    # 保存 traces
    for trace in traces:
        save_trace(trace, result_dir)

    # 保存 metrics
    summary = {
        "timestamp": timestamp,
        "dataset_size": len(records),
        "kb_id": kb_id,
        "agent_elapsed_seconds": round(agent_elapsed, 1),
        "metrics": all_metrics,
        "baselines": baselines_data,
    }

    with open(result_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 保存 records（复现用）
    with open(result_dir / "samples.json", "w", encoding="utf-8") as f:
        json.dump(records[:len(traces)], f, ensure_ascii=False, indent=2)

    logger.info("Results saved: %s", result_dir)

    # ── Phase 4: 生成报告 ──
    from agent_eval.report import generate_agent_report
    report_path = generate_agent_report(result_dir)
    logger.info("Report saved: %s", report_path)

    return result_dir


# ─── RAGAS (可选) ──────────────────────────────────────────────


async def _run_ragas_for_traces(
    traces: list[AgentTrace],
    records: list[dict],
    kb_id: int,
) -> list[dict] | None:
    """对有 reference 的样本跑 RAGAS 四指标。"""
    # 过滤有 reference 的样本
    has_ref = [(t, r) for t, r in zip(traces, records) if r.get("reference")]
    if not has_ref:
        logger.info("No samples with reference, skipping RAGAS")
        return None

    logger.info("Running RAGAS for %d samples with reference", len(has_ref))

    try:
        from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
        from ragas import evaluate as ragas_evaluate

        samples = []
        for t, r in has_ref:
            samples.append(SingleTurnSample(
                user_input=r["user_input"],
                response=t.final_answer,
                reference=r.get("reference", ""),
                retrieved_contexts=[],  # Agent trace 无 retrieved_contexts
            ))

        eval_dataset = EvaluationDataset(samples=samples)

        # 构建评估 LLM (使用 sensenova 无限额度)
        from rag_eval.evaluate import _build_ragas_components
        _, _, ragas_metrics, resolved_model = _build_ragas_components()

        result = ragas_evaluate(
            dataset=eval_dataset,
            metrics=ragas_metrics,
            raise_exceptions=False,
            show_progress=True,
        )

        result_df = result.to_pandas()
        scores = []
        idx = 0
        for t, r in zip(traces, records):
            if r.get("reference") and idx < len(result_df):
                row = result_df.iloc[idx]
                scores.append({
                    "factual_correctness(mode=f1)": row.get("factual_correctness(mode=f1)"),
                    "faithfulness": row.get("faithfulness"),
                })
                idx += 1
            else:
                scores.append({})

        return scores

    except Exception as e:
        logger.error("RAGAS evaluation failed: %s", e)
        return None


# ─── Checkpoint ────────────────────────────────────────────────


def _save_checkpoint(path: Path, traces: list, baselines: dict, next_idx: int):
    data = {
        "traces": traces,
        "baselines": baselines,
        "next_idx": next_idx,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        logger.info("Checkpoint saved: %d traces, next_idx=%d", len(traces), next_idx)
    except Exception as e:
        logger.warning("Checkpoint save failed: %s", e)
