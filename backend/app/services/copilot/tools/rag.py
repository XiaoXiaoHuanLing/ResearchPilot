"""RAG 工具 — rag_query, rag_stats, rag_reindex"""
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


async def _rag_query_impl(question: str, knowledge_base_id: int | None = None, use_hyde: bool = True) -> str:
    from app.services.rag.engine import rag_query as _rag_query
    from app.db.models import KnowledgeBaseModel
    from app.db.session import SessionLocal

    kb_type = None
    if knowledge_base_id is not None:
        with SessionLocal() as db:
            kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == knowledge_base_id).first()
            if kb:
                kb_type = kb.kb_type

    result = await _rag_query(question, top_k=5, kb_id=knowledge_base_id, kb_type=kb_type, use_hyde=use_hyde)
    answer = result.get("answer", "")
    citations = result.get("citations", [])

    if not citations:
        return f"{answer}\n\n（未找到相关引用）"

    cite_lines = []
    for i, c in enumerate(citations[:3], 1):
        score = c.get("relevance_score")
        score_str = f" ({score:.0%})" if score else ""
        cite_lines.append(f"  {i}. {c.get('title', '未知')}{score_str}")

    return f"{answer}\n\n📚 引用：\n" + "\n".join(cite_lines)


@tool
async def rag_query(question: str, knowledge_base_id: int | None = None, use_hyde: bool = True) -> str:
    """查询本地知识库，基于已收藏/上传的文档进行 RAG 问答。

    适用于：用户想基于已有知识回答问题、查找特定信息。
    """
    return await _rag_query_impl(question, knowledge_base_id, use_hyde)


def _rag_stats_impl() -> str:
    from app.services.rag.engine import get_collection_stats
    stats = get_collection_stats()
    if "error" in stats:
        return f"⚠️ RAG 状态查询失败：{stats['error']}"
    return (
        f"📊 RAG 索引状态\n"
        f"  集合: {stats['collection_name']}\n"
        f"  总 chunks: {stats['total_chunks']}\n"
        f"  嵌入模型: {stats['embedding_model']}\n"
        f"  分块: {stats['chunk_size']} / 重叠: {stats['chunk_overlap']}"
    )


@tool
def rag_stats() -> str:
    """查看 RAG 索引状态：集合名、chunk 数、嵌入模型等。"""
    return _rag_stats_impl()


async def _rag_reindex_impl() -> str:
    from app.services.rag.engine import reindex_all_articles
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        result = await reindex_all_articles(db)

    return (
        f"🔄 索引重建完成\n"
        f"  总数: {result['total']}\n"
        f"  ✅ 成功: {result['success']}\n"
        f"  ❌ 失败: {result['failed']}"
    )


@tool
async def rag_reindex() -> str:
    """重建所有已收藏文章的 RAG 索引。耗时操作，慎用。"""
    return await _rag_reindex_impl()


TOOLS = [rag_query, rag_stats, rag_reindex]

FUNC_MAP = {
    "rag_query": _rag_query_impl,
    "rag_stats": _rag_stats_impl,
    "rag_reindex": _rag_reindex_impl,
}
