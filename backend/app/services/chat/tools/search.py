"""Agentic Search 工具：search_web + fetch_page + get_search_content + SearchPoolManager。

核心设计（与 RecallPool 同理）：
- Agent 自主驱动反思检索循环
- search_web: 搜索结果存 SearchPool（轻量，可直接返回摘要），按 URL 去重
- fetch_page: 抓取内容存 SearchPool（不进 messages），返回轻量确认
- get_search_content: Agent 按需获取指定页面完整内容
- SearchPool: 按 URL 去重，会话级临时状态
"""

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger(__name__)

# ─── SearchPool 存储 ───

SEARCH_POOL_DIR = None  # 延迟初始化


def _get_search_pool_dir() -> Path:
    """获取 SearchPool 临时文件目录"""
    global SEARCH_POOL_DIR
    if SEARCH_POOL_DIR is None:
        SEARCH_POOL_DIR = Path(settings.storage_base_dir) / "search_pool"
        SEARCH_POOL_DIR.mkdir(parents=True, exist_ok=True)
    return SEARCH_POOL_DIR


# ─── 数据结构 ───

@dataclass
class SearchedPage:
    """搜索池中的页面"""
    url: str              # URL（去重键）
    title: str            # 页面标题
    source: str           # 来源域名
    snippet: str          # 搜索结果 snippet
    content: str | None   # 抓取的完整内容（未抓取时为 None）
    relevance_score: float  # 相关度（搜索结果无精确分数，默认 0）
    round: int            # 第几轮获得


# ─── SearchPoolManager ───

class SearchPoolManager:
    """搜索池管理器：临时文件存储，按 URL 去重

    生命周期：
    - 每次新用户消息到达时，API 层调用 reset_for_new_turn() 清空旧 pool
    - 一轮对话中（同一次 Agent 调用），pool 在多轮搜索间累积去重
    - 超过 POOL_MAX_AGE_HOURS 的临时文件由 cleanup_stale_files() 自动清理
    """

    # 临时文件最大保留时间（小时）
    POOL_MAX_AGE_HOURS = 24

    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self._pool_path = _get_search_pool_dir() / f"{thread_id}.json"
        self._pages: dict[str, SearchedPage] | None = None

    def _load(self) -> dict[str, SearchedPage]:
        """从临时文件加载"""
        if self._pages is not None:
            return self._pages

        if self._pool_path.exists():
            try:
                with open(self._pool_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._pages = {
                    url: SearchedPage(**pd) for url, pd in data.items()
                }
            except Exception as e:
                logger.warning("SearchPool load failed: %s", e)
                self._pages = {}
        else:
            self._pages = {}
        return self._pages

    def _save(self):
        """保存到临时文件"""
        if self._pages is None:
            return
        try:
            data = {url: asdict(pd) for url, pd in self._pages.items()}
            with open(self._pool_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("SearchPool save failed: %s", e)

    def add_search_results(self, results: list[dict], current_round: int = 1) -> int:
        """追加搜索结果，按 URL 去重。返回新增条数。"""
        pool = self._load()
        added = 0
        for r in results:
            url = r.get("url", "")
            if not url:
                continue
            if url in pool:
                continue  # URL 去重：已存在则跳过
            pool[url] = SearchedPage(
                url=url,
                title=r.get("title", ""),
                source=r.get("source", ""),
                snippet=r.get("snippet", ""),
                content=None,  # 搜索结果只有 snippet，无完整内容
                relevance_score=0,
                round=current_round,
            )
            added += 1
        self._save()
        return added

    def update_page_content(self, url: str, title: str, source: str, content: str) -> bool:
        """更新指定页面的抓取内容。如果 URL 不在池中，自动创建。"""
        pool = self._load()
        if url in pool:
            pool[url].content = content
            pool[url].title = title or pool[url].title
            pool[url].source = source or pool[url].source
        else:
            pool[url] = SearchedPage(
                url=url,
                title=title,
                source=source,
                snippet="",
                content=content,
                relevance_score=0,
                round=0,
            )
        self._save()
        return True

    def get_pages(self, urls: list[str]) -> list[SearchedPage]:
        """获取指定页面"""
        pool = self._load()
        return [pool[u] for u in urls if u in pool]

    def get_all_urls(self) -> list[str]:
        """获取所有 URL"""
        return list(self._load().keys())

    def get_fetched_urls(self) -> list[str]:
        """获取已抓取内容的 URL"""
        pool = self._load()
        return [url for url, pg in pool.items() if pg.content is not None]

    def total_count(self) -> int:
        """获取总条目数"""
        return len(self._load())

    def clear(self):
        """清空搜索池并删除临时文件"""
        self._pages = {}
        try:
            if self._pool_path.exists():
                self._pool_path.unlink()
        except Exception:
            pass

    def reset_for_new_turn(self):
        """新对话轮次开始时重置搜索池。

        与 clear() 相同——清空旧数据，确保不同轮次的搜索结果不会混入。
        由 API 层在每次新用户消息到达时调用。
        """
        self.clear()
        logger.debug("SearchPool reset for new turn: %s", self.thread_id)

    @staticmethod
    def cleanup_stale_files(max_age_hours: int = 24):
        """清理超过 max_age_hours 的临时文件。

        可由定时任务或心跳调用，防止磁盘文件无限增长。
        """
        pool_dir = _get_search_pool_dir()
        if not pool_dir.exists():
            return 0

        import time
        now = time.time()
        max_age_seconds = max_age_hours * 3600
        cleaned = 0

        for f in pool_dir.glob("*.json"):
            try:
                if now - f.stat().st_mtime > max_age_seconds:
                    f.unlink()
                    cleaned += 1
            except Exception:
                pass

        if cleaned > 0:
            logger.info("SearchPool cleanup: removed %d stale files (> %dh)", cleaned, max_age_hours)
        return cleaned


# ─── thread_id 注入（统一上下文） ───

from app.services.chat.tools.thread_context import (
    get_current_thread_id,
    set_current_thread_id,
    clear_current_thread_id,
)


# ─── 工具实现 ───

async def _search_web_impl(query: str, max_results: int = 5, thread_id: str = "") -> str:
    """搜索互联网，结果存 SearchPool，返回轻量摘要"""
    from app.services.consultation.ingestion import search_web as _sw

    results = await _sw(query, max_results=max_results)

    if not results:
        return "未找到搜索结果。"

    # 存入 SearchPool
    pool = SearchPoolManager(thread_id) if thread_id else None
    current_round = 1
    if pool:
        existing_count = pool.total_count()
        current_round = (existing_count // max(max_results, 1)) + 1
        added = pool.add_search_results(results, current_round=current_round)
        total = pool.total_count()
    else:
        added = len(results)
        total = len(results)

    # 构建轻量摘要返回（搜索结果本身轻量，可直接返回给 Agent）
    lines = [f"搜索到 {len(results)} 条结果，新增 {added} 条（去重后），搜索池累计 {total} 条。"]
    for i, r in enumerate(results[:max_results], 1):
        url = r.get("url", "")
        lines.append(
            f"  [{i}] {r.get('title', '无标题')} | 来源: {r.get('source', '?')}\n"
            f"      {r.get('snippet', '')[:150]}\n"
            f"      链接: {url}"
        )

    if pool and pool.get_fetched_urls():
        lines.append(f"已抓取页面: {', '.join(pool.get_fetched_urls()[:5])}")

    return "\n".join(lines)


async def _fetch_page_impl(url: str, thread_id: str = "") -> str:
    """抓取网页内容，存 SearchPool（不进 messages），返回轻量确认"""
    from app.services.consultation.ingestion import fetch_page, extract_article

    html = await fetch_page(url)
    if not html:
        return f"无法访问页面: {url}"

    data = extract_article(html, url)
    if not data:
        return f"无法提取页面内容: {url}"

    title = data.get("title", "")
    source = data.get("source", "")
    content = data.get("content", "")

    # 存入 SearchPool
    if thread_id:
        pool = SearchPoolManager(thread_id)
        pool.update_page_content(url, title, source, content)

    # 返回轻量确认（不返回完整内容）
    char_count = len(content)
    # 提取前 200 字作为核心发现预览
    preview = content[:200].replace("\n", " ").strip()

    lines = [
        f"已抓取页面: \"{title}\" | 来源: {source} | 约 {char_count} 字",
        f"核心发现: {preview}...",
        f"链接: {url}",
        "",
        "调用 get_search_content 可阅读完整内容。",
    ]
    return "\n".join(lines)


async def _get_search_content_impl(urls: str, thread_id: str = "") -> str:
    """获取搜索池中指定页面的完整内容"""
    if not thread_id:
        return "⚠️ 无法访问搜索池：缺少会话ID"

    pool = SearchPoolManager(thread_id)
    url_list = [u.strip() for u in urls.split(",") if u.strip()]
    pages = pool.get_pages(url_list)

    if not pages:
        return "搜索池中未找到指定页面。"

    lines = []
    for pg in pages:
        if pg.content is None:
            lines.append(
                f"[{pg.url}] {pg.title} | 来源: {pg.source}\n"
                f"（此页面尚未抓取，请先调用 fetch_page 抓取）\n"
                f"搜索摘要: {pg.snippet[:200]}\n"
            )
        else:
            lines.append(
                f"[{pg.url}] {pg.title} | 来源: {pg.source}\n"
                f"---\n{pg.content}\n"
            )

    return "\n".join(lines)


# ─── LangChain 工具 ───

@tool
async def search_web(query: str, max_results: int = 5) -> str:
    """搜索互联网，获取与查询相关的网页列表。

    搜索结果自动追加到搜索池（SearchPool），按URL去重。
    返回搜索结果摘要（标题、来源、链接、snippet），结果本身轻量。

    Args:
        query: 搜索关键词（可直接使用改写后的关键词，无需额外改写工具）
        max_results: 最大搜索结果数（3-10）
    """
    return await _search_web_impl(query, max_results, get_current_thread_id())


@tool
async def fetch_page(url: str) -> str:
    """抓取指定URL的网页内容。

    抓取结果存入搜索池（SearchPool），不返回完整内容。
    只返回轻量确认（标题、来源、字数摘要）。
    Agent 可调用 get_search_content 阅读完整内容。

    Args:
        url: 要抓取的网页URL
    """
    return await _fetch_page_impl(url, get_current_thread_id())


@tool
async def get_search_content(urls: str) -> str:
    """获取搜索池中指定页面的完整内容。

    Agent 选择性阅读感兴趣的页面，用于判断信息是否充分。
    可一次获取多个页面，用逗号分隔URL。

    Args:
        urls: 页面URL列表，逗号分隔
    """
    return await _get_search_content_impl(urls, get_current_thread_id())
