from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db

from app.core.config import settings

router = APIRouter()


@router.get("/health")
def health_check():
    return {"status": "ok", "service": "researchpilot-backend", "version": "0.2.0"}


@router.get("/status")
def system_status():
    """Return system capability status for the frontend."""
    return {
        "llm_configured": settings.llm_configured,
        "search_configured": bool(settings.tavily_api_key or settings.serper_api_key),
        "rag_available": settings.embedding_configured,
        "llm_model": settings.llm_model if settings.llm_configured else None,
        "embedding_model": settings.embedding_model if settings.embedding_configured else None,
        "search_provider": "tavily" if settings.tavily_api_key else ("serper" if settings.serper_api_key else None),
    }


@router.get("/scheduler/jobs")
def scheduler_jobs():
    """Return scheduled collection jobs."""
    from app.services.scheduler import get_scheduled_jobs
    return {"jobs": get_scheduled_jobs()}


@router.get("/rag/stats")
def rag_stats(db: Session = Depends(get_db)):
    """Return RAG system statistics: index info + DB counts.
    
    Provides visibility into the health of the RAG pipeline.
    """
    from app.db.models import ArticleModel, KnowledgeBaseModel, KbDocumentModel
    from app.services.rag.engine import get_collection_stats

    # Vector store stats
    vector_stats = get_collection_stats()

    # DB stats
    total_articles = db.query(ArticleModel).count()
    bookmarked_articles = db.query(ArticleModel).filter(ArticleModel.bookmarked == True).count()  # noqa: E712
    total_kbs = db.query(KnowledgeBaseModel).count()
    total_kb_docs = db.query(KbDocumentModel).count()
    indexed_kb_docs = db.query(KbDocumentModel).filter(KbDocumentModel.indexed == 1).count()  # noqa: E712

    return {
        "vector_store": vector_stats,
        "database": {
            "total_articles": total_articles,
            "bookmarked_articles": bookmarked_articles,
            "total_knowledge_bases": total_kbs,
            "total_kb_documents": total_kb_docs,
            "indexed_kb_documents": indexed_kb_docs,
            "unindexed_kb_documents": total_kb_docs - indexed_kb_docs,
        },
    }


@router.post("/rag/reindex")
async def rag_reindex(async_mode: bool = True, db: Session = Depends(get_db)):
    """Trigger reindexing of all bookmarked articles.
    
    By default runs asynchronously (async_mode=True) and returns a task_id.
    Set async_mode=False for synchronous execution.
    """
    if async_mode:
        from app.services.tasks import start_reindex_task
        task = await start_reindex_task()
        return {
            "task_id": task.id,
            "message": f"索引重建已在后台启动，任务ID：{task.id}。通过 /api/tasks/{task.id} 查看进度。",
        }
    else:
        from app.services.rag.engine import reindex_all_articles
        result = await reindex_all_articles(db)
        return {
            "message": f"重新索引完成：成功 {result['success']} 篇，失败 {result['failed']} 篇",
            "result": result,
        }
