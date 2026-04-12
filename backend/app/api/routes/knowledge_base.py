from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.orm import Session

from app.db.models import KnowledgeBaseModel, KbDocumentModel
from app.db.session import get_db
from app.schemas.knowledge_base import (
    KnowledgeBaseCreate, KnowledgeBaseRead, KbDocumentRead, KbDocumentUpload,
)

router = APIRouter()


@router.get("", response_model=list[KnowledgeBaseRead])
def list_knowledge_bases(db: Session = Depends(get_db)):
    """List all knowledge bases."""
    return db.query(KnowledgeBaseModel).order_by(KnowledgeBaseModel.id.desc()).all()


@router.post("", response_model=KnowledgeBaseRead, status_code=status.HTTP_201_CREATED)
def create_knowledge_base(payload: KnowledgeBaseCreate, db: Session = Depends(get_db)):
    """Create a new knowledge base (type=upload)."""
    kb = KnowledgeBaseModel(
        name=payload.name,
        description=payload.description,
        kb_type="upload",
        is_default=False,
        article_count=0,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(kb_id: int, db: Session = Depends(get_db)):
    """Delete a knowledge base and all its documents."""
    kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
    if kb is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if kb.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete the default knowledge base")

    # Delete all documents in this KB
    db.query(KbDocumentModel).filter(KbDocumentModel.kb_id == kb_id).delete()

    # Delete from RAG index (bulk delete by kb_id metadata)
    try:
        from app.services.rag.engine import delete_kb_from_index
        await delete_kb_from_index(kb_id)
    except Exception:
        pass

    db.delete(kb)
    db.commit()


@router.get("/{kb_id}/documents", response_model=list[KbDocumentRead])
def list_kb_documents(kb_id: int, db: Session = Depends(get_db)):
    """List all documents in a knowledge base.

    For bookmark-type KBs, returns bookmarked articles as virtual documents.
    """
    kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
    if kb is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    if kb.kb_type == "bookmarks":
        # Return bookmarked articles as documents
        from app.db.models import ArticleModel
        articles = db.query(ArticleModel).filter(ArticleModel.bookmarked == True).order_by(ArticleModel.id.desc()).all()  # noqa: E712
        return [
            KbDocumentRead(
                id=a.id,
                kb_id=kb_id,
                title=a.title,
                source=a.source,
                file_type="article",
                indexed=True,  # bookmarked articles are indexed into RAG
                created_at=a.published_at,
            )
            for a in articles
        ]

    return db.query(KbDocumentModel).filter(KbDocumentModel.kb_id == kb_id).order_by(KbDocumentModel.id.desc()).all()


@router.post("/{kb_id}/documents", response_model=KbDocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    kb_id: int,
    payload: KbDocumentUpload,
    db: Session = Depends(get_db),
):
    """Upload a text document to a knowledge base."""
    kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
    if kb is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if kb.kb_type != "upload":
        raise HTTPException(status_code=400, detail="Cannot add documents to bookmark-type knowledge base")

    doc = KbDocumentModel(
        kb_id=kb_id,
        title=payload.title,
        content=payload.content,
        file_type=payload.file_type,
        indexed=0,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Update KB count
    kb.article_count = db.query(KbDocumentModel).filter(KbDocumentModel.kb_id == kb_id).count()
    db.commit()

    # Index into RAG (per-KB metadata) with source info
    if doc.content:
        try:
            from app.services.rag.engine import index_article
            chunks = await index_article(
                -doc.id, doc.title, doc.content,
                kb_id=kb_id, kb_type="upload",
                source="用户上传", url="", published_at=doc.created_at, topic="",
            )
            if chunks > 0:
                doc.indexed = 1
                db.commit()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to index KB doc %d: %s", doc.id, e)

    return doc


@router.post("/{kb_id}/documents/upload-file", response_model=KbDocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_file(
    kb_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload a file (txt/md) to a knowledge base."""
    kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
    if kb is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if kb.kb_type != "upload":
        raise HTTPException(status_code=400, detail="Cannot add documents to bookmark-type knowledge base")

    # Read file content
    content_bytes = await file.read()
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            content = content_bytes.decode("gbk")
        except UnicodeDecodeError:
            content = content_bytes.decode("latin-1")

    title = file.filename or "未命名文件"
    file_type = "md" if title.endswith(".md") else "text"

    doc = KbDocumentModel(
        kb_id=kb_id,
        title=title,
        content=content,
        file_type=file_type,
        indexed=0,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Update KB count
    kb.article_count = db.query(KbDocumentModel).filter(KbDocumentModel.kb_id == kb_id).count()
    db.commit()

    # Index into RAG (per-KB metadata) with source info
    try:
        from app.services.rag.engine import index_article
        chunks = await index_article(
            -doc.id, doc.title, content,
            kb_id=kb_id, kb_type="upload",
            source="用户上传文件", url="", published_at=doc.created_at, topic="",
        )
        if chunks > 0:
            doc.indexed = 1
            db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to index KB doc %d: %s", doc.id, e)

    return doc


@router.delete("/{kb_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(kb_id: int, doc_id: int, db: Session = Depends(get_db)):
    """Delete a document from a knowledge base."""
    doc = db.query(KbDocumentModel).filter(
        KbDocumentModel.id == doc_id, KbDocumentModel.kb_id == kb_id
    ).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove from RAG
    try:
        from app.services.rag.engine import delete_article_from_index
        await delete_article_from_index(-doc.id, kb_id=kb_id, kb_type="upload")
    except Exception:
        pass

    db.delete(doc)
    kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
    if kb:
        kb.article_count = db.query(KbDocumentModel).filter(KbDocumentModel.kb_id == kb_id).count()
    db.commit()
