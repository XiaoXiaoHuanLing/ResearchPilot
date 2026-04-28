"""端到端评估。

走 /api/chat 完整链路，评估整体回答质量 + 延迟 + token 成本 + 交互体验。
"""

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


async def run_e2e_evaluation(
    dataset: dict,
    chat_endpoint: str = "http://localhost:8000/api/chat",
    extra_metrics: list[str] | None = None,
    llm_config: dict | None = None,
    kb_ids: list[int] | None = None,
) -> dict:
    """运行端到端评估

    Args:
        dataset: qa_dataset.json 加载后的数据
        chat_endpoint: /api/chat 端点 URL
        extra_metrics: 额外指标列表，如 ["fact_coverage", "key_phrase_recall", "token_cost", "latency"]
        llm_config: LLM 配置（用于 Fact Coverage 等需要 LLM 判断的指标）
        kb_ids: 知识库 ID 列表

    Returns:
        {overall: {...}, per_question: [...]}
    """
    qa_pairs = dataset.get("qa_pairs", [])
    if not qa_pairs:
        return {"overall": {}, "per_question": []}

    extra_metrics = extra_metrics or ["fact_coverage", "key_phrase_recall", "token_cost", "latency"]

    per_question = []

    for qa in qa_pairs:
        try:
            result = await _e2e_single(qa, chat_endpoint, extra_metrics, llm_config, kb_ids)
            per_question.append(result)
        except Exception as e:
            logger.warning("E2E evaluation failed for %s: %s", qa.get("id"), e)

    # 汇总
    overall = _aggregate_e2e(per_question, extra_metrics)

    return {
        "overall": overall,
        "per_question": per_question,
    }


async def _e2e_single(
    qa: dict,
    chat_endpoint: str,
    extra_metrics: list[str],
    llm_config: dict | None,
    kb_ids: list[int] | None,
) -> dict:
    """单个问题的端到端评估"""
    import httpx

    question = qa["user_input"]
    payload = {
        "message": question,
    }
    if kb_ids:
        payload["kb_ids"] = kb_ids

    # 调用 chat API
    start_time = time.time()
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(chat_endpoint, json=payload)
        latency_ms = int((time.time() - start_time) * 1000)

    if resp.status_code != 200:
        logger.warning("Chat API returned %d for %s", resp.status_code, qa.get("id"))
        return {
            "qa_id": qa.get("id"),
            "question_type": qa.get("question_type"),
            "error": f"HTTP {resp.status_code}",
            "latency_ms": latency_ms,
        }

    data = resp.json()
    response = data.get("response", data.get("message", ""))
    token_usage = data.get("token_usage", {})

    # 计算各项指标
    metrics = {"latency_ms": latency_ms}

    if "fact_coverage" in extra_metrics:
        metrics["fact_coverage"] = _compute_fact_coverage(
            qa.get("answer_facts", []), response, llm_config
        )

    if "key_phrase_recall" in extra_metrics:
        metrics["key_phrase_recall"] = _compute_key_phrase_recall(
            qa.get("key_phrases", []), response
        )

    if "token_cost" in extra_metrics:
        metrics["token_cost"] = _compute_token_cost(token_usage)

    if "latency" in extra_metrics:
        metrics["latency_ms"] = latency_ms

    # RAGAS Faithfulness（如需要）
    if "faithfulness" in extra_metrics and llm_config:
        try:
            faith_score = await _compute_faithfulness(question, response, data, llm_config)
            metrics["faithfulness"] = faith_score
        except Exception as e:
            logger.warning("Faithfulness computation failed: %s", e)

    return {
        "qa_id": qa.get("id"),
        "question_type": qa.get("question_type"),
        "difficulty": qa.get("difficulty"),
        "response": response[:500],
        "metrics": metrics,
        "token_usage": token_usage,
    }


def _compute_fact_coverage(answer_facts: list[str], response: str, llm_config: dict | None) -> float:
    """计算原子事实覆盖率

    如果有 llm_config，用 LLM 判断语义覆盖；否则用字符串包含判断。
    """
    if not answer_facts:
        return 1.0

    if not response:
        return 0.0

    covered = 0
    if llm_config:
        # LLM 判断（批量）
        try:
            facts_text = "\n".join(f"- {f}" for f in answer_facts)
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import HumanMessage

            llm = ChatOpenAI(
                model=llm_config.get("model", "qwen3.5-plus-2026-02-15"),
                api_key=llm_config["api_key"],
                base_url=llm_config.get("base_url"),
                streaming=False,
                temperature=0.0,
                max_tokens=200,
                timeout=30,
            )

            prompt = (
                "判断以下每个事实是否被回答所覆盖（语义等价也算覆盖）。\n"
                "对每个事实输出 Y 或 N，用逗号分隔，不要解释。\n\n"
                f"事实：\n{facts_text}\n\n回答：\n{response[:800]}"
            )
            result = llm.invoke([HumanMessage(content=prompt)])
            verdicts = [v.strip().upper() for v in result.content.split(",")]
            for v in verdicts[:len(answer_facts)]:
                if v == "Y":
                    covered += 1
        except Exception:
            # 降级为字符串匹配
            covered = _string_match_facts(answer_facts, response)
    else:
        covered = _string_match_facts(answer_facts, response)

    return round(covered / len(answer_facts), 4)


def _string_match_facts(facts: list[str], response: str) -> int:
    """子字符串匹配事实（兼容中英文）"""
    covered = 0
    for fact in facts:
        # 对中文文本，直接用子串匹配比 split 更可靠
        # 提取事实中的核心短语：去掉标点后取最长的连续非空白片段
        fact_clean = fact.replace("，", "").replace("。", "").replace("、", "").replace("：", "").strip()
        if fact_clean and fact_clean in response:
            covered += 1
        else:
            # 回退：提取 3 字以上的连续片段做子串匹配
            segments = [s for s in fact.replace("，", " ").replace("。", " ").split() if len(s) > 2]
            if any(seg in response for seg in segments):
                covered += 1
    return covered


def _compute_key_phrase_recall(key_phrases: list[str], response: str) -> float:
    """计算关键词命中召回率"""
    if not key_phrases:
        return 1.0
    if not response:
        return 0.0

    hit = sum(1 for phrase in key_phrases if phrase in response)
    return round(hit / len(key_phrases), 4)


def _compute_token_cost(token_usage: dict) -> dict:
    """计算 token 成本"""
    input_tokens = token_usage.get("input_tokens", token_usage.get("prompt_tokens", 0))
    output_tokens = token_usage.get("output_tokens", token_usage.get("completion_tokens", 0))

    # 基于 DashScope qwen3.5 定价（单位：元/千token）
    input_price = 0.0005
    output_price = 0.001

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated_cost_cny": round(input_tokens * input_price / 1000 + output_tokens * output_price / 1000, 6),
    }


async def _compute_faithfulness(question: str, response: str, chat_data: dict, llm_config: dict) -> float:
    """用 RAGAS 计算 Faithfulness"""
    try:
        from ragas import evaluate, EvaluationDataset, SingleTurnSample
        from ragas.metrics import Faithfulness

        # 从 chat 响应中提取检索到的上下文
        contexts = []
        if "citations" in chat_data:
            for c in chat_data["citations"]:
                if "snippet" in c:
                    contexts.append(c["snippet"])
        elif "context" in chat_data:
            contexts = [chat_data["context"]]

        if not contexts:
            return 0.0

        sample = SingleTurnSample(
            user_input=question,
            response=response,
            retrieved_contexts=contexts,
        )

        dataset = EvaluationDataset(samples=[sample])

        from langchain_openai import ChatOpenAI
        ragas_llm = ChatOpenAI(
            model=llm_config.get("model", "qwen3.5-plus-2026-02-15"),
            api_key=llm_config["api_key"],
            base_url=llm_config.get("base_url"),
            streaming=False,
            temperature=0.0,
            timeout=60,
        )

        result = evaluate(dataset, metrics=[Faithfulness()], llm=ragas_llm)

        if isinstance(result, dict):
            scores = result.get("faithfulness", [])
            return scores[0] if scores else 0.0
        elif hasattr(result, "scores"):
            scores = result.scores.get("faithfulness", [])
            return scores[0] if scores else 0.0
        return 0.0

    except Exception as e:
        logger.warning("RAGAS Faithfulness failed: %s", e)
        return 0.0


def _aggregate_e2e(per_question: list[dict], extra_metrics: list[str]) -> dict:
    """汇总端到端评估结果"""
    if not per_question:
        return {}

    n = len(per_question)
    overall = {
        "total_questions": n,
        "successful": sum(1 for q in per_question if "error" not in q),
    }

    # 聚合各指标
    metric_values = {}
    for q in per_question:
        if "error" in q:
            continue
        for mk, mv in q.get("metrics", {}).items():
            if isinstance(mv, (int, float)):
                metric_values.setdefault(mk, []).append(mv)
            elif isinstance(mv, dict) and mk == "token_cost":
                # token_cost 是个 dict
                for tk, tv in mv.items():
                    if isinstance(tv, (int, float)):
                        metric_values.setdefault(f"token_{tk}", []).append(tv)

    for mk, values in metric_values.items():
        if values:
            overall[f"{mk}_mean"] = round(sum(values) / len(values), 4)
            overall[f"{mk}_p50"] = round(sorted(values)[int(len(values) * 0.5)], 4)
            overall[f"{mk}_p95"] = round(sorted(values)[min(int(len(values) * 0.95), len(values) - 1)], 4)

    return overall
