from sqlalchemy import Integer, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KbDocumentModel(Base):
    __tablename__ = "kb_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    kb_id: Mapped[int] = mapped_column(Integer, ForeignKey("knowledge_bases.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)

    # ─── 文件信息 ───
    file_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    file_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, default="upload")
    source: Mapped[str] = mapped_column(String(200), nullable=False, default="用户上传")

    # ─── 索引状态 ───
    index_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    index_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ─── 关联 ───
    report_id: Mapped[int] = mapped_column(Integer, nullable=True)

    # ─── 时间 ───
    created_at: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    indexed_at: Mapped[str] = mapped_column(String(50), nullable=False, default="")
