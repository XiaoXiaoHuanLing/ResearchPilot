"""Chat session and message persistence API routes."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models.chat import ChatSessionModel, ChatMessageModel
from app.db.session import get_db

router = APIRouter()


# --- Schemas ---

class ChatMessageRead(BaseModel):
    id: int
    role: str
    content: str
    mode: str
    search_used: bool
    citations: list = []
    created_at: str


class ChatSessionRead(BaseModel):
    id: int
    title: str
    mode: str
    created_at: str
    updated_at: str
    message_count: int = 0


class ChatSessionDetailRead(BaseModel):
    id: int
    title: str
    mode: str
    created_at: str
    updated_at: str
    messages: list[ChatMessageRead]


class CreateSessionRequest(BaseModel):
    title: str = "新对话"
    mode: str = "hybrid"


class UpdateSessionRequest(BaseModel):
    title: str | None = None


class SaveMessageRequest(BaseModel):
    role: str  # "user" | "assistant"
    content: str
    mode: str = "hybrid"
    search_used: bool = False
    citations: list = []


# --- Endpoints ---

@router.get("", response_model=list[ChatSessionRead])
def list_sessions(db: Session = Depends(get_db)):
    """List all chat sessions."""
    sessions = db.query(ChatSessionModel).order_by(ChatSessionModel.updated_at.desc()).all()
    result = []
    for s in sessions:
        msg_count = db.query(ChatMessageModel).filter(ChatMessageModel.session_id == s.id).count()
        result.append(ChatSessionRead(
            id=s.id, title=s.title, mode=s.mode,
            created_at=s.created_at, updated_at=s.updated_at,
            message_count=msg_count,
        ))
    return result


@router.post("", response_model=ChatSessionRead, status_code=status.HTTP_201_CREATED)
def create_session(payload: CreateSessionRequest, db: Session = Depends(get_db)):
    """Create a new chat session."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    session = ChatSessionModel(
        title=payload.title, mode=payload.mode,
        created_at=now, updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return ChatSessionRead(
        id=session.id, title=session.title, mode=session.mode,
        created_at=session.created_at, updated_at=session.updated_at,
        message_count=0,
    )


@router.get("/{session_id}", response_model=ChatSessionDetailRead)
def get_session(session_id: int, db: Session = Depends(get_db)):
    """Get session details with all messages."""
    session = db.query(ChatSessionModel).filter(ChatSessionModel.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    
    messages = db.query(ChatMessageModel).filter(
        ChatMessageModel.session_id == session_id
    ).order_by(ChatMessageModel.id.asc()).all()
    
    msg_reads = []
    for m in messages:
        citations = []
        try:
            citations = json.loads(m.citations_json) if m.citations_json else []
        except json.JSONDecodeError:
            pass
        msg_reads.append(ChatMessageRead(
            id=m.id, role=m.role, content=m.content,
            mode=m.mode, search_used=m.search_used,
            citations=citations, created_at=m.created_at,
        ))
    
    return ChatSessionDetailRead(
        id=session.id, title=session.title, mode=session.mode,
        created_at=session.created_at, updated_at=session.updated_at,
        messages=msg_reads,
    )


@router.patch("/{session_id}", response_model=ChatSessionRead)
def update_session(session_id: int, payload: UpdateSessionRequest, db: Session = Depends(get_db)):
    """Update session title."""
    session = db.query(ChatSessionModel).filter(ChatSessionModel.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if payload.title is not None:
        session.title = payload.title
    session.updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    db.commit()
    db.refresh(session)
    
    msg_count = db.query(ChatMessageModel).filter(ChatMessageModel.session_id == session_id).count()
    return ChatSessionRead(
        id=session.id, title=session.title, mode=session.mode,
        created_at=session.created_at, updated_at=session.updated_at,
        message_count=msg_count,
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: int, db: Session = Depends(get_db)):
    """Delete a chat session and all its messages."""
    session = db.query(ChatSessionModel).filter(ChatSessionModel.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(session)
    db.commit()


@router.post("/{session_id}/messages", response_model=ChatMessageRead, status_code=status.HTTP_201_CREATED)
def save_message(session_id: int, payload: SaveMessageRequest, db: Session = Depends(get_db)):
    """Save a message to a chat session."""
    session = db.query(ChatSessionModel).filter(ChatSessionModel.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    msg = ChatMessageModel(
        session_id=session_id,
        role=payload.role,
        content=payload.content,
        mode=payload.mode,
        search_used=payload.search_used,
        citations_json=json.dumps(payload.citations, ensure_ascii=False),
        created_at=now,
    )
    db.add(msg)
    
    # Update session timestamp
    session.updated_at = now
    # Auto-title from first user message
    if session.title == "新对话" and payload.role == "user":
        session.title = payload.content[:50] + ("..." if len(payload.content) > 50 else "")
    
    db.commit()
    db.refresh(msg)
    
    return ChatMessageRead(
        id=msg.id, role=msg.role, content=msg.content,
        mode=msg.mode, search_used=msg.search_used,
        citations=payload.citations, created_at=msg.created_at,
    )


@router.get("/{session_id}/history", response_model=list[ChatMessageRead])
def get_session_history(session_id: int, db: Session = Depends(get_db)):
    """Get message history for a session (simpler format for chat context)."""
    session = db.query(ChatSessionModel).filter(ChatSessionModel.id == session_id).first()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    
    messages = db.query(ChatMessageModel).filter(
        ChatMessageModel.session_id == session_id
    ).order_by(ChatMessageModel.id.asc()).all()
    
    result = []
    for m in messages:
        citations = []
        try:
            citations = json.loads(m.citations_json) if m.citations_json else []
        except json.JSONDecodeError:
            pass
        result.append(ChatMessageRead(
            id=m.id, role=m.role, content=m.content,
            mode=m.mode, search_used=m.search_used,
            citations=citations, created_at=m.created_at,
        ))
    return result
