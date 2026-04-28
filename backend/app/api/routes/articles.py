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
    """收藏或取消收藏文章。

    V2: 收藏=永久保存+感兴趣标记，不再自动入知识库。
    取消收藏时设置过期时间（未收藏资讯到期自动清理）。
    """
    from app.services.consultation.cleanup import compute_expires_at

    article = db.query(ArticleModel).filter(ArticleModel.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    article.bookmarked = payload.bookmarked

    # 收藏/取消收藏时更新过期时间
    article.expires_at = compute_expires_at(bookmarked=payload.bookmarked)

    db.commit()
    db.refresh(article)

    # ⭐ V2: 收藏不再触发入KB，取消收藏不再从KB删除
    # 知识库入库路径：报告生成后用户手动选择入KB

    return article


@router.delete("/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_article(article_id: int, db: Session = Depends(get_db)):
    """删除文章。V2: 收藏的文章也可删除（用户主动操作优先）。"""
    article = db.query(ArticleModel).filter(ArticleModel.id == article_id).first()
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    # V2: 文章不再自动入向量库，此处仅做安全清理（旧数据兼容）
    try:
        import chromadb
        from app.core.config import settings
        client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        collection = client.get_or_create_collection("researchpilot_all")
        # 删除旧格式 article_{id} 的向量
        old_doc_ids = [f"article_{article.id}"]
        for did in old_doc_ids:
            try:
                collection.delete(where={"doc_id": did})
            except Exception:
                pass
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
    from app.services.consultation.ingestion import ingest_url as _ingest

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
        from app.services.consultation.ingestion import run_topic_collection
        count = await run_topic_collection(topic.id, topic.name, keywords)
        return TopicCollectResponse(
            topic_name=topic.name,
            new_articles=count,
            message=f"专题「{topic.name}」采集完成，新增 {count} 篇资讯",
        )
