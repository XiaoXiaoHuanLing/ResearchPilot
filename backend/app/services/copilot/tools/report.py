"""报告工具 — generate_report, list_reports"""
import logging
from datetime import datetime, timezone
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


async def _generate_report_impl(title: str | None = None, article_ids: list[int] | None = None, prompt: str | None = None) -> str:
    from app.db.session import SessionLocal
    from app.db.models import ArticleModel, ReportModel
    from app.services.report_generator import generate_report_with_langgraph

    with SessionLocal() as db:
        if article_ids:
            articles = db.query(ArticleModel).filter(ArticleModel.id.in_(article_ids)).all()
        else:
            articles = db.query(ArticleModel).filter(ArticleModel.bookmarked == True).all()  # noqa: E712

        if not articles:
            return "❌ 没有可用的文章来生成报告。请先收藏一些文章。"

        article_dicts = [
            {"title": a.title, "source": a.source, "summary": a.summary, "content": a.content[:2000]}
            for a in articles
        ]

        topics = list(set(a.topic for a in articles if a.topic))
        topic = topics[0] if len(topics) == 1 else "综合研究"

        result = await generate_report_with_langgraph(
            articles=article_dicts, topic=topic, title=title, prompt=prompt,
        )

        report = ReportModel(
            title=result.get("title", title or "研究报告"),
            topic=topic,
            summary=result.get("summary", ""),
            content=result.get("full_content", ""),
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        rid = report.id
        rtitle = report.title

    return f"✅ 报告「{rtitle}」已生成 (ID={rid}，基于 {len(articles)} 篇文章)"


@tool
async def generate_report(title: str | None = None, article_ids: list[int] | None = None, prompt: str | None = None) -> str:
    """生成研究报告。基于指定文章或已收藏文章。参数：article_ids(文章ID列表，不传则用所有收藏文章)、title(报告标题)、prompt(报告要求)。此工具执行较慢，只需调用一次。"""
    return await _generate_report_impl(title, article_ids, prompt)


def _list_reports_impl() -> str:
    from app.db.session import SessionLocal
    from app.db.models import ReportModel

    with SessionLocal() as db:
        reports = db.query(ReportModel).order_by(ReportModel.id.desc()).limit(20).all()

    if not reports:
        return "暂无报告。你可以让我基于收藏的文章生成一份。"

    lines = []
    for r in reports:
        lines.append(f"  📝 [{r.id}] {r.title} ({r.topic or '综合'}) — {r.created_at}")

    return f"📝 共 {len(reports)} 份报告：\n" + "\n".join(lines)


@tool
def list_reports() -> str:
    """列出所有研究报告。"""
    return _list_reports_impl()


TOOLS = [generate_report, list_reports]

FUNC_MAP = {
    "generate_report": _generate_report_impl,
    "list_reports": _list_reports_impl,
}
