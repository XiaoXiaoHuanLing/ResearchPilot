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

    # Auto-index into RAG vector store when bookmarked
    if payload.bookmarked and article.content:
        try:
            from app.services.rag.engine import index_article
            await index_article(article.id, article.title, article.summary, article.content)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to index article %d into RAG: %s", article_id, e)

    return article


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


class TopicCollectResponse(BaseModel):
    topic_name: str
    new_articles: int
    message: str


@router.post("/collect", response_model=TopicCollectResponse)
async def collect_topic(payload: TopicCollectRequest, db: Session = Depends(get_db)):
    """Trigger a manual collection run for a topic."""
    from app.db.models import TopicModel

    topic = db.query(TopicModel).filter(TopicModel.id == payload.topic_id).first()
    if topic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")

    keywords = [k.strip() for k in topic.keywords.split(",") if k.strip()]
    from app.services.ingestion import run_topic_collection
    count = await run_topic_collection(topic.id, topic.name, keywords)

    return TopicCollectResponse(
        topic_name=topic.name,
        new_articles=count,
        message=f"专题「{topic.name}」采集完成，新增 {count} 篇资讯",
    )
