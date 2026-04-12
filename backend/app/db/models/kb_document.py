from sqlalchemy import Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KbDocumentModel(Base):
    __tablename__ = "kb_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    kb_id: Mapped[int] = mapped_column(Integer, ForeignKey("knowledge_bases.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False, default="用户上传")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    file_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    # "text", "pdf", "md", "docx"
    indexed: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(50), nullable=False, default="")
