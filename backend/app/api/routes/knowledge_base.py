"""知识库管理API — 文件上传+异步入库+启用禁用。"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import KnowledgeBaseModel, KbDocumentModel
from app.db.session import get_db
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Schemas ────────────────────────────────────────────────────

class KnowledgeBaseCreate(BaseModel):
    name: str
    description: str = ""


class KnowledgeBaseRead(BaseModel):
    id: int
    name: str
    description: str
    kb_type: str
    enabled: bool = True
    document_count: int = 0
    chunk_count: int = 0
    created_at: str = ""
    updated_at: str = ""

    model_config = {"from_attributes": True}


class KbDocumentRead(BaseModel):
    id: int
    kb_id: int
    title: str
    file_path: str = ""
    file_type: str = "text"
    source_type: str = "upload"
    source: str = ""
    index_status: str = "pending"
    chunk_count: int = 0
    created_at: str = ""
    indexed_at: str = ""

    model_config = {"from_attributes": True}


# ─── KB CRUD ────────────────────────────────────────────────────

@router.get("", response_model=list[KnowledgeBaseRead])
def list_knowledge_bases(db: Session = Depends(get_db)):
    return db.query(KnowledgeBaseModel).order_by(KnowledgeBaseModel.id.desc()).all()


@router.post("", response_model=KnowledgeBaseRead, status_code=status.HTTP_201_CREATED)
def create_knowledge_base(payload: KnowledgeBaseCreate, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    kb = KnowledgeBaseModel(
        name=payload.name, description=payload.description,
        kb_type="upload", enabled=True,
        document_count=0, chunk_count=0,
        created_at=now, updated_at=now,
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(kb_id: int, db: Session = Depends(get_db)):
    kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
    if kb is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # 删除所有文档的文件
    docs = db.query(KbDocumentModel).filter(KbDocumentModel.kb_id == kb_id).all()
    for doc in docs:
        if doc.file_path:
            try:
                file_path = Path(settings.storage_base_dir) / doc.file_path
                if file_path.exists():
                    file_path.unlink()
            except Exception:
                pass

    # 删除RAG向量
    try:
        from app.services.knowledge.engine import delete_kb_from_index
        await delete_kb_from_index(kb_id)
    except Exception:
        pass

    db.query(KbDocumentModel).filter(KbDocumentModel.kb_id == kb_id).delete()
    db.delete(kb)
    db.commit()


# ─── 启用/禁用 ──────────────────────────────────────────────────

@router.patch("/{kb_id}/toggle")
def toggle_knowledge_base(kb_id: int, db: Session = Depends(get_db)):
    """切换知识库启用/禁用状态"""
    from app.services.knowledge.manager import toggle_kb_enabled
    result = toggle_kb_enabled(kb_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ─── 文档上传 ──────────────────────────────────────────────────

@router.post("/{kb_id}/documents/upload", response_model=KbDocumentRead)
async def upload_file_to_kb(
    kb_id: int,
    file: UploadFile = File(...),
    source_type: str = Form("upload"),
    report_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    """上传文件到知识库（异步入库）

    支持:
    - 用户手动上传文件 (source_type="upload")
    - 报告入库 (source_type="report", report_id=XX)
    """
    kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
    if kb is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # 确定文件来源和路径
    if source_type == "report" and report_id:
        # 报告入库：文件已在 reports/ 目录
        from app.db.models import ReportModel
        report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")

        file_path = report.file_path
        title = report.title
        file_type = "md"
        source_desc = f"报告入库: {report.title}"
    else:
        # 用户上传：保存文件到 uploads/kb/{kb_id}/
        content_bytes = await file.read()
        filename = file.filename or "未命名文件"
        file_type = _detect_file_type(filename)

        # ─── 去重检查：同KB下同名文件视为重复 ───
        existing = db.query(KbDocumentModel).filter(
            KbDocumentModel.kb_id == kb_id,
            KbDocumentModel.title == filename,
            KbDocumentModel.source_type == "upload",
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"知识库中已存在同名文档「{filename}」(ID: {existing.id}，状态: {existing.index_status})，请先删除再上传",
            )

        save_dir = Path(settings.storage_base_dir) / "uploads" / "kb" / str(kb_id)
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / filename

        if save_path.exists():
            stem = save_path.stem
            save_path = save_dir / f"{stem}_{int(time.time())}{save_path.suffix}"

        save_path.write_bytes(content_bytes)

        file_path = str(save_path.relative_to(settings.storage_base_dir)).replace("\\", "/")
        title = filename
        source_desc = "用户上传"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    # 创建DB记录
    doc = KbDocumentModel(
        kb_id=kb_id, title=title,
        file_path=file_path, file_type=file_type,
        source_type=source_type, source=source_desc,
        report_id=report_id if source_type == "report" else None,
        index_status="pending",
        created_at=now,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # 加入异步入库队列
    from app.services.knowledge.manager import enqueue_index_task
    await enqueue_index_task(doc.id)

    return doc


@router.post("/{kb_id}/documents/from-report/{report_id}")
async def index_report_to_kb_via_kb(kb_id: int, report_id: int, db: Session = Depends(get_db)):
    """将已有报告入库到知识库（快捷操作 — 走 indexer 文件路径）"""
    from app.db.models import ReportModel, KbDocumentModel

    kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
    if kb is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status != "ready":
        raise HTTPException(status_code=400, detail="报告尚未生成完成")
    if not report.file_path:
        raise HTTPException(status_code=400, detail="报告文件不存在")

    # 检查重复
    existing = db.query(KbDocumentModel).filter(
        KbDocumentModel.source_type == "report",
        KbDocumentModel.report_id == report_id,
        KbDocumentModel.kb_id == kb_id,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="该报告已在此知识库中")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    doc = KbDocumentModel(
        kb_id=kb_id,
        title=report.title,
        file_path=report.file_path,
        file_type="md",
        source_type="report",
        source=f"报告入库: {report.title}",
        report_id=report_id,
        index_status="pending",
        created_at=now,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # 加入异步入库队列
    from app.services.knowledge.manager import enqueue_index_task
    await enqueue_index_task(doc.id)

    return doc


# ─── 文档管理 ──────────────────────────────────────────────────

@router.get("/{kb_id}/documents", response_model=list[KbDocumentRead])
def list_kb_documents(kb_id: int, db: Session = Depends(get_db)):
    kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
    if kb is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    return db.query(KbDocumentModel).filter(KbDocumentModel.kb_id == kb_id).order_by(KbDocumentModel.id.desc()).all()


@router.delete("/{kb_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(kb_id: int, doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(KbDocumentModel).filter(
        KbDocumentModel.id == doc_id, KbDocumentModel.kb_id == kb_id
    ).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    deleted_chunks = doc.chunk_count or 0

    # 删除物理文件（仅 source_type=upload 的才删，report 的文件属于报告模块）
    if doc.file_path and doc.source_type != "report":
        try:
            file_path = Path(settings.storage_base_dir) / doc.file_path
            if file_path.exists():
                file_path.unlink()
        except Exception:
            pass

    # 从 ChromaDB 向量库删除（按 kb_doc_id metadata 精确匹配）
    try:
        from app.services.knowledge.engine import delete_doc_vectors
        await delete_doc_vectors(kb_doc_id=doc.id, kb_id=kb_id)
    except Exception as e:
        logger.warning("Failed to delete vectors for doc %d: %s", doc.id, e)

    # 从 docstore 删除 + 刷新 BM25 缓存
    try:
        from app.services.knowledge.docstore import delete_nodes_by_kb_doc_id
        delete_nodes_by_kb_doc_id(doc.id)
    except Exception as e:
        logger.warning("Failed to delete docstore nodes for doc %d: %s", doc.id, e)

    try:
        from app.services.knowledge.bm25 import invalidate_bm25_cache
        invalidate_bm25_cache()
    except Exception:
        pass

    # 删除 DB 记录 + 更新 KB 统计
    db.delete(doc)
    kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
    if kb:
        kb.document_count = max(0, (kb.document_count or 0) - 1)
        kb.chunk_count = max(0, (kb.chunk_count or 0) - deleted_chunks)
        kb.updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    db.commit()


# ─── Helpers ────────────────────────────────────────────────────

def _detect_file_type(filename: str) -> str:
    from app.services.knowledge.parser import detect_file_type
    return detect_file_type(filename)
