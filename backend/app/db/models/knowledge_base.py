from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class KnowledgeBaseModel(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kb_type: Mapped[str] = mapped_column(String(50), nullable=False, default="bookmarks")
    # "bookmarks" = auto from bookmarked articles, "upload" = user uploaded files
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    article_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False, default="")
