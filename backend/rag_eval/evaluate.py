"""RAG evaluation — metric calculation module.

Pipeline: dataset → per-sample RAG retrieval → answer generation → RAGAS metrics → aggregation → save.

Retrieval modes:
  - dense:  engine.rag_query (ChromaDB vector similarity + LLM answer)
  - hybrid: retriever.hybrid_retrieve (Dense+BM25→RRF→AutoMerging) + LLM answer
  - agent:  Agentic RAG (search_knowledge → RecallPool → Reranker → distilled facts → LLM answer)

RAGAS metrics (4 core):
  ContextPrecision   — retrieval ranking quality      (requires: user_input, retrieved_contexts, reference)
  ContextRecall      — recall completeness            (requires: user_input, retrieved_contexts, reference)
  Faithfulness       — hallucination-free rate         (requires: user_input, retrieved_contexts, response)
  FactualCorrectness — factual alignment (F1)          (requires: response, reference)

LLM call budget per sample:  CP(8) + CR(1) + Faith(2) + FC(4) = 15 calls
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Optional

from ragas.dataset_schema import EvaluationDataset, SingleTurnSample

# ─── Module paths ─────────────────────────────────────────────────

_MODULE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _MODULE_DIR.resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_DATA_DIR = _MODULE_DIR / "data"
_RESULTS_DIR = _DATA_DIR / "results"
_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 8


# ═══════════════════════════════════════════════════════════════════
# DashScope model fallback chain
# ═══════════════════════════════════════════════════════════════════

# Sorted by quality × speed.  * = supports n>1.
_EVAL_MODEL_CHAIN = [
    # Currently available (2026-05-11 17:35, comi confirmed)
    "qwen3.5-plus-2026-04-20",          # ✓ strong, n>1 compatible
    "qwen3-max-2025-09-23",            # ✓ strong
    "qwen3.6-plus-2026-04-02",          # ✓ strong
    "deepseek-v3.2",                    # ✓ strong
    "glm-5.1",                          # ✓ strong (Zhipu)
    "kimi-k2.6",                        # ✓ (Moonshot)
    "qwen3.6-27b",                      # ✓ medium, needs enable_thinking=false
    # Previously exhausted (may have recovered)
    "qwen3-32b",                        # ? needs enable_thinking=false
    "qwen3-14b",                        # ? needs enable_thinking=false
    "qwen3-8b",                         # ? needs enable_thinking=false
    "qwen3-coder-flash",                # ?
]

# Models that MUST set enable_thinking=false (otherwise 400 error).
_NO_THINK_MODELS = frozenset({
    "qwen3-32b", "qwen3-14b", "qwen3-8b", "qwen3-4b",
    "qwen3-1.7b", "qwen3-0.6b", "qwen3.6-27b",
})

# Cache: {model: (available: bool, timestamp: float)}
_MODEL_AVAILABILITY_CACHE: dict[str, tuple[bool, float]] = {}
_CACHE_TTL = 600  # 10 minutes


def _test_model_available(model: str, api_key: str, base_url: str) -> bool:
    """Probe whether a model has available quota.  Results cached for 10 min."""
    now = time.time()
    cached = _MODEL_AVAILABILITY_CACHE.get(model)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    from openai import OpenAI as _OAI
    try:
        c = _OAI(base_url=base_url, api_key=api_key)
        kwargs: dict = dict(model=model, messages=[{"role": "user", "content": "ok"}], max_tokens=5)
        if model in _NO_THINK_MODELS:
            kwargs["extra_body"] = {"enable_thinking": False}
        c.chat.completions.create(**kwargs)
        _MODEL_AVAILABILITY_CACHE[model] = (True, now)
        return True
    except Exception:
        _MODEL_AVAILABILITY_CACHE[model] = (False, now)
        return False


def _find_available_eval_model(preferred: str | None = None) -> str:
    """Walk the fallback chain and return the first model with quota."""
    from app.core.config import settings
    api_key, base_url = settings.eval_llm_api_key, settings.eval_llm_base_url

    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.extend(m for m in _EVAL_MODEL_CHAIN if m != preferred)

    for model in candidates:
        logger.info("Probing %s …", model)
        if _test_model_available(model, api_key, base_url):
            logger.info("Model available: %s", model)
            return model
        logger.warning("Model unavailable: %s", model)

    raise RuntimeError("All eval models unavailable — check DashScope quota")


# ═══════════════════════════════════════════════════════════════════
# LLM helpers
# ═══════════════════════════════════════════════════════════════════

def _chat_llm(
    model: str,
    *,
    temperature: float = 0.0,
    max_tokens: int = 8192,
    timeout: int = 300,
    streaming: bool = False,
) -> "ChatOpenAI":
    """Build a ChatOpenAI with correct enable_thinking handling."""
    from langchain_openai import ChatOpenAI
    from app.core.config import settings

    kwargs: dict = dict(
        model=model,
        api_key=settings.llm_api_key if model == settings.llm_model else settings.eval_llm_api_key,
        base_url=settings.llm_base_url if model == settings.llm_model else settings.eval_llm_base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        streaming=streaming,
    )
    if model in _NO_THINK_MODELS:
        kwargs["extra_body"] = {"enable_thinking": False}
    return ChatOpenAI(**kwargs)


# ═══════════════════════════════════════════════════════════════════
# Retrieval executors
# ═══════════════════════════════════════════════════════════════════

async def _execute_dense_query(question: str, kb_id: int, top_k: int = DEFAULT_TOP_K, **_) -> dict:
    """Dense: vector retrieval (retrieval_only=True) + separate LLM answer generation.
    
    Using retrieval_only=True avoids engine.rag_query's internal LLM call
    which may fail on quota exhaustion. Instead we generate the answer
    separately with the fallback chain.
    """
    from app.services.knowledge.engine import rag_query

    result = await rag_query(question, top_k=top_k, kb_id=kb_id, retrieval_only=True)
    citations = result.get("citations", [])
    # Generate answer separately with fallback chain
    answer = await _generate_answer(question, citations)
    return {
        "answer": answer,
        "citations": citations,
        "retrieved_contexts": [c.get("snippet", "") for c in citations],
        "retrieval_mode": "dense",
    }


async def _execute_hybrid_query(question: str, kb_id: int, top_k: int = DEFAULT_TOP_K, **_) -> dict:
    """Hybrid: Dense+BM25→RRF→AutoMerging, then LLM answer generation."""
    from app.services.knowledge.retriever import hybrid_retrieve

    retrieve_result = await hybrid_retrieve(
        query=question, kb_ids=[kb_id], top_k=top_k, retrieval_mode="hybrid",
    )
    citations = retrieve_result.get("citations", [])
    # _generate_answer already has model fallback chain
    answer = await _generate_answer(question, citations)
    return {
        "answer": answer,
        "citations": citations,
        "retrieved_contexts": [c.get("snippet", "") for c in citations],
        "retrieval_mode": retrieve_result.get("retrieval_mode", "hybrid"),
    }


# ─── Agent mode ────────────────────────────────────────────────────

_AGENT_PROMPT = """\
你是知识库检索专家。采用 Agentic RAG 策略，自主多轮检索循环。

## 核心流程

1. list_active_kbs → 了解可用知识库
2. search_knowledge → 首轮检索（自主决定 query/top_k/kb_ids）
3. get_recall_nodes → 阅读召回节点，判断充分性
4. 不充分 → 改写查询再检索（search_knowledge 最多 3 次）
5. rerank_recall_pool → 精确重排
6. 蒸馏输出事实点

## 蒸馏输出格式（严格遵守）

事实1: [相关原文段落，尽量完整保留]
事实2: [相关原文段落]
事实3: [相关原文段落]
...

要求：至少3条；保留原文（200-500字），不概括不改写不推理；不相关的不要输出。

## 约束
- search_knowledge 最多 3 次
- 知识库没有的信息绝不编造
- 用中文回复，简洁专业
"""


async def _execute_agent_query(
    question: str, kb_id: int, top_k: int = DEFAULT_TOP_K,
    agent_model: str | None = None, **_,
) -> dict:
    """Agent: multi-round retrieval + Reranker + distilled facts + LLM answer."""
    from langchain_openai import ChatOpenAI
    from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    from app.core.config import settings
    from app.services.chat.tools.reflexive_retriever import (
        search_knowledge, get_recall_nodes, rerank_recall_pool,
        RecallPoolManager,
    )
    from app.services.chat.tools.base import list_active_kbs
    from app.services.chat.tools.thread_context import set_current_thread_id

    thread_id = f"eval_{uuid.uuid4().hex[:8]}"
    set_current_thread_id(thread_id)

    # ── Pre-warm: do one search to get initial citations with real scores ──
    # This ensures we always have at least some retrieved contexts even if agent fails.
    from app.services.knowledge.retriever import hybrid_retrieve
    warmup_result = await hybrid_retrieve(
        query=question, kb_ids=[kb_id], top_k=top_k, retrieval_mode="hybrid",
    )
    warmup_citations = warmup_result.get("citations", [])

    tools = [search_knowledge, get_recall_nodes, rerank_recall_pool, list_active_kbs]
    prompt = ChatPromptTemplate.from_messages([
        ("system", _AGENT_PROMPT),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    _model = agent_model or settings.llm_model
    llm = _chat_llm(_model, temperature=0.1, max_tokens=3000, timeout=180)

    agent = create_tool_calling_agent(llm=llm, tools=tools, prompt=prompt)
    executor = AgentExecutor(
        agent=agent, tools=tools,
        max_iterations=12, max_execution_time=300,
        handle_parsing_errors=True, verbose=False,
    )

    # Run agent — re-raise quota errors so model fallback can catch them
    try:
        result = await executor.ainvoke({"input": question})
        agent_answer = result.get("output", "")
    except Exception as e:
        err = str(e)
        if _detect_quota_error(err):
            raise  # Let _run_agent_with_model_fallback switch to next model
        logger.error("Agent failed: %s", e)
        agent_answer = ""

    # Parse facts
    facts = _parse_facts(agent_answer)

    # ── Build citations from RecallPool (with score preservation) ──
    pool = RecallPoolManager(thread_id)
    pool_node_ids = pool.get_all_node_ids()
    recalled = pool.get_nodes(pool_node_ids) if pool_node_ids else []
    pool.clear()

    # Build citations — prefer pool nodes (may have rerank scores), fallback to warmup
    seen_snippets: set[str] = set()
    citations = []
    
    # First: pool nodes (dedup by snippet prefix)
    for nd in sorted(recalled, key=lambda n: n.relevance_score, reverse=True):
        snippet_key = nd.snippet[:80] if nd.snippet else ""
        if snippet_key in seen_snippets:
            continue
        seen_snippets.add(snippet_key)
        citations.append({
            "doc_id": None, "title": nd.title, "source": nd.source,
            "source_type": "", "kb_id": nd.kb_id,
            "relevance_score": nd.relevance_score, "snippet": nd.snippet,
        })

    # If pool has no real scores (all 0.0 from reranker failure), use warmup citations
    pool_has_real_scores = any(c.get("relevance_score", 0) > 0.01 for c in citations)
    if not pool_has_real_scores and warmup_citations:
        logger.info("Pool scores all near-zero, using warmup hybrid citations")
        citations = warmup_citations[:top_k]
    elif len(citations) < 3 and warmup_citations:
        # Supplement with warmup citations not already in pool
        for c in warmup_citations:
            snippet_key = (c.get("snippet", "") or "")[:80]
            if snippet_key not in seen_snippets:
                seen_snippets.add(snippet_key)
                citations.append(c)
                if len(citations) >= top_k:
                    break

    # Generate final answer from facts
    facts_as_citations = [
        {"source": f"fact_{i+1}", "title": f"distilled_fact_{i+1}",
         "snippet": text, "relevance_score": 1.0}
        for i, text in enumerate(facts)
    ]

    answer = ""
    if facts:
        try:
            answer = await _generate_answer(question, facts_as_citations, model_override=_model)
        except Exception as e:
            logger.warning("Answer gen failed: %s", e)
        if not answer or len(answer) < 20:
            answer = "\n".join(f"事实{i+1}: {f}" for i, f in enumerate(facts))
    else:
        # No facts distilled — try generating answer from citations directly
        if citations:
            try:
                answer = await _generate_answer(question, citations, model_override=_model)
            except Exception as e:
                logger.warning("Answer gen from citations failed: %s", e)
        answer = answer or agent_answer or "未找到相关事实"

    # retrieved_contexts = raw snippets (aligned with dense/hybrid for fair RAGAS comparison)
    raw_snippets = [c.get("snippet", "") for c in citations if c.get("snippet", "").strip()]
    return {
        "answer": answer,
        "citations": citations,
        "retrieved_contexts": raw_snippets or [f"事实{i+1}: {f}" for i, f in enumerate(facts)],
        "facts": facts,
        "retrieval_mode": "agent",
    }


_EXECUTORS = {
    "dense": _execute_dense_query,
    "hybrid": _execute_hybrid_query,
    "agent": _execute_agent_query,
}


# ═══════════════════════════════════════════════════════════════════
# Answer generation (shared by hybrid & agent modes)
# ═══════════════════════════════════════════════════════════════════

async def _generate_answer(
    question: str, citations: list[dict], model_override: str | None = None,
) -> str:
    """LLM generates answer from citations with faithfulness constraints.
    
    Tries preferred model first, then walks the eval model fallback chain
    on quota exhaustion so answer generation never produces empty results.
    """
    from langchain_core.messages import SystemMessage, HumanMessage
    from app.core.config import settings

    if not citations:
        return "未找到相关参考信息。"

    context_block = "\n\n---\n\n".join(
        f"[来源：{c.get('source', '?')}] {c.get('title', '?')}\n{c.get('snippet', '')[:600]}"
        for c in citations[:8]
    )

    messages = [
        SystemMessage(content=(
            "你是研究助手。严格根据参考材料回答，不得推理或添加外部知识。"
            "每条信息标注来源。参考材料不足时说明哪些部分无法回答。"
        )),
        HumanMessage(content=f"问题：{question}\n\n参考材料：\n{context_block}"),
    ]

    # Try preferred model → fallback chain
    candidates = []
    preferred = model_override or settings.llm_model
    candidates.append(preferred)
    candidates.extend(m for m in _EVAL_MODEL_CHAIN if m != preferred)

    for model in candidates:
        try:
            llm = _chat_llm(model, temperature=0.3, max_tokens=1500, timeout=60, streaming=False)
            resp = await llm.ainvoke(messages)
            content = resp.content or ""
            if len(content) >= 20:
                return content
            logger.warning("Model %s produced short answer (%d chars), trying next", model, len(content))
        except Exception as e:
            err = str(e)
            if _detect_quota_error(err):
                logger.warning("Answer gen model %s quota exhausted, trying next", model)
                continue
            logger.error("Answer gen failed with %s: %s", model, err[:100])
            continue

    # All models failed — return raw citation snippets as fallback answer
    logger.error("All answer-gen models failed, using raw citations")
    return "\n".join(c.get("snippet", "")[:200] for c in citations[:5])


# ═══════════════════════════════════════════════════════════════════
# Fact-point parser
# ═══════════════════════════════════════════════════════════════════

def _parse_facts(text: str) -> list[str]:
    """Extract numbered fact points from agent output."""
    if not text or not text.strip():
        return []

    patterns = [
        # "事实1: xxx" / "事实1：xxx"
        (re.compile(r'事实\s*(\d+)\s*[:：]\s*(.+?)(?=事实\s*\d+\s*[:：]|$)', re.DOTALL), 1),
        # "1. xxx" / "1、xxx" / "1：xxx"
        (re.compile(r'(?:^|\n)\s*(\d+)\s*[.、：:]\s*(.+?)(?=(?:^|\n)\s*\d+\s*[.、：:]|$)', re.DOTALL), 1),
        # "Fact 1: xxx"
        (re.compile(r'Fact\s*(\d+)\s*[:：]\s*(.+?)(?=Fact\s*\d+\s*[:：]|$)', re.DOTALL | re.IGNORECASE), 1),
    ]

    for pattern, _ in patterns:
        matches = pattern.findall(text)
        if matches:
            return [text.strip() for _, text in sorted(matches, key=lambda x: int(x[0]))]

    return [text.strip()] if text.strip() else []


# ═══════════════════════════════════════════════════════════════════
# RAGAS metric construction
# ═══════════════════════════════════════════════════════════════════

def _build_ragas_components(eval_model: str | None = None):
    """Build RAGAS LLM wrapper + embeddings + metrics.

    Returns (wrapped_llm, embeddings, metrics, resolved_model).
    """
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    from langchain_openai import ChatOpenAI
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        ContextPrecision, ContextRecall, Faithfulness, FactualCorrectness,
    )
    from rag_eval.ollama_embeddings import OllamaEmbeddings
    from app.core.config import settings

    # Auto-select eval model
    model = _find_available_eval_model(eval_model)
    logger.info("Eval LLM: %s (requested: %s)", model, eval_model or "auto")

    # Build ChatOpenAI
    llm_kwargs: dict = dict(
        model=model,
        api_key=settings.eval_llm_api_key,
        base_url=settings.eval_llm_base_url,
        temperature=0.0,
        max_tokens=8192,
        timeout=300,
    )
    if model in _NO_THINK_MODELS:
        llm_kwargs["model_kwargs"] = {"enable_thinking": False}
    chat_llm = ChatOpenAI(**llm_kwargs)
    wrapped = LangchainLLMWrapper(chat_llm)

    # Build embeddings
    embeddings = OllamaEmbeddings(
        model=settings.embedding_model,
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
    )

    # Build metrics with Chinese optimization
    metrics = [
        ContextPrecision(),
        ContextRecall(),
        Faithfulness(),
        FactualCorrectness(mode="f1", atomicity="high", coverage="high", language="chinese"),
    ]

    for m in metrics:
        if hasattr(m, "llm"):
            m.llm = wrapped
        if hasattr(m, "embeddings"):
            m.embeddings = embeddings

    return wrapped, embeddings, metrics, model


# ═══════════════════════════════════════════════════════════════════
# NaN-safe aggregation
# ═══════════════════════════════════════════════════════════════════

def _safe_mean(vals: list) -> float | None:
    """Mean of numeric values, silently skipping NaN/None.  Returns None if empty."""
    clean = [float(v) for v in vals if v is not None and not math.isnan(float(v))]
    return round(sum(clean) / len(clean), 4) if clean else None


def _aggregate_by_group(
    records: list[dict], result_df, metric_cols: list[str],
) -> dict:
    """Per question_type + difficulty breakdown, NaN-safe."""
    type_idx: dict[str, list[int]] = defaultdict(list)
    diff_idx: dict[str, list[int]] = defaultdict(list)

    for i, rec in enumerate(records):
        qt = rec.get("question_type", rec.get("metadata", {}).get("query_type", "unknown"))
        diff = rec.get("difficulty", "unknown")
        type_idx[qt].append(i)
        diff_idx[diff].append(i)

    def _group_scores(indices: list[int]) -> dict:
        data = {}
        for col in metric_cols:
            vals = [result_df.iloc[i].get(col) for i in indices if i < len(result_df)]
            data[col] = _safe_mean(vals)
        data["count"] = len(indices)
        return data

    result = {}
    for qt, idx in type_idx.items():
        result[f"type:{qt}"] = _group_scores(idx)
    for diff, idx in diff_idx.items():
        result[f"difficulty:{diff}"] = _group_scores(idx)
    return result


# ═══════════════════════════════════════════════════════════════════
# Agent model fallback chain (for quota exhaustion)
# ═══════════════════════════════════════════════════════════════════

_AGENT_MODEL_CHAIN = [
    # Currently available (2026-05-11 17:35, comi confirmed)
    "qwen3.5-plus-2026-04-20",          # ✓ strong, tool-calling
    "qwen3-max-2025-09-23",            # ✓ strong, tool-calling
    "qwen3.6-plus-2026-04-02",          # ✓ strong, tool-calling
    "deepseek-v3.2",                    # ✓ strong, tool-calling
    "glm-5.1",                          # ✓ (Zhipu)
    "kimi-k2.6",                        # ✓ (Moonshot)
    "qwen3.6-27b",                      # ✓ medium, needs enable_thinking=false
    "qwen3-32b",                        # ? needs enable_thinking=false
    "qwen3-14b",                        # ? needs enable_thinking=false
    "qwen3-8b",                         # ? needs enable_thinking=false
    "qwen3-coder-flash",                # ?
]


def _detect_quota_error(err: str) -> bool:
    """Detect if error is API quota/rate-limit related."""
    return any(kw in err for kw in ("403", "429", "AllocationQuota", "rate_limit", "quota", "insufficient_quota"))


async def _run_agent_with_model_fallback(
    question: str, kb_id: int, top_k: int,
    preferred_model: str | None = None,
) -> dict:
    """Run agent query with automatic model switching on quota exhaustion.

    Strategy: try preferred model → walk fallback chain → each model retried once.
    Only fallback to dense if ALL models fail.
    Skips models known to be quota-exhausted (cached for 30 min).
    """
    now = time.time()
    # Prune expired entries from cache
    _DEAD_MODELS = {m: ts for m, ts in _run_agent_with_model_fallback._dead.items() if now - ts < 1800}
    _run_agent_with_model_fallback._dead = _DEAD_MODELS

    candidates = []
    if preferred_model and preferred_model not in _DEAD_MODELS:
        candidates.append(preferred_model)
    candidates.extend(m for m in _AGENT_MODEL_CHAIN if m != preferred_model and m not in _DEAD_MODELS)

    if not candidates:
        logger.warning("No available agent models, falling back to dense")
        r = await _execute_dense_query(question, kb_id=kb_id, top_k=top_k)
        r["retrieval_mode"] = "agent_fallback_dense"
        r["agent_model_used"] = "dense_fallback"
        return r

    for model in candidates:
        try:
            r = await _execute_agent_query(question, kb_id=kb_id, top_k=top_k, agent_model=model)
            # Check if agent actually produced useful output
            if r.get("retrieved_contexts") or len(r.get("answer", "")) >= 20:
                r["agent_model_used"] = model
                return r
            # Empty result from this model, try next
            logger.warning("Agent with %s produced empty result, trying next model", model)
        except Exception as e:
            err = str(e)
            if _detect_quota_error(err):
                # 403 = permanently exhausted → cache as dead
                # 429 = rate limit → just skip, don't cache (may recover)
                is_permanent = "403" in err or "AllocationQuota" in err or "insufficient_quota" in err
                if is_permanent:
                    _run_agent_with_model_fallback._dead[model] = time.time()
                    logger.warning("Agent model %s quota permanently exhausted, caching as dead", model)
                else:
                    logger.warning("Agent model %s rate-limited (429), skipping this round", model)
                continue
            logger.error("Agent with %s failed: %s", model, err[:100])
            continue

    # All agent models failed — last resort: dense (with clear marking)
    logger.warning("All agent models failed, falling back to dense as last resort")
    r = await _execute_dense_query(question, kb_id=kb_id, top_k=top_k)
    r["retrieval_mode"] = "agent_fallback_dense"
    r["agent_model_used"] = "dense_fallback"
    return r


# Module-level dead model cache for _run_agent_with_model_fallback
_run_agent_with_model_fallback._dead = {}  # type: ignore[attr-defined]


# ═══════════════════════════════════════════════════════════════════
# Main evaluation flow
# ═══════════════════════════════════════════════════════════════════

async def run_evaluation(
    dataset_path: str | Path,
    kb_id: int,
    top_k: int = DEFAULT_TOP_K,
    metrics: list[str] | None = None,
    retrieval_mode: str = "dense",
    eval_model: str | None = None,
    agent_model: str | None = None,
) -> Path:
    """Execute full RAG evaluation pipeline.

    For agent mode: automatic model switching on quota exhaustion +
    checkpoint/resume support. Only falls back to dense when ALL models fail.

    Returns the result directory path.
    """
    import asyncio
    from ragas import evaluate as ragas_evaluate
    from rag_eval.dataset import load_dataset

    if retrieval_mode not in _EXECUTORS:
        raise ValueError(f"retrieval_mode must be one of {list(_EXECUTORS)}, got {retrieval_mode}")

    # ── Phase 1: RAG retrieval + answer generation ──
    records = load_dataset(dataset_path)
    logger.info("Dataset: %d samples, mode=%s, top_k=%d", len(records), retrieval_mode, top_k)

    # Checkpoint support for agent mode (resume after interruption)
    ckpt_path = _RESULTS_DIR / "_rag_checkpoint.json"
    rag_results: list[dict] = []
    start_idx = 0

    if retrieval_mode == "agent" and ckpt_path.exists():
        try:
            with open(ckpt_path, encoding="utf-8") as f:
                ckpt = json.load(f)
            rag_results = ckpt.get("results", [])
            start_idx = ckpt.get("next_idx", 0)
            logger.info("Resuming agent RAG from checkpoint: %d done, starting at %d",
                        len(rag_results), start_idx)
        except Exception as e:
            logger.warning("Checkpoint load failed: %s", e)

    t0 = time.time()

    for i, rec in enumerate(records):
        if i < start_idx:
            continue

        if retrieval_mode == "agent":
            # Agent mode: model-fallback chain + automatic model switching
            r = await _run_agent_with_model_fallback(
                rec["user_input"], kb_id=kb_id, top_k=top_k, preferred_model=agent_model,
            )
            rag_results.append(r)
            mode_tag = r.get("retrieval_mode", "agent")
            model_tag = r.get("agent_model_used", "?")
            logger.info("[%d/%d] %s (model=%s) — ans=%d chars, ctx=%d",
                        i + 1, len(records), mode_tag, model_tag,
                        len(r.get("answer", "")), len(r.get("retrieved_contexts", [])))
        else:
            # Dense / Hybrid: straightforward
            try:
                executor = _EXECUTORS[retrieval_mode]
                r = await executor(rec["user_input"], kb_id=kb_id, top_k=top_k)
                rag_results.append(r)
                logger.info("[%d/%d] OK — ans=%d chars, ctx=%d",
                            i + 1, len(records), len(r.get("answer", "")),
                            len(r.get("retrieved_contexts", [])))
            except Exception as e:
                logger.error("[%d/%d] FAILED: %s", i + 1, len(records), e)
                rag_results.append({
                    "answer": "", "citations": [], "retrieved_contexts": [],
                    "retrieval_mode": retrieval_mode,
                })

        # Rate limiting for agent mode (avoid 429)
        if retrieval_mode == "agent":
            await asyncio.sleep(1)
            if (i + 1) % 5 == 0:
                await asyncio.sleep(3)

        # Save checkpoint every 10 samples (agent mode only)
        if retrieval_mode == "agent" and (i + 1) % 10 == 0:
            ckpt_data = {"results": rag_results, "next_idx": i + 1,
                         "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")}
            try:
                with open(ckpt_path, "w", encoding="utf-8") as f:
                    json.dump(ckpt_data, f, ensure_ascii=False, indent=1)
                logger.info("Checkpoint saved: %d/%d", len(rag_results), len(records))
            except Exception as e:
                logger.warning("Checkpoint save failed: %s", e)

    rag_elapsed = time.time() - t0
    logger.info("RAG phase done: %.1fs", rag_elapsed)

    # Clean up checkpoint
    if ckpt_path.exists():
        ckpt_path.unlink()
        logger.info("Checkpoint cleaned up")

    # ── Phase 2: RAGAS metric evaluation ──
    samples = []
    for rec, rag in zip(records, rag_results):
        samples.append(SingleTurnSample(
            user_input=rec["user_input"],
            response=rag.get("answer", ""),
            reference=rec.get("reference", ""),
            reference_contexts=rec.get("reference_contexts", []),
            retrieved_contexts=rag.get("retrieved_contexts", []),
        ))

    eval_dataset = EvaluationDataset(samples=samples)
    _, _, ragas_metrics, resolved_model = _build_ragas_components(eval_model)

    logger.info("RAGAS: %d samples × %d metrics, model=%s",
                len(samples), len(ragas_metrics), resolved_model)

    t1 = time.time()
    result = ragas_evaluate(
        dataset=eval_dataset,
        metrics=ragas_metrics,
        raise_exceptions=False,       # ← don't crash on single-sample failure
        show_progress=True,
    )
    eval_elapsed = time.time() - t1
    logger.info("RAGAS done: %.1fs", eval_elapsed)

    # ── Phase 3: Aggregate & save ──
    result_df = result.to_pandas()
    metric_cols = [c for c in result_df.columns
                   if c not in ("user_input", "reference", "response",
                                "reference_contexts", "retrieved_contexts")]

    # Overall scores (NaN-safe)
    ragas_scores = {}
    for col in metric_cols:
        ragas_scores[col] = _safe_mean(result_df[col].tolist())

    # Save
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    result_dir = _RESULTS_DIR / f"{timestamp}_{retrieval_mode}"
    result_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "timestamp": timestamp,
        "dataset_path": str(dataset_path),
        "kb_id": kb_id,
        "top_k": top_k,
        "retrieval_mode": retrieval_mode,
        "eval_model": resolved_model,
        "dataset_size": len(records),
        "rag_elapsed_seconds": round(rag_elapsed, 1),
        "eval_elapsed_seconds": round(eval_elapsed, 1),
        "ragas_scores": ragas_scores,
        "metrics_used": [type(m).__name__ for m in ragas_metrics],
    }

    # Type/difficulty breakdown (NaN-safe)
    summary["type_scores"] = _aggregate_by_group(records, result_df, metric_cols)

    # Write summary
    with open(result_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Write per-sample detail
    samples_detail = []
    for i, (rec, rag) in enumerate(zip(records, rag_results)):
        detail = {
            "id": rec.get("id", f"q_{i+1:03d}"),
            "user_input": rec["user_input"],
            "reference": rec.get("reference", ""),
            "reference_contexts": rec.get("reference_contexts", []),
            "response": rag.get("answer", ""),
            "retrieved_contexts": rag.get("retrieved_contexts", []),
            "citations": rag.get("citations", []),
            "question_type": rec.get("question_type"),
            "difficulty": rec.get("difficulty"),
            "retrieval_mode": rag.get("retrieval_mode", retrieval_mode),
            "ragas_scores": {},
        }
        if rag.get("facts"):
            detail["facts"] = rag["facts"]

        for col in metric_cols:
            val = result_df.iloc[i].get(col)
            detail["ragas_scores"][col] = (
                round(float(val), 4) if val is not None and not math.isnan(float(val)) else None
            )
        samples_detail.append(detail)

    with open(result_dir / "samples.json", "w", encoding="utf-8") as f:
        json.dump(samples_detail, f, ensure_ascii=False, indent=2)

    # Write config (reproducibility)
    config = {
        "dataset_path": str(dataset_path),
        "kb_id": kb_id, "top_k": top_k,
        "retrieval_mode": retrieval_mode,
        "eval_model": resolved_model,
        "metrics": [type(m).__name__ for m in ragas_metrics],
    }
    with open(result_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    logger.info("Results saved: %s", result_dir)
    return result_dir
