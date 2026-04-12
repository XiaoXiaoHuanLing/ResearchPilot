from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import ArticleModel
from app.db.session import get_db
from app.schemas.article import ArticleRead, ArticleBookmarkUpdate

router = APIRouter()


@router.get("", response_model=list[ArticleRead])
def list_articles(
    topic: str | None = None,
    keyword: str | None = None,
    bookmarked: bool | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(ArticleModel).order_by(ArticleModel.id.desc())

    if topic is not None:
        query = query.filter(ArticleModel.topic == topic)
    if keyword is not None:
        pattern = f"%{keyword}%"
        query = query.filter(
            (ArticleModel.title.ilike(pattern)) | (ArticleModel.summary.ilike(pattern))
        )
    if bookmarked is not None:
        query = query.filter(ArticleModel.bookmarked == bookmarked)

    return query.all()


@router.post("/{article_id}/bookmark", response_model=ArticleRead)
async def toggle_bookmark(article_id: int, payload: ArticleBookmarkUpdate, db: Session = Depends(get_db)):
    article = db.query(ArticleModel).filter(ArticleModel.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    article.bookmarked = payload.bookmarked
    db.commit()
    db.refresh(article)

    # Auto-index into RAG vector store when bookmarked (bookmarks collection)
    if payload.bookmarked and article.content:
        try:
            from app.services.rag.engine import index_article
            from app.db.models import KnowledgeBaseModel
            # Find the default bookmark KB id
            bk_kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.is_default == True).first()
            kb_id = bk_kb.id if bk_kb else 0
            await index_article(
                article_id=article.id, title=article.title, content=article.content,
                kb_id=kb_id, kb_type="bookmarks",
                source=article.source, url=article.url,
                published_at=article.published_at, topic=article.topic,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to index article %d into RAG: %s", article_id, e)

    # Remove from RAG when un-bookmarked
    if not payload.bookmarked:
        try:
            from app.services.rag.engine import delete_article_from_index
            await delete_article_from_index(article.id, kb_id=None, kb_type="bookmarks")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to remove article %d from RAG: %s", article_id, e)

    return article


@router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_article(article_id: int, db: Session = Depends(get_db)):
    """Delete an article. Only allowed for non-bookmarked articles."""
    article = db.query(ArticleModel).filter(ArticleModel.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    if article.bookmarked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="已收藏的资讯不能删除，请先取消收藏后再删除",
        )

    # Remove from RAG index if present (safety check)
    try:
        from app.services.rag.engine import delete_article_from_index
        await delete_article_from_index(article.id)
    except Exception:
        pass

    db.delete(article)
    db.commit()


class IngestUrlRequest(BaseModel):
    url: str
    topic: str
    auto_bookmark: bool = False


class IngestUrlResponse(BaseModel):
    id: int | None
    title: str | None
    message: str


@router.post("/ingest/url", response_model=IngestUrlResponse)
async def ingest_url(payload: IngestUrlRequest):
    """Manually ingest a URL: fetch, extract, and store as an article."""
    from app.services.ingestion import ingest_url as _ingest

    article = await _ingest(
        url=payload.url,
        topic_name=payload.topic,
        auto_bookmark=payload.auto_bookmark,
    )

    if article is None:
        return IngestUrlResponse(
            id=None,
            title=None,
            message="采集失败：页面无法访问、提取失败或已存在相同URL",
        )

    return IngestUrlResponse(
        id=article.id,
        title=article.title,
        message=f"采集成功：已添加文章「{article.title}」",
    )


class TopicCollectRequest(BaseModel):
    topic_id: int
    async_mode: bool = False  # If True, run in background and return task_id


class TopicCollectResponse(BaseModel):
    topic_name: str
    new_articles: int
    message: str


class TopicCollectAsyncResponse(BaseModel):
    task_id: str
    topic_name: str
    message: str


@router.post("/collect")
async def collect_topic(payload: TopicCollectRequest, db: Session = Depends(get_db)):
    """Trigger a collection run for a topic.
    
    If async_mode=True, runs in background and returns task_id for progress tracking.
    Otherwise, runs synchronously (may timeout for large collections).
    """
    from app.db.models import TopicModel

    topic = db.query(TopicModel).filter(TopicModel.id == payload.topic_id).first()
    if topic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")

    keywords_csv = topic.keywords

    if payload.async_mode:
        # Background execution
        from app.services.tasks import start_collection_task
        task = await start_collection_task(topic.id, topic.name, keywords_csv)
        return TopicCollectAsyncResponse(
            task_id=task.id,
            topic_name=topic.name,
            message=f"专题「{topic.name}」采集已在后台启动，任务ID：{task.id}",
        )
    else:
        # Synchronous execution (original behavior)
        keywords = [k.strip() for k in keywords_csv.split(",") if k.strip()]
        from app.services.ingestion import run_topic_collection
        count = await run_topic_collection(topic.id, topic.name, keywords)
        return TopicCollectResponse(
            topic_name=topic.name,
            new_articles=count,
            message=f"专题「{topic.name}」采集完成，新增 {count} 篇资讯",
        )
