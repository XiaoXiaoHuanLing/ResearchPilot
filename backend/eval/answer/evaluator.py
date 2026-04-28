"""回答评估（RAGAS）。

基于检索结果用固定 prompt 生成答案，再用 RAGAS 评估
Faithfulness / Context Recall / Answer Relevancy / Context Precision。
"""

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_GENERATE_PROMPT = (
    "基于以下检索到的上下文回答问题。只使用上下文中的信息，"
    "如果上下文不足以回答，请说明。\n\n"
    "上下文：\n{context}\n\n问题：{question}"
)


async def run_answer_evaluation(
    dataset: dict,
    kb_ids: list[int] | None = None,
    ragas_metrics: list[str] | None = None,
    generate_prompt: str | None = None,
    llm_config: dict | None = None,
    top_k: int = 5,
) -> dict:
    """运行回答评估

    Args:
        dataset: qa_dataset.json 加载后的数据
        kb_ids: 知识库 ID 列表
        ragas_metrics: 要计算的 RAGAS 指标名列表
        generate_prompt: 答案生成 prompt 模板
        llm_config: LLM 配置（用于 RAGAS 评估）
        top_k: 检索 top-K

    Returns:
        {overall: {...}, per_question: [...]}
    """
    qa_pairs = dataset.get("qa_pairs", [])
    if not qa_pairs:
        return {"overall": {}, "per_question": []}

    ragas_metrics = ragas_metrics or ["faithfulness", "context_recall", "answer_relevancy", "context_precision"]
    generate_prompt = generate_prompt or DEFAULT_GENERATE_PROMPT

    # Step 1: 对每个 QA 对执行检索 + 生成答案
    # 保持 qa_id 映射，跳过的条目不入 RAGAS 但保留在 per_question 中
    logger.info("Generating answers for %d questions...", len(qa_pairs))
    samples_data = []          # 成功的样本，送入 RAGAS
    sample_qa_ids = []         # 对应的 qa_id，用于回填分数
    failed_ids = set()         # 失败的 qa_id

    for qa in qa_pairs:
        try:
            sample = await _retrieve_and_generate(qa, kb_ids, top_k, generate_prompt, llm_config)
            samples_data.append(sample)
            sample_qa_ids.append(qa.get("id"))
        except Exception as e:
            logger.warning("Failed for %s: %s", qa.get("id"), e)
            failed_ids.add(qa.get("id"))

    if not samples_data:
        return {"overall": {}, "per_question": []}

    # Step 2: 运行 RAGAS 评估
    logger.info("Running RAGAS evaluation with metrics: %s", ragas_metrics)
    ragas_result = await _run_ragas(samples_data, ragas_metrics, llm_config)

    # Step 3: 组装结果（用 sample_qa_ids 对齐，避免错位）
    per_question = []
    for i, qa in enumerate(qa_pairs):
        qa_id = qa.get("id")
        if qa_id in failed_ids:
            per_question.append({"qa_id": qa_id, "question_type": qa.get("question_type"), "error": "generation_failed"})
            continue

        # 在 samples_data 中找到对应位置
        if qa_id in sample_qa_ids:
            idx = sample_qa_ids.index(qa_id)
            entry = {
                "qa_id": qa_id,
                "question_type": qa.get("question_type"),
                "difficulty": qa.get("difficulty"),
                "response": samples_data[idx].get("response", ""),
                "retrieved_context_ids": samples_data[idx].get("retrieved_context_ids", []),
                "num_retrieved": len(samples_data[idx].get("retrieved_context_ids", [])),
            }
            if ragas_result and "per_question" in ragas_result:
                entry["ragas_scores"] = ragas_result["per_question"].get(str(idx), {})
            per_question.append(entry)

    # Step 4: 汇总
    overall = ragas_result.get("overall", {}) if ragas_result else {}
    overall["total_questions"] = len(qa_pairs)
    overall["answered_questions"] = len(samples_data)

    return {
        "overall": overall,
        "per_question": per_question,
    }


async def _retrieve_and_generate(
    qa: dict,
    kb_ids: list[int] | None,
    top_k: int,
    generate_prompt: str,
    llm_config: dict | None,
) -> dict:
    """检索 + 生成答案"""
    from app.services.knowledge.retriever import hybrid_retrieve

    # 检索
    result = await hybrid_retrieve(qa["user_input"], kb_ids=kb_ids, top_k=top_k)
    citations = result.get("citations", [])

    # 组装上下文
    context_parts = []
    for c in citations:
        snippet = c.get("snippet", "")
        title = c.get("title", "")
        if title:
            context_parts.append(f"[{title}]\n{snippet}")
        else:
            context_parts.append(snippet)

    context_text = "\n---\n".join(context_parts) if context_parts else "（无检索结果）"

    # 用固定 prompt 生成答案
    response = ""
    if llm_config:
        try:
            response = _generate_answer(
                question=qa["user_input"],
                context=context_text,
                prompt_template=generate_prompt,
                llm_config=llm_config,
            )
        except Exception as e:
            logger.warning("Answer generation failed for %s: %s", qa.get("id"), e)
            response = f"（生成失败: {e}）"

    return {
        "user_input": qa["user_input"],
        "response": response,
        "retrieved_contexts": [c.get("snippet", "") for c in citations],
        "retrieved_context_ids": [c.get("node_id", "") for c in citations if c.get("node_id")],
        "reference": qa.get("reference", ""),
        "reference_contexts": qa.get("reference_contexts", []),
        "reference_context_ids": qa.get("reference_context_ids", []),
        "latency_ms": result.get("latency_ms", 0),
    }


def _generate_answer(question: str, context: str, prompt_template: str, llm_config: dict) -> str:
    """用固定 prompt 调 LLM 生成答案"""
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage

    llm = ChatOpenAI(
        model=llm_config.get("model", "qwen3.5-plus-2026-02-15"),
        api_key=llm_config["api_key"],
        base_url=llm_config.get("base_url"),
        streaming=False,
        temperature=0.3,
        max_tokens=1000,
        timeout=60,
    )

    prompt = prompt_template.format(context=context, question=question)
    resp = llm.invoke([HumanMessage(content=prompt)])
    return resp.content.strip()


async def _run_ragas(
    samples_data: list[dict],
    metric_names: list[str],
    llm_config: dict | None,
) -> dict | None:
    """运行 RAGAS 评估"""
    try:
        from ragas import evaluate, EvaluationDataset, SingleTurnSample
        from ragas.metrics import (
            Faithfulness,
            AnswerRelevancy,
            LLMContextPrecisionWithReference,
            ContextRecall,
        )
    except ImportError:
        logger.error("ragas 库未安装。请运行: pip install ragas")
        return None

    if not llm_config:
        logger.error("RAGAS 评估需要 llm_config")
        return None

    # 构建 RAGAS 指标实例
    metric_map = {
        "faithfulness": Faithfulness(),
        "answer_relevancy": AnswerRelevancy(),
        "context_precision": LLMContextPrecisionWithReference(),
        "context_recall": ContextRecall(),
    }

    metrics = []
    for name in metric_names:
        if name in metric_map:
            metrics.append(metric_map[name])
        else:
            logger.warning("Unknown RAGAS metric: %s, skipping", name)

    if not metrics:
        return None

    # 组装 RAGAS 数据集
    samples = []
    for s in samples_data:
        kwargs = {
            "user_input": s.get("user_input", ""),
            "response": s.get("response", ""),
            "reference": s.get("reference", ""),
            "retrieved_contexts": s.get("retrieved_contexts", []),
            "reference_contexts": s.get("reference_contexts", []),
        }
        # 尝试传入 ID 字段（RAGAS v0.4+ 部分版本支持）
        try:
            kwargs["retrieved_context_ids"] = s.get("retrieved_context_ids", [])
            kwargs["reference_context_ids"] = s.get("reference_context_ids", [])
            samples.append(SingleTurnSample(**kwargs))
        except Exception:
            # ID 字段不被当前 RAGAS 版本支持，回退
            kwargs.pop("retrieved_context_ids", None)
            kwargs.pop("reference_context_ids", None)
            samples.append(SingleTurnSample(**kwargs))

    eval_dataset = EvaluationDataset(samples=samples)

    # 配置 RAGAS 用的 LLM
    try:
        from langchain_openai import ChatOpenAI as RAGASChatOpenAI
        ragas_llm = RAGASChatOpenAI(
            model=llm_config.get("model", "qwen3.5-plus-2026-02-15"),
            api_key=llm_config["api_key"],
            base_url=llm_config.get("base_url"),
            streaming=False,
            temperature=0.0,
            timeout=60,
        )

        # 尝试配置 embedding 模型（RAGAS AnswerRelevancy 需要）
        try:
            from langchain_openai import OpenAIEmbeddings
            ragas_embeddings = OpenAIEmbeddings(
                model="text-embedding-v3",
                api_key=llm_config["api_key"],
                base_url=llm_config.get("base_url"),
            )
            result = evaluate(
                eval_dataset,
                metrics=metrics,
                llm=ragas_llm,
                embeddings=ragas_embeddings,
            )
        except Exception:
            # embedding 配置失败，只用 LLM 评估
            result = evaluate(
                eval_dataset,
                metrics=metrics,
                llm=ragas_llm,
            )

    except Exception as e:
        logger.error("RAGAS evaluation failed: %s", e)
        return None

    # 解析结果
    overall = {}
    per_question = {}

    if hasattr(result, "scores"):
        # ragas v0.4+ 返回 EvaluationResult
        for metric in metrics:
            # 用 metric.name 属性获取正确的列名（如 "answer_relevancy"）
            # 而非 __class__.__name__.lower()（会得到 "answerrelevancy"）
            metric_name = getattr(metric, "name", metric.__class__.__name__.lower())
            if hasattr(result, "scores") and metric_name in result.scores:
                scores = result.scores[metric_name]
                overall[metric_name] = round(sum(scores) / len(scores), 4) if scores else 0.0
                for i, s in enumerate(scores):
                    per_question.setdefault(str(i), {})[metric_name] = round(s, 4)
    elif isinstance(result, dict):
        # ragas 旧版返回 dict
        for key, values in result.items():
            if isinstance(values, list):
                overall[key] = round(sum(values) / len(values), 4) if values else 0.0
                for i, v in enumerate(values):
                    per_question.setdefault(str(i), {})[key] = round(v, 4) if isinstance(v, (int, float)) else v
            elif isinstance(values, (int, float)):
                overall[key] = values

    # 统一 metric name 到报告常用名的映射
    name_map = {
        "faithfulness": "faithfulness",
        "answer_relevancy": "answer_relevancy",
        "answerrelevancy": "answer_relevancy",
        "llmcontextprecisionwithreference": "context_precision",
        "context_precision": "context_precision",
        "context_recall": "context_recall",
        "contextrecall": "context_recall",
    }
    mapped_overall = {}
    for k, v in overall.items():
        mapped_overall[name_map.get(k, k)] = v

    mapped_per_question = {}
    for idx, scores in per_question.items():
        mapped_scores = {}
        for k, v in scores.items():
            mapped_scores[name_map.get(k, k)] = v
        mapped_per_question[idx] = mapped_scores

    return {"overall": mapped_overall, "per_question": mapped_per_question}
