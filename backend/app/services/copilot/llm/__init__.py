"""Copilot LLM 配置 — 模型选择与实例化。

设计：
1. 所有 Chat LLM 统一使用 OPENAI_BASE_URL + OPENAI_API_KEY + OPENAI_MODEL_NAME
2. 默认 streaming=True（原生流式输出）
3. with_fallbacks() 自动处理模型调用失败
4. 支持可选 fallback 模型（OPENAI_MODEL_NAME_FALLBACK）
5. 解耦原则：LLM 配置独立于 agent 和 tool
"""

import logging

from langchain_openai import ChatOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _build_primary_llm(
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> ChatOpenAI | None:
    """构建主 Chat LLM 实例。"""
    if not settings.openai_api_key:
        return None
    try:
        return ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            streaming=streaming,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("Failed to init primary LLM (%s): %s", settings.llm_model, e)
        return None


def _build_fallback_llm(
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> ChatOpenAI | None:
    """构建 fallback Chat LLM 实例（同一 API，不同模型名）。"""
    fallback_model = settings.openai_model_name_fallback
    if not fallback_model:
        return None
    try:
        return ChatOpenAI(
            model=fallback_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
            streaming=streaming,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("Failed to init fallback LLM (%s): %s", fallback_model, e)
        return None


# ─── Internal ────────────────────────────────────────────────────────────────

def _build_llm_chain(
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> ChatOpenAI:
    """构建带可选 fallback 的 LLM 链。"""
    llms = []

    primary = _build_primary_llm(streaming, temperature, max_tokens, timeout)
    if primary:
        llms.append(("primary", primary))

    fallback = _build_fallback_llm(streaming, temperature, max_tokens, timeout)
    if fallback:
        llms.append(("fallback", fallback))

    if not llms:
        raise RuntimeError("No LLM available — check OPENAI_API_KEY / OPENAI_BASE_URL in .env")

    head_label, head_llm = llms[0]
    fallbacks = [llm for _, llm in llms[1:]]

    if fallbacks:
        chain = head_llm.with_fallbacks(fallbacks)
        logger.info(
            "LLM chain: %s(%s) → %s (with_fallbacks)",
            head_label, settings.llm_model,
            " → ".join(label for label, _ in llms[1:]),
        )
    else:
        chain = head_llm
        logger.info("LLM chain: %s(%s) (no fallbacks)", head_label, settings.llm_model)

    return chain


# ─── Public API ────────────────────────────────────────────────────────────

def get_chat_llm(
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> ChatOpenAI:
    """获取带 fallback 的 ChatOpenAI 实例（默认 streaming=True）。"""
    return _build_llm_chain(streaming, temperature, max_tokens, timeout)


def get_all_chat_llms(
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> list[ChatOpenAI]:
    """获取所有 LLM 实例列表。"""
    llms = []
    primary = _build_primary_llm(streaming, temperature, max_tokens, timeout)
    if primary:
        llms.append(primary)
    fallback = _build_fallback_llm(streaming, temperature, max_tokens, timeout)
    if fallback:
        llms.append(fallback)
    return llms


def get_rag_llm(
    streaming: bool = True,
    temperature: float = 0.3,
    max_tokens: int = 1500,
    timeout: int = 60,
) -> ChatOpenAI:
    """获取用于 RAG answer 生成的 LLM。"""
    return get_chat_llm(streaming=streaming, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
