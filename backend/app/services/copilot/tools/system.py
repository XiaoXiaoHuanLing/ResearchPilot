"""系统工具 — get_system_status, get_scheduler_jobs"""
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _get_system_status_impl() -> str:
    from app.core.config import settings

    parts = []

    if settings.openai_api_key:
        parts.append(f"🟢 LLM: {settings.llm_model}")
    else:
        parts.append("🔴 LLM: 未配置")

    if settings.tavily_api_key or settings.serper_api_key:
        providers = []
        if settings.tavily_api_key:
            providers.append("Tavily")
        if settings.serper_api_key:
            providers.append("Serper")
        parts.append(f"🟢 搜索: {', '.join(providers)}")
    else:
        parts.append("🔴 搜索: 未配置")

    if settings.embedding_api_key:
        parts.append(f"🟢 Embedding: {settings.embedding_model}")
    else:
        parts.append("🔴 Embedding: 未配置")

    return "📊 系统状态\n" + "\n".join(f"  {p}" for p in parts)


@tool
def get_system_status() -> str:
    """查看系统状态：LLM、搜索引擎、RAG 是否配置就绪。"""
    return _get_system_status_impl()


def _get_scheduler_jobs_impl() -> str:
    try:
        from app.services.scheduler import get_scheduled_jobs
        jobs = get_scheduled_jobs()
    except Exception as e:
        return f"⚠️ 调度器查询失败：{e}"

    if not jobs:
        return "暂无定时采集任务。"

    lines = []
    for j in jobs:
        lines.append(f"  ⏰ {j.get('id', '?')} → 下次: {j.get('next_run', '?')} | {j.get('trigger', '?')}")

    return f"📋 共 {len(jobs)} 个定时任务：\n" + "\n".join(lines)


@tool
def get_scheduler_jobs() -> str:
    """查看定时采集任务列表。"""
    return _get_scheduler_jobs_impl()


TOOLS = [get_system_status, get_scheduler_jobs]

FUNC_MAP = {
    "get_system_status": _get_system_status_impl,
    "get_scheduler_jobs": _get_scheduler_jobs_impl,
}
