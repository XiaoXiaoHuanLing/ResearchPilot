"""咨询过期清理模块。

定时清理未收藏且已过期的资讯，防止大量旧咨询占用空间。
"""

import logging
from datetime import datetime, timezone, timedelta

from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import ArticleModel

logger = logging.getLogger(__name__)


def compute_expires_at(bookmarked: bool = False) -> str:
    """计算过期时间。

    收藏 → 不过期(None)
    未收藏 → now + expire_days 天

    Returns:
        ISO格式时间字符串，收藏返回空字符串
    """
    if bookmarked:
        return ""  # 不过期

    expire_days = getattr(settings, 'article_expire_days', 7)
    if expire_days <= 0:
        return ""  # 0=永不过期

    expires = datetime.now(timezone.utc) + timedelta(days=expire_days)
    return expires.strftime("%Y-%m-%d %H:%M")


async def cleanup_expired_articles() -> int:
    """清理过期未收藏资讯。

    删除所有 bookmarked=False 且 expires_at <= now 的文章。

    Returns:
        清理的文章数量
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    with SessionLocal() as db:
        # 查询过期文章
        expired = db.query(ArticleModel).filter(
            ArticleModel.bookmarked == False,  # noqa: E712
            ArticleModel.expires_at != "",
            ArticleModel.expires_at != None,  # noqa: E711
            ArticleModel.expires_at <= now,
        ).all()

        count = len(expired)
        for article in expired:
            db.delete(article)

        if count > 0:
            db.commit()
            logger.info("Cleaned up %d expired articles", count)

    return count
