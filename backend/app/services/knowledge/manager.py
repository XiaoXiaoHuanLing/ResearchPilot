"""知识库管理 + 异步入库队列。

KB CRUD、启用/禁用、异步入库任务队列、入库状态追踪。
"""

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone

from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import KnowledgeBaseModel, KbDocumentModel

logger = logging.getLogger(__name__)

# ─── 异步入库队列 ───
_index_queue: deque[int] = deque()  # KbDocumentModel.id 队列
_index_worker_running: bool = False


async def enqueue_index_task(doc_id: int) -> None:
    """将文档加入异步入库队列"""
    # 更新状态为pending
    with SessionLocal() as db:
        doc = db.query(KbDocumentModel).filter(KbDocumentModel.id == doc_id).first()
        if doc:
            doc.index_status = "pending"
            db.commit()

    _index_queue.append(doc_id)
    logger.info("Enqueued index task for doc %d (queue size: %d)", doc_id, len(_index_queue))

    # 确保worker在跑
    if not _index_worker_running:
        asyncio.create_task(_index_worker())


async def _index_worker() -> None:
    """入库worker：从队列取任务，执行入库"""
    global _index_worker_running
    _index_worker_running = True

    while _index_queue:
        doc_id = _index_queue.popleft()
        try:
            await _do_index_document(doc_id)
        except Exception as e:
            logger.error("Index worker failed for doc %d: %s", doc_id, e)
            with SessionLocal() as db:
                doc = db.query(KbDocumentModel).filter(KbDocumentModel.id == doc_id).first()
                if doc:
                    doc.index_status = "failed"
                    doc.index_error = str(e)[:500]
                    db.commit()

    _index_worker_running = False


async def _do_index_document(doc_id: int) -> None:
    """执行单个文档的入库流程"""
    from app.services.knowledge.indexer import index_document

    with SessionLocal() as db:
        doc = db.query(KbDocumentModel).filter(KbDocumentModel.id == doc_id).first()
        if not doc:
            return

        # 更新状态为正在入库
        doc.index_status = "indexing"
        db.commit()

        file_path = doc.file_path
        kb_id = doc.kb_id
        title = doc.title
        file_type = doc.file_type
        source = doc.source
        source_type = doc.source_type

    # 执行入库
    result = await index_document(
        file_path=file_path,
        kb_id=kb_id,
        title=title,
        file_type=file_type,
        source=source,
        metadata={"kb_doc_id": doc_id, "source_type": source_type},
    )

    # 更新状态
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with SessionLocal() as db:
        doc = db.query(KbDocumentModel).filter(KbDocumentModel.id == doc_id).first()
        if doc:
            doc.index_status = "indexed"
            doc.chunk_count = result.get("leaf_count", 0)
            doc.indexed_at = now
            db.commit()

        # 更新KB统计
        kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
        if kb:
            kb.chunk_count = (kb.chunk_count or 0) + result.get("leaf_count", 0)
            kb.document_count = (kb.document_count or 0) + 1
            kb.updated_at = now
            db.commit()

    logger.info("Document %d indexed: %d chunks", doc_id, result.get("leaf_count", 0))


def toggle_kb_enabled(kb_id: int) -> dict:
    """切换知识库启用/禁用状态"""
    with SessionLocal() as db:
        kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
        if not kb:
            return {"error": f"知识库 ID={kb_id} 不存在"}

        kb.enabled = not kb.enabled
        db.commit()

        status_text = "启用" if kb.enabled else "禁用"
        return {"id": kb.id, "name": kb.name, "enabled": kb.enabled, "message": f"知识库已{status_text}"}


def get_index_status(doc_id: int) -> dict:
    """查询文档入库状态"""
    with SessionLocal() as db:
        doc = db.query(KbDocumentModel).filter(KbDocumentModel.id == doc_id).first()
        if not doc:
            return {"error": "文档不存在"}

        return {
            "doc_id": doc.id,
            "title": doc.title,
            "index_status": doc.index_status,
            "chunk_count": doc.chunk_count,
            "index_error": doc.index_error,
        }
