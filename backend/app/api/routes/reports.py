from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import ArticleModel, ReportModel
from app.db.session import get_db
from app.schemas.report import ReportRead, ReportGenerateRequest

router = APIRouter()


@router.get("", response_model=list[ReportRead])
def list_reports(db: Session = Depends(get_db)):
    return db.query(ReportModel).order_by(ReportModel.id.desc()).all()


@router.get("/{report_id}", response_model=ReportRead)
def get_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(report_id: int, db: Session = Depends(get_db)):
    """Delete a report."""
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    db.delete(report)
    db.commit()


@router.post("/generate", response_model=ReportRead, status_code=status.HTTP_201_CREATED)
async def generate_report(payload: ReportGenerateRequest, db: Session = Depends(get_db)):
    """Generate a report from selected articles with optional prompt.

    Flow:
    1. Get articles: either from article_ids (explicit selection) or all bookmarked
    2. Use LangGraph to analyze and generate
    3. Store and return
    """
    # Get articles
    if payload.article_ids:
        articles = db.query(ArticleModel).filter(
            ArticleModel.id.in_(payload.article_ids)
        ).all()
    else:
        articles = db.query(ArticleModel).filter(ArticleModel.bookmarked == True).all()  # noqa: E712

    if not articles:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="没有选中的资讯可供生成报告。请选择至少一篇资讯。",
        )

    # Prepare articles for LangGraph workflow
    articles_data = [
        {
            "title": a.title,
            "source": a.source,
            "summary": a.summary,
            "content": a.content or a.summary,
            "topic": a.topic,
        }
        for a in articles
    ]

    # Build topic info
    topic_names = sorted(set(a.topic for a in articles))
    topic = "、".join(topic_names) if topic_names else None

    # Use LangGraph report generation
    from app.services.report_generator import generate_report_with_langgraph
    result = await generate_report_with_langgraph(
        articles=articles_data,
        topic=topic,
        title=payload.title,
        prompt=payload.prompt,
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    report = ReportModel(
        title=result["title"],
        topic=topic or "",
        created_at=now,
        summary=result["summary"],
        content=result.get("full_content", result["summary"]),
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    return report


@router.get("/{report_id}/export/markdown")
def export_report_markdown(report_id: int, db: Session = Depends(get_db)):
    """Export a report as Markdown file."""
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    md_content = f"# {report.title}\n\n"
    md_content += f"> 生成时间：{report.created_at}\n\n"
    if report.topic:
        md_content += f"**专题**：{report.topic}\n\n---\n\n"
    md_content += f"## 摘要\n\n{report.summary}\n\n"
    if report.content and report.content != report.summary:
        md_content += f"---\n\n## 详细内容\n\n{report.content}\n"

    from io import BytesIO
    buf = BytesIO(md_content.encode("utf-8"))

    filename = f"{report.title}.md"
    return StreamingResponse(
        buf,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/{report_id}/export/pdf")
def export_report_pdf(report_id: int, db: Session = Depends(get_db)):
    """Export a report as PDF file.

    Uses markdown → HTML → PDF conversion.
    Falls back to markdown if PDF libraries are not available.
    """
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    # Build HTML content
    html_content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: "Microsoft YaHei", "SimHei", sans-serif; margin: 40px; line-height: 1.8; color: #333; }}
  h1 {{ color: #1a1a1a; border-bottom: 2px solid #06b6d4; padding-bottom: 8px; }}
  h2 {{ color: #333; margin-top: 24px; }}
  blockquote {{ color: #666; border-left: 3px solid #06b6d4; padding-left: 12px; margin: 16px 0; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 20px 0; }}
  .meta {{ color: #888; font-size: 14px; }}
</style>
</head><body>
<h1>{report.title}</h1>
<p class="meta">生成时间：{report.created_at}</p>
{f'<p class="meta">专题：{report.topic}</p>' if report.topic else ''}
<hr/>
<h2>摘要</h2>
<p>{report.summary.replace(chr(10), '<br/>')}</p>
"""
    if report.content and report.content != report.summary:
        html_content += f"<hr/><h2>详细内容</h2><p>{report.content.replace(chr(10), '<br/>')}</p>"

    html_content += "</body></html>"

    # Try weasyprint for PDF
    try:
        import weasyprint
        from io import BytesIO
        buf = BytesIO()
        weasyprint.HTML(string=html_content).write_pdf(buf)
        buf.seek(0)
        filename = f"{report.title}.pdf"
        return StreamingResponse(
            buf,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )
    except ImportError:
        # weasyprint not installed, fall back to HTML download
        from io import BytesIO
        buf = BytesIO(html_content.encode("utf-8"))
        filename = f"{report.title}.html"
        return StreamingResponse(
            buf,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )
