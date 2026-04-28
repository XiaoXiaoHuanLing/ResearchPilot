"""文章工具 — list_articles, bookmark_article, delete_article"""
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _list_articles_impl(topic: str | None = None, keyword: str | None = None,
                        bookmarked: bool | None = None, limit: int = 20) -> str:
    from app.db.session import SessionLocal
    from app.db.models import ArticleModel

    with SessionLocal() as db:
        query = db.query(ArticleModel)
        if topic:
            query = query.filter(ArticleModel.topic == topic)
        if keyword:
            pattern = f"%{keyword}%"
            from sqlalchemy import or_
            query = query.filter(
                ArticleModel.title.ilike(pattern) | ArticleModel.summary.ilike(pattern)
            )
        if bookmarked is not None:
            query = query.filter(ArticleModel.bookmarked == bookmarked)

        articles = query.order_by(ArticleModel.id.desc()).limit(limit).all()

    if not articles:
        return "未找到匹配的文章。"

    lines = []
    for a in articles:
        star = "⭐" if a.bookmarked else "☆"
        lines.append(f"  {star} [{a.id}] {a.title} ({a.source}) [{a.topic}]")

    return f"📄 共 {len(articles)} 篇文章：\n" + "\n".join(lines)


@tool
def list_articles(topic: str | None = None, keyword: str | None = None,
                  bookmarked: bool | None = None, limit: int = 20) -> str:
    """列出资讯文章，支持按专题、关键词、收藏状态过滤。"""
    return _list_articles_impl(topic, keyword, bookmarked, limit)


def _bookmark_article_impl(article_id: int, bookmarked: bool = True) -> str:
    from app.db.session import SessionLocal
    from app.db.models import ArticleModel
    from app.services.consultation.cleanup import compute_expires_at

    with SessionLocal() as db:
        article = db.query(ArticleModel).filter(ArticleModel.id == article_id).first()
        if not article:
            return f"❌ 未找到文章 ID={article_id}"

        article.bookmarked = bookmarked
        article.expires_at = compute_expires_at(bookmarked=bookmarked)
        db.commit()
        title = article.title

    action = "已收藏（永久保存）" if bookmarked else "已取消收藏（将过期清理）"
    return f"✅ 「{title}」{action}"


@tool
def bookmark_article(article_id: int, bookmarked: bool = True) -> str:
    """收藏或取消收藏文章。收藏=永久保存+感兴趣标记，不会入知识库。"""
    return _bookmark_article_impl(article_id, bookmarked)


def _delete_article_impl(article_id: int) -> str:
    from app.db.session import SessionLocal
    from app.db.models import ArticleModel

    with SessionLocal() as db:
        article = db.query(ArticleModel).filter(ArticleModel.id == article_id).first()
        if not article:
            return f"❌ 未找到文章 ID={article_id}"

        title = article.title
        db.delete(article)
        db.commit()

    return f"✅ 文章「{title}」已删除"


@tool
def delete_article(article_id: int) -> str:
    """删除文章。"""
    return _delete_article_impl(article_id)


TOOLS = [list_articles, bookmark_article, delete_article]

FUNC_MAP = {
    "list_articles": _list_articles_impl,
    "bookmark_article": _bookmark_article_impl,
    "delete_article": _delete_article_impl,
}
