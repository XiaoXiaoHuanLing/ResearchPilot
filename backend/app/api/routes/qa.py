from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import ArticleModel
from app.db.session import get_db
from app.services.rag.engine import rag_query

router = APIRouter()


class QARequest(BaseModel):
    question: str


@router.post("/query")
async def query_qa(payload: QARequest, db: Session = Depends(get_db)):
    """RAG-powered Q&A endpoint.

    If OpenAI API key is configured, uses real embedding + LLM retrieval.
    Otherwise falls back to simple keyword matching from stored articles.
    """
    from app.core.config import settings

    if settings.openai_api_key:
        # Real RAG pipeline
        return await rag_query(payload.question, top_k=5)
    else:
        # Fallback: simple keyword-based retrieval from DB
        articles = db.query(ArticleModel).order_by(ArticleModel.id.desc()).limit(3).all()
        citations = [
            {
                "article_id": article.id,
                "title": article.title,
                "source": article.source,
                "url": article.url,
            }
            for article in articles
        ]

        return {
            "question": payload.question,
            "answer": "这是当前阶段的本地检索式 MVP 回答（未配置 LLM API）。系统已从已存储资讯中返回候选来源。配置 RESEARCHPILOT_OPENAI_API_KEY 后将启用真实 RAG 问答。",
            "citations": citations,
        }
