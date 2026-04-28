"""Copilot LLM 配置 — 模型选择与实例化。

设计：
1. 所有 Chat LLM 统一使用 DashScope (DASHSCOPE_API_KEY + DASHSCOPE_BASE_URL)
2. 默认 streaming=True（原生流式输出）
3. with_fallbacks() 自动处理模型调用失败，支持多层 fallback
4. Fallback 链: primary → fallback_1 → fallback_2 → fallback_3 → fallback_4
5. 解耦原则：LLM 配置独立于 agent 和 tool
"""

import logging

from langchain_openai import ChatOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _build_llm_for_model(
    model_name: str,
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> ChatOpenAI | None:
    """为指定模型名构建 ChatOpenAI 实例。"""
    if not model_name:
        return None
    try:
        return ChatOpenAI(
            model=model_name,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            streaming=streaming,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("Failed to init LLM (%s): %s", model_name, e)
        return None


# ─── Internal ────────────────────────────────────────────────────────────────

def _build_llm_chain(
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> ChatOpenAI:
    """构建带多层 fallback 的 LLM 链。

    Fallback 链: primary → fallback_1 → fallback_2 → fallback_3 → fallback_4
    """
    if not settings.llm_configured:
        raise RuntimeError("No LLM available — check DASHSCOPE_API_KEY in .env")

    # 按优先级收集模型名
    model_names = [
        ("primary", settings.llm_model),
        ("fallback_1", settings.dashscope_model_name_fallback),
        ("fallback_2", settings.dashscope_model_name_fallback_2),
        ("fallback_3", settings.dashscope_model_name_fallback_3),
        ("fallback_4", settings.dashscope_model_name_fallback_4),
    ]

    # 构建实例
    llms: list[tuple[str, ChatOpenAI]] = []
    for label, model_name in model_names:
        llm = _build_llm_for_model(model_name, streaming, temperature, max_tokens, timeout)
        if llm:
            llms.append((label, llm))

    if not llms:
        raise RuntimeError("No LLM available — check DASHSCOPE_MODEL_NAME in .env")

    head_label, head_llm = llms[0]
    fallbacks = [llm for _, llm in llms[1:]]

    if fallbacks:
        chain = head_llm.with_fallbacks(fallbacks)
        logger.info(
            "LLM chain: %s(%s) → %s (with_fallbacks)",
            head_label, settings.llm_model,
            " → ".join(f"{label}({model_names[i+1][1]})" for i, (label, _) in enumerate(llms[1:])),
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
    """获取带多层 fallback 的 ChatOpenAI 实例（默认 streaming=True）。"""
    return _build_llm_chain(streaming, temperature, max_tokens, timeout)


def get_all_chat_llms(
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> list[ChatOpenAI]:
    """获取所有 LLM 实例列表。"""
    llms = []
    for model_name in [
        settings.llm_model,
        settings.dashscope_model_name_fallback,
        settings.dashscope_model_name_fallback_2,
        settings.dashscope_model_name_fallback_3,
        settings.dashscope_model_name_fallback_4,
    ]:
        llm = _build_llm_for_model(model_name, streaming, temperature, max_tokens, timeout)
        if llm:
            llms.append(llm)
    return llms


def get_rag_llm(
    streaming: bool = True,
    temperature: float = 0.3,
    max_tokens: int = 1500,
    timeout: int = 60,
) -> ChatOpenAI:
    """获取用于 RAG answer 生成的 LLM。"""
    return get_chat_llm(streaming=streaming, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
