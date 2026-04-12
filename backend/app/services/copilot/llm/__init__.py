"""Copilot LLM 配置 — 模型选择与实例化。

解耦原则：LLM 配置独立于 agent 和 tool，方便切换模型。
"""

import logging

from langchain_openai import ChatOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


def get_chat_llm(
    prefer: str = "alibaba",
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> ChatOpenAI | None:
    """获取 ChatOpenAI 实例。

    Args:
        prefer: 优先选择 "alibaba"(默认,快) 或 "proxy"(慢,兼容性差)
        streaming: 是否流式（建议 True，代理API非流式返回空）
        temperature: 温度
        max_tokens: 最大token
        timeout: 超时秒数

    Returns:
        ChatOpenAI 实例或 None（无可用key时）
    """
    configs = []

    if prefer == "alibaba":
        # Alibaba qwen first (fast + reliable + supports tool calling)
        if settings.alibaba_api_key and settings.alibaba_base_url:
            configs.append({
                "model": settings.alibaba_model_name or "qwen3.5-flash",
                "api_key": settings.alibaba_api_key,
                "base_url": settings.alibaba_base_url,
                "label": "alibaba-flash",
            })
            # Fallback model (e.g. qwen3.5-plus when flash quota exhausted)
            fallback_model = getattr(settings, "alibaba_model_name_fallback", None)
            if fallback_model:
                configs.append({
                    "model": fallback_model,
                    "api_key": settings.alibaba_api_key,
                    "base_url": settings.alibaba_base_url,
                    "label": "alibaba-plus",
                })
        # Proxy as last fallback
        if settings.openai_api_key:
            configs.append({
                "model": settings.llm_model,
                "api_key": settings.openai_api_key,
                "base_url": settings.openai_base_url or None,
                "label": "proxy",
            })
    else:
        # Proxy first
        if settings.openai_api_key:
            configs.append({
                "model": settings.llm_model,
                "api_key": settings.openai_api_key,
                "base_url": settings.openai_base_url or None,
                "label": "proxy",
            })
        if settings.alibaba_api_key and settings.alibaba_base_url:
            configs.append({
                "model": settings.alibaba_model_name or "qwen3.5-flash",
                "api_key": settings.alibaba_api_key,
                "base_url": settings.alibaba_base_url,
                "label": "alibaba-flash",
            })
            fallback_model = getattr(settings, "alibaba_model_name_fallback", None)
            if fallback_model:
                configs.append({
                    "model": fallback_model,
                    "api_key": settings.alibaba_api_key,
                    "base_url": settings.alibaba_base_url,
                    "label": "alibaba-plus",
                })

    for cfg in configs:
        try:
            llm = ChatOpenAI(
                model=cfg["model"],
                api_key=cfg["api_key"],
                base_url=cfg["base_url"],
                streaming=streaming,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            logger.info("Copilot LLM: %s (model=%s)", cfg["label"], cfg["model"])
            return llm
        except Exception as e:
            logger.warning("Failed to init LLM %s: %s", cfg["label"], e)
            continue

    logger.error("No LLM available for copilot")
    return None


def get_all_chat_llms(
    prefer: str = "alibaba",
    streaming: bool = True,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 60,
) -> list[ChatOpenAI]:
    """获取所有可用 LLM 实例列表，用于请求级 fallback。

    当首选 LLM 请求失败（如额度耗尽）时，Agent 可依次尝试下一个。
    """
    llms = []
    
    providers = []
    if prefer == "alibaba":
        if settings.alibaba_api_key and settings.alibaba_base_url:
            providers.append(("alibaba-flash", settings.alibaba_model_name or "qwen3.5-flash",
                            settings.alibaba_api_key, settings.alibaba_base_url))
            fallback_model = getattr(settings, "alibaba_model_name_fallback", None)
            if fallback_model:
                providers.append(("alibaba-plus", fallback_model,
                                settings.alibaba_api_key, settings.alibaba_base_url))
        if settings.openai_api_key:
            providers.append(("proxy", settings.llm_model,
                            settings.openai_api_key, settings.openai_base_url))
    else:
        if settings.openai_api_key:
            providers.append(("proxy", settings.llm_model,
                            settings.openai_api_key, settings.openai_base_url))
        if settings.alibaba_api_key and settings.alibaba_base_url:
            providers.append(("alibaba-flash", settings.alibaba_model_name or "qwen3.5-flash",
                            settings.alibaba_api_key, settings.alibaba_base_url))
            fallback_model = getattr(settings, "alibaba_model_name_fallback", None)
            if fallback_model:
                providers.append(("alibaba-plus", fallback_model,
                                settings.alibaba_api_key, settings.alibaba_base_url))

    for label, model, api_key, base_url in providers:
        try:
            llm = ChatOpenAI(
                model=model,
                api_key=api_key,
                base_url=base_url,
                streaming=streaming,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            llms.append(llm)
            logger.info("Fallback LLM ready: %s (model=%s)", label, model)
        except Exception as e:
            logger.warning("Skip fallback LLM %s: %s", label, e)

    return llms


def get_rag_llm(
    streaming: bool = True,
    temperature: float = 0.3,
    max_tokens: int = 1500,
    timeout: int = 60,
) -> ChatOpenAI | None:
    """获取用于 RAG answer 生成的 LLM。

    RAG 场景优先用 Alibaba（稳定、非流式也正常）。
    """
    return get_chat_llm(
        prefer="alibaba",
        streaming=streaming,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
