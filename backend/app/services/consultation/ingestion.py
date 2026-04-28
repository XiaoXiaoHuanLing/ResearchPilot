"""Source ingestion pipeline: search, fetch, extract, and store articles.

Pipeline:
1. Search: Use Tavily API (or Serper) for web search with keywords
2. Fetch: HTTP GET with polite delays and User-Agent
3. Extract: readability-lxml for main content, BS4 for metadata
4. Normalize: clean text, standardize date, generate summary via LLM
5. Store: save to DB as ArticleModel, auto-index into RAG if bookmarked
"""

import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from readability import Document

from app.core.config import settings
from app.db.models import ArticleModel
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# Polite fetching config
FETCH_TIMEOUT = 30.0
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Get or create the shared httpx AsyncClient (sync factory, async usage)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
    return _client


# --- Web Search ---

async def search_web_tavily(query: str, max_results: int = 10) -> list[dict]:
    """Search the web using Tavily API.

    Returns list of {title, url, snippet}.
    """
    if not settings.tavily_api_key:
        logger.warning("Tavily API key not configured, skipping web search")
        return []

    try:
        client = _get_client()
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.tavily_api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",  # "advanced" causes 400 errors with CJK queries
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("results", []):
            results.append({
                "title": item.get("title", "无标题"),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
                "source": urlparse(item.get("url", "")).netloc or "未知来源",
            })
        return results

    except Exception as e:
        logger.error("Tavily search failed: %s", e)
        return []


async def search_web_serper(query: str, max_results: int = 10) -> list[dict]:
    """Search the web using Serper API.

    Returns list of {title, url, snippet}.
    """
    if not settings.serper_api_key:
        logger.warning("Serper API key not configured, skipping web search")
        return []

    try:
        client = _get_client()
        resp = await client.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": settings.serper_api_key},
            json={
                "q": query,
                "num": max_results,
                "gl": "cn",
                "hl": "zh-cn",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("organic", []):
            results.append({
                "title": item.get("title", "无标题"),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "source": urlparse(item.get("link", "")).netloc or "未知来源",
            })
        return results

    except Exception as e:
        logger.error("Serper search failed: %s", e)
        return []


async def search_web(query: str, max_results: int = 10) -> list[dict]:
    """Search the web using the first available search API.

    Priority: Tavily > Serper > empty
    """
    results = await search_web_tavily(query, max_results)
    if not results:
        results = await search_web_serper(query, max_results)
    if not results:
        logger.info("No search API configured, returning empty results for: %s", query)
    return results


# --- Page Fetch & Extract ---

async def fetch_page(url: str) -> str | None:
    """Fetch a web page and return its HTML content."""
    try:
        client = _get_client()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPError as e:
        logger.warning("Fetch failed for %s: %s", url, e)
        return None


def extract_article(html: str, url: str) -> dict | None:
    """Extract article content from HTML using readability + BS4."""
    try:
        doc = Document(html)
        title = doc.title()

        summary_html = doc.summary(html_partial=True)
        soup = BeautifulSoup(summary_html, "lxml")
        content_text = soup.get_text(separator="\n", strip=True)

        parsed = urlparse(url)
        source = parsed.netloc or "未知来源"

        published_at = _extract_date(html) or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        summary = content_text[:300].strip()
        if len(content_text) > 300:
            summary += "..."

        return {
            "title": title.strip() or "无标题",
            "content": content_text,
            "summary": summary,
            "source": source,
            "url": url,
            "published_at": published_at,
        }
    except Exception as e:
        logger.error("Extraction failed for %s: %s", url, e)
        return None


def _extract_date(html: str) -> str | None:
    """Try to extract a publication date from HTML meta tags."""
    soup = BeautifulSoup(html, "lxml")

    for tag_name, attr_name in [("meta", "property"), ("meta", "name")]:
        for tag in soup.find_all(tag_name):
            prop = tag.get(attr_name, "").lower()
            if any(k in prop for k in ["date", "time", "published"]):
                content = tag.get("content", "")
                if content:
                    return _normalize_date(content)

    time_tag = soup.find("time")
    if time_tag:
        dt = time_tag.get("datetime") or time_tag.get_text(strip=True)
        if dt:
            return _normalize_date(dt)

    return None


def _normalize_date(date_str: str) -> str:
    """Normalize various date formats to 'YYYY-MM-DD HH:MM'."""
    date_str = date_str.strip()
    for fmt in [
        "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    ]:
        try:
            dt = datetime.strptime(date_str[:19], fmt)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return date_str[:16]


def _is_duplicate(url: str, topic_name: str) -> bool:
    """Check if an article with this URL already exists for the topic."""
    with SessionLocal() as db:
        return db.query(ArticleModel).filter(
            ArticleModel.url == url,
            ArticleModel.topic == topic_name,
        ).first() is not None


# --- LLM Summary Generation ---

async def generate_llm_summary(title: str, content: str) -> str:
    """Use LLM to generate a concise summary of the article.

    Falls back to truncation if LLM is not available.
    """
    if not settings.llm_configured:
        # Fallback: just truncate
        return content[:300].strip() + ("..." if len(content) > 300 else "")

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {
                    "role": "system",
                    "content": "你是资讯摘要助手。请用1-2句话概括文章的大方向主题。要求:只说明文章讨论了什么主题/领域;不需要包含具体数据、参数、数值;侧重讲了什么方向而非讲了什么细节。示例:好->该文章介绍了小米SU7电动汽车的详细功能配置参数,分析了其市场定位和竞争力。坏->小米SU7搭载骁龙8295芯片,续航700km。<-太详细",
                },
                {
                    "role": "user",
                    "content": f"标题：{title}\n\n内容：{content[:3000]}",
                },
            ],
            temperature=0.3,
            max_tokens=300,
        )
        return resp.choices[0].message.content or content[:300]

    except Exception as e:
        logger.warning("LLM summary generation failed: %s", e)
        return content[:300].strip() + ("..." if len(content) > 300 else "")


# --- Main Ingestion Pipeline ---

async def ingest_url(url: str, topic_name: str, auto_bookmark: bool = False) -> ArticleModel | None:
    """Full ingestion pipeline for a single URL (V2 — 质量审查+格式化+摘要优化).

    1. Fetch page
    2. Extract article
    3. ⭐ 质量审查（不通过直接丢弃）
    4. ⭐ 内容格式化（HTML→MD）
    5. Deduplicate
    6. ⭐ 摘要生成（大方向概述，不含详细参数）
    7. Store in DB（+quality_score/quality_label/expires_at）
    8. 收藏不再自动入KB（解耦）
    """
    html = await fetch_page(url)
    if not html:
        return None

    article_data = extract_article(html, url)
    if not article_data:
        return None

    # ⭐ Step 2: 质量审查
    from app.services.consultation.quality import quality_check
    q_result = quality_check(article_data["title"], article_data["content"], url)
    if not q_result.passed:
        logger.info("Quality check FAILED for '%s' (score=%d, reason=%s) — discarding",
                     article_data["title"][:50], q_result.score, q_result.reason)
        return None
    logger.info("Quality check PASSED for '%s' (score=%d, label=%s)",
                article_data["title"][:50], q_result.score, q_result.label)

    # ⭐ Step 3: 内容格式化（HTML→MD）
    from app.services.consultation.formatter import format_content
    formatted_content = format_content(html, article_data["content"])
    article_data["content"] = formatted_content

    # Deduplicate（在格式化之后检查，避免重复格式化）
    if _is_duplicate(url, topic_name):
        logger.info("Skipping duplicate: %s for topic '%s'", url, topic_name)
        return None

    # ⭐ Step 4: 摘要生成（大方向概述，不含详细参数）
    enhanced_summary = await generate_llm_summary(article_data["title"], article_data["content"])
    article_data["summary"] = enhanced_summary

    # ⭐ Step 5: 计算过期时间
    from app.services.consultation.cleanup import compute_expires_at
    expires_at = compute_expires_at(bookmarked=auto_bookmark)

    # Store
    with SessionLocal() as db:
        article = ArticleModel(
            topic=topic_name,
            title=article_data["title"],
            source=article_data["source"],
            published_at=article_data["published_at"],
            summary=article_data["summary"],
            url=article_data["url"],
            content=article_data["content"],
            bookmarked=auto_bookmark,
            quality_score=q_result.score,
            quality_label=q_result.label,
            expires_at=expires_at,
        )
        db.add(article)
        db.commit()
        db.refresh(article)
        logger.info(
            "Ingested article %d: '%s' from %s for topic '%s' (quality=%d/%s)",
            article.id, article.title, article.source, topic_name, q_result.score, q_result.label,
        )

        # ⭐ 收藏不再自动入KB（已解耦）
        # 入KB路径：报告生成后手动选择入KB

    return article


async def ingest_search_results(
    topic_name: str,
    keywords: list[str],
    max_results: int = 10,
    auto_bookmark: bool = False,
) -> list[ArticleModel]:
    """Search the web with keywords and ingest found articles.

    1. Build search query from keywords
    2. Search via Tavily/Serper
    3. For each result, fetch full page and ingest
    4. Return list of successfully ingested articles
    """
    query = " ".join(keywords)
    logger.info("Searching web for topic '%s' with query: %s", topic_name, query)

    search_results = await search_web(query, max_results)
    ingested: list[ArticleModel] = []

    for result in search_results:
        url = result.get("url", "")
        if not url:
            continue

        try:
            article = await ingest_url(url, topic_name, auto_bookmark=auto_bookmark)
            if article is not None:
                ingested.append(article)
        except Exception as e:
            logger.warning("Failed to ingest %s: %s", url, e)

        # Be polite - don't hammer servers
        import asyncio
        await asyncio.sleep(1.0)

    logger.info("Ingested %d articles for topic '%s'", len(ingested), topic_name)
    return ingested


async def run_topic_collection(topic_id: int, topic_name: str, keywords: list[str]) -> int:
    """Run a full collection cycle for a topic.

    Returns the number of new articles ingested.
    """
    logger.info("Starting collection for topic '%s' (id=%d)", topic_name, topic_id)

    articles = await ingest_search_results(
        topic_name=topic_name,
        keywords=keywords,
        max_results=10,
        auto_bookmark=False,
    )

    logger.info("Collection complete for topic '%s': %d new articles", topic_name, len(articles))
    return len(articles)
