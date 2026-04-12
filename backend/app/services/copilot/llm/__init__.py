"""Copilot LLM 配置 — 模型选择与实例化。

核心设计：
1. with_fallbacks() 自动处理模型调用失败，无需手写 try/except
2. 请求级 fallback：主模型失败 → 自动切换后备模型
3. 解耦原则：LLM 配置独立于 agent 和 tool
"""

import logging

from langchain_openai import ChatOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _build_alibaba_llm(
    model: str,
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> ChatOpenAI | None:
    """构建阿里云 DashScope LLM 实例。"""
    if not (settings.alibaba_api_key and settings.alibaba_base_url and model):
        return None
    try:
        return ChatOpenAI(
            model=model,
            api_key=settings.alibaba_api_key,
            base_url=settings.alibaba_base_url,
            streaming=streaming,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("Failed to init Alibaba LLM (%s): %s", model, e)
        return None


def _build_proxy_llm(
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> ChatOpenAI | None:
    """构建代理 LLM 实例（OpenAI 兼容 API）。"""
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
        logger.warning("Failed to init Proxy LLM (%s): %s", settings.llm_model, e)
        return None


def _build_llm_chain(
    prefer: str = "alibaba",
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> ChatOpenAI:
    """构建带 fallback 的 LLM 链。

    使用 with_fallbacks() 自动处理模型调用失败：
    - 主模型失败（额度耗尽、超时、API 错误等）→ 自动切换后备模型
    - 支持流式 + 非流式
    - 无需手写 try/except 或 reset_agent

    链顺序（prefer=alibaba）:
    1. alibaba_model_name (e.g. qwen3.5-35b-a3b)
    2. alibaba_model_name_fallback (e.g. qwen3.5-122b-a10b)
    3. proxy (e.g. gpt-5.4)

    链顺序（prefer=proxy）: 反转
    """
    # Collect LLMs in priority order
    llms = []

    if prefer == "alibaba":
        # Primary Alibaba model
        primary = _build_alibaba_llm(
            settings.alibaba_model_name, streaming, temperature, max_tokens, timeout
        )
        if primary:
            llms.append(("alibaba-primary", primary))
        # Alibaba fallback model
        fallback_model = settings.alibaba_model_name_fallback
        if fallback_model:
            fb = _build_alibaba_llm(fallback_model, streaming, temperature, max_tokens, timeout)
            if fb:
                llms.append(("alibaba-fallback", fb))
        # Proxy as last resort
        proxy = _build_proxy_llm(streaming, temperature, max_tokens, timeout)
        if proxy:
            llms.append(("proxy", proxy))
    else:
        # Proxy first
        proxy = _build_proxy_llm(streaming, temperature, max_tokens, timeout)
        if proxy:
            llms.append(("proxy", proxy))
        # Alibaba as fallback
        primary = _build_alibaba_llm(
            settings.alibaba_model_name, streaming, temperature, max_tokens, timeout
        )
        if primary:
            llms.append(("alibaba-primary", primary))
        fallback_model = settings.alibaba_model_name_fallback
        if fallback_model:
            fb = _build_alibaba_llm(fallback_model, streaming, temperature, max_tokens, timeout)
            if fb:
                llms.append(("alibaba-fallback", fb))

    if not llms:
        raise RuntimeError("No LLM available — check ALIBABA_API_KEY or OPENAI_API_KEY in .env")

    # Build with_fallbacks chain
    head_label, head_llm = llms[0]
    fallbacks = [llm for _, llm in llms[1:]]

    if fallbacks:
        chain = head_llm.with_fallbacks(fallbacks)
        logger.info(
            "LLM chain: %s → %s (with_fallbacks)",
            head_label,
            " → ".join(label for label, _ in llms[1:]),
        )
    else:
        chain = head_llm
        logger.info("LLM chain: %s (no fallbacks)", head_label)

    return chain


# ─── Public API ────────────────────────────────────────────────────────────

def get_chat_llm(
    prefer: str = "alibaba",
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> ChatOpenAI:
    """获取带 fallback 的 ChatOpenAI 实例。

    Args:
        prefer: 优先选择 "alibaba"(默认,快) 或 "proxy"(慢,兼容性差)
        streaming: 是否流式（建议 True，代理API非流式返回空）
        temperature: 温度
        max_tokens: 最大token
        timeout: 超时秒数

    Returns:
        带 with_fallbacks 的 ChatOpenAI 实例。
        主模型失败时自动切换后备，调用方无需处理 fallback。
    """
    return _build_llm_chain(prefer, streaming, temperature, max_tokens, timeout)


def get_all_chat_llms(
    prefer: str = "alibaba",
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> list[ChatOpenAI]:
    """获取所有 LLM 实例列表（用于 create_react_agent 预测试）。

    返回顺序与 fallback 链一致，供 agent 初始化时选择可用模型。
    """
    llms = []

    if prefer == "alibaba":
        primary = _build_alibaba_llm(
            settings.alibaba_model_name, streaming, temperature, max_tokens, timeout
        )
        if primary:
            llms.append(primary)
        fallback_model = settings.alibaba_model_name_fallback
        if fallback_model:
            fb = _build_alibaba_llm(fallback_model, streaming, temperature, max_tokens, timeout)
            if fb:
                llms.append(fb)
        proxy = _build_proxy_llm(streaming, temperature, max_tokens, timeout)
        if proxy:
            llms.append(proxy)
    else:
        proxy = _build_proxy_llm(streaming, temperature, max_tokens, timeout)
        if proxy:
            llms.append(proxy)
        primary = _build_alibaba_llm(
            settings.alibaba_model_name, streaming, temperature, max_tokens, timeout
        )
        if primary:
            llms.append(primary)
        fallback_model = settings.alibaba_model_name_fallback
        if fallback_model:
            fb = _build_alibaba_llm(fallback_model, streaming, temperature, max_tokens, timeout)
            if fb:
                llms.append(fb)

    return llms


def get_rag_llm(
    streaming: bool = True,
    temperature: float = 0.3,
    max_tokens: int = 1500,
    timeout: int = 60,
) -> ChatOpenAI:
    """获取用于 RAG answer 生成的 LLM（带 fallback）。"""
    return get_chat_llm(
        prefer="alibaba",
        streaming=streaming,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
