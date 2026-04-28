from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ArticleModel(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    published_at: Mapped[str] = mapped_column(String(50), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    bookmarked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ─── Phase 1 新增字段 ───
    quality_score: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    # 质量评分 0-100，-1=未评分（旧数据兼容）

    quality_label: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    # "high" / "medium" / "low"(low不存DB) / ""(旧数据)

    expires_at: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    # 过期时间 ISO格式，收藏后为空字符串(永不过期)
