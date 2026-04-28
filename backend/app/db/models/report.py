from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReportModel(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    topic: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # ─── 文件路径（不存全文） ───
    file_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    # ─── 关联信息 ───
    article_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # ─── 质量评估 ───
    quality_score: Mapped[float] = mapped_column(default=0.0)
    quality_detail: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ─── 状态 ───
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    # draft / outline_ready / generating / ready / archived

    indexed: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    kb_id: Mapped[int] = mapped_column(Integer, nullable=True)

    # ─── 时间 ───
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(50), nullable=False, default="")
