from sqlalchemy import Integer, String, Text, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ChatSessionModel(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="新对话")
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="hybrid")
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(50), nullable=False)

    messages: Mapped[list["ChatMessageModel"]] = relationship(
        "ChatMessageModel", back_populates="session", cascade="all, delete-orphan",
        order_by="ChatMessageModel.id",
    )


class ChatMessageModel(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant" | "system"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="hybrid")
    search_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    citations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON string
    created_at: Mapped[str] = mapped_column(String(50), nullable=False)

    session: Mapped["ChatSessionModel"] = relationship("ChatSessionModel", back_populates="messages")
