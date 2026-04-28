"""报告管理API — 4步生成 + 入KB + 导出。"""

import json
import logging
from io import BytesIO
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import ReportModel
from app.db.session import get_db, SessionLocal
from app.schemas.report import ReportRead, ReportGenerateRequest, OutlineConfirmRequest

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Helpers ────────────────────────────────────────────────────

def _get_report_content(report: ReportModel) -> str:
    """从文件读取报告MD内容"""
    if not report.file_path:
        return ""
    from app.services.report.formatter import read_report_file
    return read_report_file(report.file_path)


# ─── CRUD ──────────────────────────────────────────────────────

@router.get("", response_model=list[ReportRead])
def list_reports(db: Session = Depends(get_db)):
    return db.query(ReportModel).order_by(ReportModel.id.desc()).all()


@router.get("/{report_id}", response_model=ReportRead)
def get_report(report_id: int, db: Session = Depends(get_db)):
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.get("/{report_id}/content")
def get_report_content(report_id: int, db: Session = Depends(get_db)):
    """获取报告MD内容（从文件读取）"""
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    content = _get_report_content(report)
    if not content:
        raise HTTPException(status_code=404, detail="Report file not found")

    return {"report_id": report_id, "content": content, "title": report.title}


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(report_id: int, db: Session = Depends(get_db)):
    """删除报告（含文件）"""
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    # 删除文件
    if report.file_path:
        try:
            from pathlib import Path
            from app.core.config import settings
            file_path = Path(settings.storage_base_dir) / report.file_path
            if file_path.exists():
                file_path.unlink()
        except Exception as e:
            logger.warning("Failed to delete report file: %s", e)

    db.delete(report)
    db.commit()


# ─── 4步生成流程 ───────────────────────────────────────────────

@router.post("/start")
async def start_report_generation(payload: ReportGenerateRequest):
    """Step1+2: 启动报告生成，提取关键信息+生成大纲，返回大纲等待确认"""
    from app.services.report.generator import start_report_generation as _start

    try:
        result = await _start(
            article_ids=payload.article_ids,
            title=payload.title,
            prompt=payload.prompt,
        )
    except Exception as e:
        err_msg = str(e)
        if "AllocationQuota" in err_msg or "free tier" in err_msg:
            raise HTTPException(status_code=503, detail="LLM免费额度已耗尽，请在控制台关闭'仅使用免费额度'或切换付费模式")
        raise HTTPException(status_code=500, detail=f"报告生成失败: {err_msg[:200]}")

    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    return result


@router.post("/{report_id}/confirm-outline")
async def confirm_outline(report_id: int, payload: OutlineConfirmRequest):
    """Step2确认: 确认/编辑大纲后生成报告（Step3+4）"""
    from app.services.report.generator import confirm_outline_and_generate as _confirm

    result = await _confirm(
        report_id=report_id,
        action=payload.action,
        outline=payload.outline,
        regenerate_hint=payload.regenerate_hint,
    )

    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    return result


@router.get("/{report_id}/status")
def get_report_status(report_id: int, db: Session = Depends(get_db)):
    """查询报告生成状态+质量评分"""
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    quality_detail = {}
    if report.quality_detail:
        try:
            quality_detail = json.loads(report.quality_detail)
        except Exception:
            pass

    return {
        "report_id": report_id,
        "status": report.status,
        "quality_score": report.quality_score,
        "quality_detail": quality_detail,
    }


# ─── 入KB ──────────────────────────────────────────────────────

class IndexToKbRequest(BaseModel):
    kb_id: int


@router.post("/{report_id}/index-to-kb")
async def index_report_to_kb(report_id: int, payload: IndexToKbRequest):
    """将报告入库到指定知识库 — 走 indexer 文件入库路径"""
    from app.db.models import KnowledgeBaseModel, KbDocumentModel

    with SessionLocal() as db:
        report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        if report.status != "ready":
            raise HTTPException(status_code=400, detail="报告尚未生成完成")
        if not report.file_path:
            raise HTTPException(status_code=400, detail="报告文件不存在")

        kb = db.query(KnowledgeBaseModel).filter(KnowledgeBaseModel.id == payload.kb_id).first()
        if not kb:
            raise HTTPException(status_code=404, detail="知识库不存在")

        # 检查是否已入库到该KB
        existing = db.query(KbDocumentModel).filter(
            KbDocumentModel.source_type == "report",
            KbDocumentModel.report_id == report_id,
            KbDocumentModel.kb_id == payload.kb_id,
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"该报告已入库到「{kb.name}」(文档ID: {existing.id})")

        # 创建 KbDocument 记录
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        doc = KbDocumentModel(
            kb_id=payload.kb_id,
            title=report.title,
            file_path=report.file_path,  # 报告文件已在 reports/ 目录
            file_type="md",
            source_type="report",
            source=f"报告入库: {report.title}",
            report_id=report_id,
            index_status="pending",
            created_at=now,
        )
        db.add(doc)

        # 标记报告已入库
        report.indexed = True
        report.kb_id = payload.kb_id
        db.commit()
        db.refresh(doc)

    # 加入异步入库队列（走 indexer.index_document 文件路径）
    from app.services.knowledge.manager import enqueue_index_task
    await enqueue_index_task(doc.id)

    return {
        "indexed": True,
        "doc_id": doc.id,
        "kb_id": payload.kb_id,
        "message": f"报告已提交入库到「{kb.name}」，正在索引...",
    }


# ─── 导出 ──────────────────────────────────────────────────────

@router.get("/{report_id}/export/markdown")
def export_report_markdown(report_id: int, db: Session = Depends(get_db)):
    """导出MD文件"""
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    content = _get_report_content(report)
    if not content:
        raise HTTPException(status_code=404, detail="Report file not found")

    buf = BytesIO(content.encode("utf-8"))
    filename = f"{report.title}.md"

    return StreamingResponse(
        buf,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/{report_id}/export/pdf")
def export_report_pdf(report_id: int, db: Session = Depends(get_db)):
    """导出PDF（降级为HTML）"""
    report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    content = _get_report_content(report)
    if not content:
        raise HTTPException(status_code=404, detail="Report file not found")

    # 简单HTML包装
    html_content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: "Microsoft YaHei", sans-serif; margin: 40px; line-height: 1.8; color: #333; }}
  h1 {{ color: #1a1a1a; border-bottom: 2px solid #06b6d4; padding-bottom: 8px; }}
  h2 {{ color: #333; margin-top: 24px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
  th {{ background: #f5f5f5; }}
  blockquote {{ color: #666; border-left: 3px solid #06b6d4; padding-left: 12px; margin: 16px 0; }}
</style>
</head><body>
<pre style="white-space: pre-wrap; font-family: inherit;">{content}</pre>
</body></html>"""

    try:
        import weasyprint
        buf = BytesIO()
        weasyprint.HTML(string=html_content).write_pdf(buf)
        buf.seek(0)
        filename = f"{report.title}.pdf"
        return StreamingResponse(
            buf, media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )
    except ImportError:
        buf = BytesIO(html_content.encode("utf-8"))
        filename = f"{report.title}.html"
        return StreamingResponse(
            buf, media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )
