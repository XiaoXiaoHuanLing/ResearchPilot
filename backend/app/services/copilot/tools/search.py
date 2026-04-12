"""搜索与采集工具 — search_web, ingest_url, collect_topic"""
import json
import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


# ─── search_web ──────────────────────────────────────────────────────────

async def _search_web_impl(query: str, max_results: int = 5) -> str:
    """Implementation: search the web."""
    from app.services.ingestion import search_web as _sw
    results = await _sw(query, max_results=max_results)
    if not results:
        return "未找到搜索结果。"
    lines = []
    for i, r in enumerate(results[:5], 1):
        lines.append(f"{i}. [{r.get('source','?')}] {r.get('title','无标题')}")
        lines.append(f"   {r.get('snippet','')[:150]}")
        lines.append(f"   链接: {r.get('url','')}")
    return "\n".join(lines)


@tool
async def search_web(query: str, max_results: int = 5) -> str:
    """搜索互联网，获取与查询相关的网页列表。

    适用于：用户想了解某个话题的最新信息、查找特定主题的资讯。
    返回搜索结果摘要（标题、来源、链接、片段）。
    """
    return await _search_web_impl(query, max_results)


# ─── ingest_url ──────────────────────────────────────────────────────────

async def _ingest_url_impl(url: str, topic_name: str = "对话采集", auto_bookmark: bool = False) -> str:
    """Implementation: ingest a URL."""
    from app.services.ingestion import ingest_url as _iu
    try:
        article = await _iu(url, topic_name=topic_name, auto_bookmark=auto_bookmark)
        if article:
            bm = "（已收藏）" if article.bookmarked else ""
            return f"✅ 已入库: [{article.id}] {article.title} — {article.source} {bm}"
        return "❌ 抓取失败，无法提取正文内容。"
    except Exception as e:
        return f"❌ 入库失败: {e}"


@tool
async def ingest_url(url: str, topic_name: str = "对话采集", auto_bookmark: bool = False) -> str:
    """抓取一个 URL 的完整内容并入库。

    步骤：搜索 → 获取全文 → 提取正文 → 生成摘要 → 入库。
    适用于：用户发现一篇感兴趣的资讯，想保存到知识库。
    """
    return await _ingest_url_impl(url, topic_name, auto_bookmark)


# ─── collect_topic ──────────────────────────────────────────────────────

async def _collect_topic_impl(topic_id: int) -> str:
    """Implementation: collect articles for a topic."""
    from app.services.ingestion import run_topic_collection
    from app.db.session import SessionLocal
    from app.db.models import TopicModel

    with SessionLocal() as db:
        topic = db.query(TopicModel).filter(TopicModel.id == topic_id).first()
        if not topic:
            return f"❌ 未找到专题 ID={topic_id}"
        name = topic.name
        kw_csv = topic.keywords
        keywords = [k.strip() for k in kw_csv.split(",") if k.strip()]

    try:
        count = await run_topic_collection(topic_id, name, keywords)
        return f"✅ 专题「{name}」采集完成，新增 {count} 篇资讯。"
    except Exception as e:
        return f"❌ 采集失败: {e}"


@tool
async def collect_topic(topic_id: int) -> str:
    """为指定专题执行一次完整的资讯采集。

    流程：读取专题关键词 → 搜索互联网 → 抓取入库。
    适用于：用户想手动触发某个专题的采集。
    """
    return await _collect_topic_impl(topic_id)


# ─── Exports ────────────────────────────────────────────────────────────

TOOLS = [search_web, ingest_url, collect_topic]

# Map tool name → actual callable (bypasses @tool async func=None issue)
FUNC_MAP = {
    "search_web": _search_web_impl,
    "ingest_url": _ingest_url_impl,
    "collect_topic": _collect_topic_impl,
}
