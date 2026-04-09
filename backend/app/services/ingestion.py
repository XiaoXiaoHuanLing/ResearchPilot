"""Source ingestion pipeline: fetch web pages, extract articles, normalize and store.

Pipeline:
1. Fetch: HTTP GET with polite delays and User-Agent
2. Extract: readability-lxml for main content, BS4 for metadata
3. Normalize: clean text, standardize date, generate summary
4. Store: save to DB as ArticleModel, auto-index into RAG if bookmarked
"""

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from readability import Document

from app.db.models import ArticleModel
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

# Polite fetching config
FETCH_TIMEOUT = 30.0
FETCH_DELAY = 1.0  # seconds between requests (respectful crawling)
USER_AGENT = "ResearchPilot/0.1.0 (Research Bot; +https://github.com/openclaw/researchpilot)"

_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
    return _client


async def fetch_page(url: str) -> str | None:
    """Fetch a web page and return its HTML content."""
    try:
        client = await _get_client()
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

        # Get clean content
        summary_html = doc.summary(html_partial=True)
        soup = BeautifulSoup(summary_html, "lxml")
        content_text = soup.get_text(separator="\n", strip=True)

        # Try to extract source name from domain
        parsed = urlparse(url)
        source = parsed.netloc or "未知来源"

        # Try to find publish date
        published_at = _extract_date(html) or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        # Generate a brief summary from first 300 chars
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

    # Check common meta tags
    for tag_name, attr_name in [
        ("meta", "property"),  # article:published_time
        ("meta", "name"),      # date, pubdate, DC.date
    ]:
        for tag in soup.find_all(tag_name):
            prop = tag.get(attr_name, "").lower()
            if any(k in prop for k in ["date", "time", "published"]):
                content = tag.get("content", "")
                if content:
                    return _normalize_date(content)

    # Check <time> element
    time_tag = soup.find("time")
    if time_tag:
        dt = time_tag.get("datetime") or time_tag.get_text(strip=True)
        if dt:
            return _normalize_date(dt)

    return None


def _normalize_date(date_str: str) -> str:
    """Normalize various date formats to 'YYYY-MM-DD HH:MM'."""
    date_str = date_str.strip()

    # Try ISO format first
    for fmt in [
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
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


async def ingest_url(url: str, topic_name: str, auto_bookmark: bool = False) -> ArticleModel | None:
    """Full ingestion pipeline for a single URL.

    1. Fetch page
    2. Extract article
    3. Deduplicate
    4. Store in DB
    5. Optionally auto-bookmark and index into RAG
    """
    # Deduplicate
    if _is_duplicate(url, topic_name):
        logger.info("Skipping duplicate: %s for topic '%s'", url, topic_name)
        return None

    # Fetch
    html = await fetch_page(url)
    if not html:
        return None

    # Extract
    article_data = extract_article(html, url)
    if not article_data:
        return None

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
        )
        db.add(article)
        db.commit()
        db.refresh(article)
        logger.info(
            "Ingested article %d: '%s' from %s for topic '%s'",
            article.id, article.title, article.source, topic_name,
        )

        # Auto-index into RAG if bookmarked
        if auto_bookmark and article.content:
            try:
                from app.services.rag.engine import index_article
                await index_article(article.id, article.title, article.summary, article.content)
            except Exception as e:
                logger.warning("RAG indexing failed for article %d: %s", article.id, e)

    return article


async def ingest_search_results(
    topic_name: str,
    keywords: list[str],
    max_results: int = 10,
    auto_bookmark: bool = False,
) -> list[ArticleModel]:
    """Search and ingest articles for a topic using keywords.

    MVP: Uses a simple web search approach.
    Future: Will integrate with specific source APIs (RSS, APIs, etc.)
    """
    query = " ".join(keywords)
    results: list[ArticleModel] = []

    # MVP: Ingest from a configurable list of source URLs
    # Future versions will use real search APIs or RSS feeds
    logger.info(
        "Ingestion request for topic '%s' with keywords: %s (max %d results)",
        topic_name, query, max_results,
    )

    # For now, this is a placeholder that logs the request
    # Real implementation will connect to search APIs or RSS feeds
    return results


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
