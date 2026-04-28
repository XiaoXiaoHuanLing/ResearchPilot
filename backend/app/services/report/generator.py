"""报告生成主流程 — 4步编排。

Step1: 素材收集 → 关键信息提取+去重
Step2: 大纲生成（人工确认）
Step3: 按大纲逐节生成
Step4: 质量校验

报告是知识库入库的关键路径：凝练后的高质量内容入KB。
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.report.extractor import extract_key_facts, KeyFact
from app.services.report.evaluator import evaluate_report, QualityReport
from app.services.report.formatter import assemble_report, save_report_file, read_report_file

logger = logging.getLogger(__name__)


# ─── 评测接口预留 ───
# 报告生成结果中包含标准化的 eval_meta，供 RAGAS/Agent 评测框架消费


@dataclass
class ReportGenerationState:
    """报告生成状态（跨步骤追踪）"""
    report_id: int = 0
    title: str = ""
    topic: str = ""
    prompt: str = ""            # 用户补充要求
    article_ids: list[int] = field(default_factory=list)

    # Step1 结果
    materials: list[dict] = field(default_factory=list)
    key_facts: list[KeyFact] = field(default_factory=list)

    # Step2 结果
    outline: str = ""

    # Step3 结果
    sections: dict[str, str] = field(default_factory=dict)
    full_content: str = ""
    file_path: str = ""

    # Step4 结果
    quality: QualityReport | None = None

    # 状态
    status: str = "draft"  # draft / outline_ready / generating / ready / failed

    # ⭐ 评测接口预留
    eval_meta: dict = field(default_factory=dict)


# --- Step 1: 素材收集 + 关键信息提取 ---

async def step1_collect_and_extract(
    article_ids: list[int] | None = None,
) -> tuple[list[dict], list[KeyFact]]:
    """收集素材并提取关键信息

    Returns:
        (materials, key_facts)
    """
    from app.db.session import SessionLocal
    from app.db.models import ArticleModel

    with SessionLocal() as db:
        if article_ids:
            articles = db.query(ArticleModel).filter(ArticleModel.id.in_(article_ids)).all()
        else:
            articles = db.query(ArticleModel).filter(ArticleModel.bookmarked == True).all()  # noqa

    if not articles:
        return [], []

    # 准备素材
    materials = []
    for a in articles:
        materials.append({
            "title": a.title,
            "source": a.source,
            "content": a.content or a.summary,
            "article_id": a.id,
            "topic": a.topic,
        })

    # 提取关键信息
    key_facts = await extract_key_facts(materials)

    return materials, key_facts


# --- Step 2: 大纲生成 ---

OUTLINE_PROMPT = """你是报告结构规划专家。根据用户主题和提取的关键信息，生成报告大纲。

用户主题：{topic}
用户补充要求：{prompt}

关键信息摘要：
{facts_summary}

请生成报告大纲，要求：
1. 章节划分要紧扣用户主题
2. 每个章节标注预期覆盖的关键信息点（括号内）
3. 章节之间逻辑清晰、层次分明
4. 输出为 Markdown 格式
5. 只输出大纲，不输出正文内容
"""


async def step2_generate_outline(
    topic: str,
    key_facts: list[KeyFact],
    prompt: str = "",
) -> str:
    """生成报告大纲

    Args:
        topic: 用户主题
        key_facts: 提取的关键信息
        prompt: 用户补充要求

    Returns:
        Markdown格式的大纲
    """
    if not settings.llm_configured:
        # 无LLM时生成简单大纲
        return f"# {topic} 研究报告\n\n## 1. 综合分析\n\n## 2. 关键发现\n\n## 3. 总结与展望"

    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage

    facts_summary = "\n".join(
        f"- [{f.fact_type}] {f.content}" for f in key_facts[:40]
    )

    user_content = OUTLINE_PROMPT.format(
        topic=topic, prompt=prompt or "无", facts_summary=facts_summary
    )

    llm = ChatOpenAI(
        model=settings.llm_model, api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        streaming=True,
        temperature=0.4, max_tokens=1000, timeout=60,
    )

    response = llm.invoke([
        SystemMessage(content="你是报告结构规划专家。"),
        HumanMessage(content=user_content),
    ])

    return response.content or ""


# --- Step 3: 逐节生成 ---

SECTION_PROMPT = """你是专业的研究报告撰写者。请根据大纲和关键信息生成该章节的内容。

报告主题：{report_topic}
当前章节：{section_title}
章节预期覆盖：{section_expectation}

该章节相关的关键信息：
{relevant_facts}

要求：
1. 内容必须基于提供的关键信息，不编造数据
2. 使用Markdown格式（表格、列表、粗体等）
3. 如有参数对比，用表格呈现
4. 关键数据加粗标注
5. 用中文撰写，专业简洁
"""


async def step3_generate_sections(
    outline: str,
    key_facts: list[KeyFact],
    topic: str,
) -> dict[str, str]:
    """按大纲逐章节生成内容

    Returns:
        {章节标题: 章节内容MD}
    """
    import re

    sections = {}
    section_list = _parse_outline_sections(outline)

    if not settings.llm_configured:
        # 无LLM降级
        for s in section_list:
            sections[s["title"]] = f"## {s['title']}\n\n（LLM未配置，无法生成内容）"
        return sections

    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage

    llm = ChatOpenAI(
        model=settings.llm_model, api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        streaming=True,
        temperature=0.3, max_tokens=2000, timeout=90,
    )

    for section in section_list:
        title = section["title"]
        expectation = section.get("expectation", "")

        # 匹配关键信息
        relevant_facts = _match_facts_to_section(key_facts, title, expectation)
        facts_text = "\n".join(f"- [{f.fact_type}] {f.content}" for f in relevant_facts[:20])

        if not facts_text:
            facts_text = "（无直接相关的关键信息，请基于通用知识谨慎补充）"

        user_content = SECTION_PROMPT.format(
            report_topic=topic, section_title=title,
            section_expectation=expectation, relevant_facts=facts_text,
        )

        try:
            response = llm.invoke([
                SystemMessage(content="你是专业的研究报告撰写者。"),
                HumanMessage(content=user_content),
            ])
            sections[title] = response.content or f"## {title}\n\n（生成失败）"
            logger.info("Generated section: %s (%d chars)", title, len(sections[title]))
        except Exception as e:
            logger.error("Section generation failed for '%s': %s", title, e)
            sections[title] = f"## {title}\n\n（生成失败：{str(e)[:100]}）"

    return sections


def _parse_outline_sections(outline: str) -> list[dict]:
    """解析大纲为章节列表"""
    import re
    sections = []
    for line in outline.strip().split("\n"):
        line = line.strip()
        m = re.match(r'^(#{2,4})\s+(.+)', line)
        if m:
            title = m.group(2).strip()
            # 提取预期覆盖（括号内容）
            expectation = ""
            em = re.search(r'[（(]预期[：:]\s*(.+?)[）)]', title)
            if em:
                expectation = em.group(1).strip()
                title = re.sub(r'[（(]预期[：:].+?[）)]', '', title).strip()

            sections.append({"title": title, "expectation": expectation})
    return sections


def _match_facts_to_section(
    key_facts: list[KeyFact], section_title: str, expectation: str
) -> list[KeyFact]:
    """将关键信息匹配到章节（简单关键词匹配）"""
    import re
    # 从章节标题和预期中提取关键词
    search_text = f"{section_title} {expectation}"
    keywords = [w for w in re.split(r"[，。、\s,?\s]+", search_text) if len(w) >= 2]

    relevant = []
    for fact in key_facts:
        # 任意关键词匹配则相关
        if any(kw in fact.content for kw in keywords):
            relevant.append(fact)
        elif any(kw in fact.topic for kw in keywords):
            relevant.append(fact)

    # 如果没有直接匹配的，返回所有（让LLM自己筛选）
    if not relevant:
        return key_facts[:20]

    return relevant


# --- Step 4: 质量校验 ---

async def step4_evaluate(
    full_content: str,
    key_facts: list[KeyFact],
) -> QualityReport:
    """评估报告质量"""
    facts_summary = "\n".join(f"- [{f.fact_type}] {f.content[:100]}" for f in key_facts[:20])
    return await evaluate_report(full_content, facts_summary)


# --- 主流程 ---

async def start_report_generation(
    article_ids: list[int] | None = None,
    title: str | None = None,
    prompt: str | None = None,
) -> dict:
    """启动报告生成（Step1+Step2），返回大纲等待确认

    Returns:
        {report_db_id, outline, key_facts_count, status: "outline_ready", eval_meta}
    """
    # Step1
    materials, key_facts = await step1_collect_and_extract(article_ids)

    if not materials:
        return {"error": "没有可用的素材，请先收藏一些资讯", "status": "failed"}

    # 确定主题
    topic_names = sorted(set(m.get("topic", "") for m in materials if m.get("topic")))
    topic = title or (topic_names[0] if len(topic_names) == 1 else "综合研究")

    # Step2: 生成大纲
    outline = await step2_generate_outline(topic, key_facts, prompt or "")

    # 暂存到DB（status=draft，等待用户确认大纲）
    from app.db.session import SessionLocal
    from app.db.models import ReportModel
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    # 临时保存大纲到文件
    outline_text = outline  # 保存以便后续确认时读取

    with SessionLocal() as db:
        report = ReportModel(
            title=title or f"{topic} 研究报告",
            topic=topic,
            created_at=now,
            updated_at=now,
            # V2: 不存全文，只存基本信息
            file_path="",
            article_ids_json=json.dumps(article_ids or []),
            quality_score=0.0,
            quality_detail="",
            status="draft",
            indexed=False,
            kb_id=None,
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        report_id = report.id

    # 保存大纲到临时文件
    outline_dir = Path(settings.storage_base_dir) / "reports"
    outline_dir.mkdir(parents=True, exist_ok=True)
    outline_path = outline_dir / f"{report_id}_outline.md"
    outline_path.write_text(outline, encoding="utf-8")

    # ⭐ 评测接口预留
    eval_meta = {
        "report_id": report_id,
        "step": "outline_generated",
        "materials_count": len(materials),
        "key_facts_count": len(key_facts),
        "fact_types": {
            ft: sum(1 for f in key_facts if f.fact_type == ft)
            for ft in ["FACT", "DATA", "OPINION", "CONCLUSION"]
        },
    }

    return {
        "report_id": report_id,
        "outline": outline,
        "key_facts_count": len(key_facts),
        "materials_count": len(materials),
        "status": "outline_ready",
        "eval_meta": eval_meta,
    }


async def confirm_outline_and_generate(
    report_id: int,
    action: str = "confirm",
    outline: str | None = None,
    regenerate_hint: str | None = None,
) -> dict:
    """确认大纲后生成报告（Step3+Step4）

    Args:
        report_id: 报告ID
        action: "confirm" / "edit_confirm" / "regenerate"
        outline: 编辑后的大纲（action=edit_confirm时）
        regenerate_hint: 重新生成提示
    """
    from app.db.session import SessionLocal
    from app.db.models import ReportModel
    from datetime import datetime, timezone

    with SessionLocal() as db:
        report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
        if not report:
            return {"error": "报告不存在", "status": "failed"}

        article_ids = json.loads(report.article_ids_json or "[]")
        topic = report.topic
        prompt = getattr(report, 'prompt', '') or ""

    # 处理action
    if action == "regenerate":
        # 重新生成大纲
        materials, key_facts = await step1_collect_and_extract(article_ids or None)
        outline_text = await step2_generate_outline(topic, key_facts, regenerate_hint or prompt)
        # 保存新大纲
        outline_path = Path(settings.storage_base_dir) / "reports" / f"{report_id}_outline.md"
        outline_path.write_text(outline_text, encoding="utf-8")
        return {
            "report_id": report_id,
            "outline": outline_text,
            "status": "outline_ready",
        }

    # 确认大纲（可能是编辑后的版本）
    if outline:
        outline_text = outline
    else:
        outline_path = Path(settings.storage_base_dir) / "reports" / f"{report_id}_outline.md"
        outline_text = outline_path.read_text(encoding="utf-8") if outline_path.exists() else ""

    if not outline_text:
        return {"error": "大纲为空", "status": "failed"}

    # Step1: 重新提取关键信息
    materials, key_facts = await step1_collect_and_extract(article_ids or None)

    # Step3: 逐节生成
    sections = await step3_generate_sections(outline_text, key_facts, topic)

    # 拼装完整报告
    full_content = assemble_report(outline_text, sections, key_facts)

    # Step4: 质量校验
    quality = await step4_evaluate(full_content, key_facts)

    # 保存MD文件
    file_path = save_report_file(report_id, topic, full_content)

    # 更新DB
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with SessionLocal() as db:
        report = db.query(ReportModel).filter(ReportModel.id == report_id).first()
        if report:
            report.file_path = file_path
            report.status = "ready"
            report.quality_score = quality.total_score
            report.quality_detail = json.dumps({
                "completeness": quality.completeness,
                "logic_clarity": quality.logic_clarity,
                "topic_relevance": quality.topic_relevance,
                "data_accuracy": quality.data_accuracy,
                "citation_sufficiency": quality.citation_sufficiency,
                "conclusion": quality.conclusion,
                "suggestions": quality.suggestions,
            }, ensure_ascii=False)
            report.updated_at = now
            db.commit()

    # ⭐ 评测接口预留
    eval_meta = {
        "report_id": report_id,
        "step": "generation_complete",
        "quality_score": quality.total_score,
        "quality_detail": {
            "completeness": quality.completeness,
            "logic_clarity": quality.logic_clarity,
            "topic_relevance": quality.topic_relevance,
            "data_accuracy": quality.data_accuracy,
            "citation_sufficiency": quality.citation_sufficiency,
        },
        "key_facts_count": len(key_facts),
        "sections_count": len(sections),
        "content_length": len(full_content),
    }

    return {
        "report_id": report_id,
        "status": "ready",
        "quality_score": quality.total_score,
        "quality_conclusion": quality.conclusion,
        "quality_suggestions": quality.suggestions,
        "file_path": file_path,
        "eval_meta": eval_meta,
    }
