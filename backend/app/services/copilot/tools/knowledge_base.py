"""Copilot 工具：知识库管理（V2 适配）。

知识库 CRUD + 文档管理 + 索引触发。
"""

from datetime import datetime, timezone

from langchain_core.tools import tool


def _list_knowledge_bases_impl() -> str:
    from app.db.session import SessionLocal
    from app.db.models import KnowledgeBaseModel

    with SessionLocal() as db:
        kbs = db.query(KnowledgeBaseModel).order_by(KnowledgeBaseModel.id).all()

    if not kbs:
        return "暂无知识库。你可以让我新建一个。"

    lines = []
    for kb in kbs:
        enabled = "🟢" if kb.enabled else "🔴"
        lines.append(f"  {enabled} [{kb.id}] {kb.name} (文档:{kb.document_count}, 分块:{kb.chunk_count})")

    return f"共 {len(kbs)} 个知识库：\n" + "\n".join(lines)


@tool
def list_knowledge_bases() -> str:
    """列出所有知识库及其文档数量。"""
    return _list_knowledge_bases_impl()


def _create_knowledge_base_impl(name: str, description: str = "") -> str:
    from app.db.session import SessionLocal
    from app.db.models import KnowledgeBaseModel

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with SessionLocal() as db:
        kb = KnowledgeBaseModel(
            name=name,
            description=description,
            kb_type="upload",
            enabled=True,
            document_count=0,
            chunk_count=0,
            created_at=now,
            updated_at=now,
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

        # Delete associated documents
        db.query(KbDocumentModel).filter(KbDocumentModel.kb_id == kb_id).delete()
        kb_name = kb.name
        db.delete(kb)
        db.commit()

    return f"✅ 知识库「{kb_name}」及其文档已删除"


@tool
def delete_knowledge_base(kb_id: int) -> str:
    """删除知识库及其所有文档。"""
    return _delete_knowledge_base_impl(kb_id)


def _upload_document_impl(kb_id: int, title: str, content: str, file_type: str = "text") -> str:
    from app.db.session import SessionLocal
    from app.db.models import KnowledgeBaseModel, KbDocumentModel

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with SessionLocal() as db:
        kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
        if not kb:
            return f"❌ 未找到知识库 ID={kb_id}"

        doc = KbDocumentModel(
            kb_id=kb_id,
            title=title,
            source_type="upload",
            file_path="",  # text content uploaded via tool has no file
            file_type=file_type,
            index_status="pending",
            created_at=now,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        # Update document_count
        kb.document_count = (kb.document_count or 0) + 1
        kb.updated_at = now
        db.commit()

        kb_name = kb.name
        doc_id = doc.id

    return f"✅ 文档「{title}」已上传到知识库「{kb_name}」(doc_id={doc_id}，索引待同步)"


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
        status_map = {"pending": "⏳", "indexing": "🔄", "indexed": "✅", "failed": "❌"}
        icon = status_map.get(getattr(d, "index_status", "pending"), "⏳")
        lines.append(f"  {icon} [{d.id}] {d.title} ({d.file_type})")

    return f"📚 知识库「{kb_name}」共 {len(docs)} 个文档：\n" + "\n".join(lines)


@tool
def list_kb_documents(kb_id: int) -> str:
    """列出指定知识库的所有文档。"""
    return _list_kb_documents_impl(kb_id)


def _toggle_knowledge_base_impl(kb_id: int) -> str:
    from app.db.session import SessionLocal
    from app.db.models import KnowledgeBaseModel

    with SessionLocal() as db:
        kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == kb_id).first()
        if not kb:
            return f"❌ 未找到知识库 ID={kb_id}"

        kb.enabled = not kb.enabled
        kb.updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        db.commit()
        kb_name = kb.name
        status = "启用" if kb.enabled else "禁用"

    return f"✅ 知识库「{kb_name}」已{status}"


@tool
def toggle_knowledge_base(kb_id: int) -> str:
    """启用/禁用知识库（开关切换）。"""
    return _toggle_knowledge_base_impl(kb_id)


# Export all KB management tools for sub-agent registration
TOOLS = [
    list_knowledge_bases,
    create_knowledge_base,
    delete_knowledge_base,
    upload_document,
    list_kb_documents,
    toggle_knowledge_base,
]
