"""知识库工具 — list_knowledge_bases, create_knowledge_base, delete_knowledge_base, upload_document, list_kb_documents"""
import logging
from datetime import datetime, timezone
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _list_knowledge_bases_impl() -> str:
    from app.db.session import SessionLocal
    from app.db.models import KnowledgeBaseModel

    with SessionLocal() as db:
        kbs = db.query(KnowledgeBaseModel).order_by(KnowledgeBaseModel.id).all()

    if not kbs:
        return "暂无知识库。你可以让我新建一个。"

    lines = []
    for kb in kbs:
        kb_type = "收藏型" if kb.kb_type == "bookmarks" else "上传型"
        lines.append(f"  📚 [{kb.id}] {kb.name} ({kb_type}, {kb.article_count} 文档)")

    return f"共 {len(kbs)} 个知识库：\n" + "\n".join(lines)


@tool
def list_knowledge_bases() -> str:
    """列出所有知识库及其文档数量。"""
    return _list_knowledge_bases_impl()


def _create_knowledge_base_impl(name: str, description: str = "") -> str:
    from app.db.session import SessionLocal
    from app.db.models import KnowledgeBaseModel

    with SessionLocal() as db:
        kb = KnowledgeBaseModel(
            name=name, description=description,
            kb_type="upload", is_default=False, article_count=0,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        )
        db.add(kb)
        db.commit()
        db.refresh(kb)

    return f"✅ 知识库「{name}」已创建 (ID={kb.id})"


@tool
def create_knowledge_base(name: str, description: str = "") -> str:
    """新建知识库（上传型）。"""
    return _create_knowledge_base_impl(name, description)


def _delete_knowledge_base_impl(kb_id: int) -> str:
    from app.db.session import SessionLocal
    from app.db.models import KnowledgeBaseModel, KbDocumentModel

    with SessionLocal() as db:
        kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
        if not kb:
            return f"❌ 未找到知识库 ID={kb_id}"

        name = kb.name
        db.query(KbDocumentModel).filter(KbDocumentModel.kb_id == kb_id).delete()
        db.delete(kb)
        db.commit()

    return f"✅ 知识库「{name}」及其文档已删除"


@tool
def delete_knowledge_base(kb_id: int) -> str:
    """删除知识库及其所有文档和向量索引。⚠️ 不可恢复！"""
    return _delete_knowledge_base_impl(kb_id)


def _upload_document_impl(kb_id: int, title: str, content: str, file_type: str = "text") -> str:
    from app.db.session import SessionLocal
    from app.db.models import KbDocumentModel, KnowledgeBaseModel

    with SessionLocal() as db:
        kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
        if not kb:
            return f"❌ 未找到知识库 ID={kb_id}"

        doc = KbDocumentModel(
            kb_id=kb_id, title=title, source="用户上传",
            content=content, file_type=file_type, indexed=False,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        kb.article_count = (kb.article_count or 0) + 1
        db.commit()

        kb_name = kb.name
        doc_id = doc.id

    return f"✅ 文档「{title}」已上传到知识库「{kb_name}」(doc_id={doc_id}，RAG索引待同步)"


@tool
def upload_document(kb_id: int, title: str, content: str, file_type: str = "text") -> str:
    """上传文本文档到指定知识库。必须提供：kb_id(知识库ID)、title(文档标题)、content(文档纯文本内容字符串)。file_type可选(text/markdown)。示例：upload_document(kb_id=1, title="AI笔记", content="人工智能是...")。此工具只接受文本内容，不接受文件路径。"""
    return _upload_document_impl(kb_id, title, content, file_type)


def _list_kb_documents_impl(kb_id: int) -> str:
    from app.db.session import SessionLocal
    from app.db.models import KbDocumentModel, KnowledgeBaseModel

    with SessionLocal() as db:
        kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
        if not kb:
            return f"❌ 未找到知识库 ID={kb_id}"

        docs = db.query(KbDocumentModel).filter(KbDocumentModel.kb_id == kb_id).order_by(KbDocumentModel.id).all()
        kb_name = kb.name

    if not docs:
        return f"知识库「{kb_name}」暂无文档。"

    lines = []
    for d in docs:
        indexed = "✅" if d.indexed else "⏳"
        lines.append(f"  {indexed} [{d.id}] {d.title} ({d.file_type})")

    return f"📚 知识库「{kb_name}」共 {len(docs)} 个文档：\n" + "\n".join(lines)


@tool
def list_kb_documents(kb_id: int) -> str:
    """列出知识库中的所有文档。"""
    return _list_kb_documents_impl(kb_id)


TOOLS = [list_knowledge_bases, create_knowledge_base, delete_knowledge_base, upload_document, list_kb_documents]

FUNC_MAP = {
    "list_knowledge_bases": _list_knowledge_bases_impl,
    "create_knowledge_base": _create_knowledge_base_impl,
    "delete_knowledge_base": _delete_knowledge_base_impl,
    "upload_document": _upload_document_impl,
    "list_kb_documents": _list_kb_documents_impl,
}
