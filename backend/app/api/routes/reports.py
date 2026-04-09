from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import ArticleModel, ReportModel
from app.db.session import get_db
from app.schemas.report import ReportRead

router = APIRouter()


class ReportGenerateRequest(BaseModel):
    topic: str | None = None
    title: str | None = None


class ReportGenerateResponse(BaseModel):
    id: int
    title: str
    created_at: str
    summary: str

    model_config = {"from_attributes": True}


@router.get("", response_model=list[ReportRead])
def list_reports(db: Session = Depends(get_db)):
    return db.query(ReportModel).order_by(ReportModel.id.desc()).all()


@router.post("/generate", response_model=ReportGenerateResponse, status_code=status.HTTP_201_CREATED)
async def generate_report(payload: ReportGenerateRequest, db: Session = Depends(get_db)):
    """Generate a report using LangGraph workflow.

    Collects bookmarked articles, analyzes with LLM (if configured),
    and produces a structured report.
    """
    query = db.query(ArticleModel).filter(ArticleModel.bookmarked == True)  # noqa: E712
    if payload.topic:
        query = query.filter(ArticleModel.topic == payload.topic)

    bookmarked_articles = query.order_by(ArticleModel.id.desc()).all()

    if not bookmarked_articles:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No bookmarked articles found for report generation.",
        )

    # Prepare articles for LangGraph workflow
    articles_data = [
        {
            "title": a.title,
            "source": a.source,
            "summary": a.summary,
            "content": a.content or a.summary,
        }
        for a in bookmarked_articles
    ]

    # Use LangGraph report generation
    from app.services.report_generator import generate_report_with_langgraph
    result = await generate_report_with_langgraph(
        articles=articles_data,
        topic=payload.topic,
        title=payload.title,
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    report = ReportModel(
        title=result["title"],
        created_at=now,
        summary=result["summary"],
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    return report
