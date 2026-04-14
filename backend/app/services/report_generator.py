"""Report generation service using LangGraph + LLM.

Workflow (simplified for reliability):
1. Collect: validate bookmarked articles exist
2. Analyze + Generate: single LLM call for efficiency (3-call version was too slow)
3. Fallback: structured aggregation if LLM fails
"""

import logging
from typing import TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from app.core.config import settings

logger = logging.getLogger(__name__)


class ReportState(TypedDict):
    topic: str | None
    title: str | None
    prompt: str | None  # User instructions for report style
    articles: list[dict]
    report_title: str
    report_summary: str
    report_content: str


def collect_articles(state: ReportState) -> dict:
    """Step 1: Validate that we have articles."""
    if not state["articles"]:
        return {"report_title": "空报告", "report_summary": "没有收藏的资讯可供分析。", "report_content": ""}
    return {}


def generate_report(state: ReportState) -> dict:
    """Step 2: Generate report with LLM (or fallback).

    LLM chain: primary → optional fallback model → aggregation fallback.
    Primary model may return empty content, so we retry with fallback if configured.
    """
    articles = state["articles"]
    if not articles:
        return {"report_title": "空报告", "report_summary": "无数据", "report_content": ""}

    report_title = state.get("title") or f"{state['topic'] or '综合'} 研究报告"

    # Build the prompt (shared across LLM attempts)
    articles_text = "\n\n".join(
        f"[来源{i+1}: {a['source']}] {a['title']}\n{a.get('content', a['summary'])[:1500]}"
        for i, a in enumerate(articles)
    )

    prompt = (
        f"你是一位专业的开源情报分析师。请基于以下资讯生成一份结构化研究报告。\n\n"
        f"专题：{state['topic'] or '综合'}\n\n"
    )

    if state.get("prompt"):
        prompt += f"用户要求：{state['prompt']}\n\n"

    prompt += (
        f"资讯：\n{articles_text}\n\n"
        f"请输出以下结构：\n"
        f"## 核心发现\n（3-5条要点，编号列表）\n\n"
        f"## 分类分析\n（按领域分类整理关键信息）\n\n"
        f"## 趋势与展望\n（短期趋势和关键不确定性）\n\n"
        f"## 信息来源\n（列出所有来源名称）"
    )

    # --- LLM attempt chain ---
    llm_configs = []

    # Primary: DashScope LLM
    if settings.llm_configured:
        llm_configs.append({
            "model": settings.llm_model,
            "api_key": settings.llm_api_key,
            "base_url": settings.llm_base_url,
            "label": "primary",
        })

    # Optional fallback model (if DASHSCOPE_MODEL_NAME_FALLBACK is configured)
    fallback_model = settings.dashscope_model_name_fallback
    if fallback_model and settings.llm_configured:
        llm_configs.append({
            "model": fallback_model,
            "api_key": settings.llm_api_key,
            "base_url": settings.llm_base_url,
            "label": "fallback",
        })

    for config in llm_configs:
        try:
            llm = ChatOpenAI(
                model=config["model"],
                api_key=config["api_key"],
                base_url=config["base_url"],
                streaming=True,
                temperature=0.4,
                max_tokens=3000,
                timeout=120,
            )

            logger.info("Report LLM attempt: %s (model=%s)", config["label"], config["model"])
            response = llm.invoke(prompt)
            content = response.content or ""

            if not content.strip():
                logger.warning("Report LLM %s returned empty content, trying next", config["label"])
                continue

            summary = content[:500] if len(content) > 500 else content

            return {
                "report_title": report_title,
                "report_summary": f"本报告基于 {len(articles)} 篇收藏资讯整理生成。\n\n{summary}",
                "report_content": content,
            }

        except Exception as e:
            logger.error("Report LLM %s failed: %s", config["label"], e)
            continue

    # Fallback: structured aggregation without LLM
    return _fallback_report(state)


def _fallback_report(state: ReportState) -> dict:
    """Fallback: build report from article summaries directly."""
    articles = state["articles"]
    source_set = sorted(set(a["source"] for a in articles))
    report_title = state.get("title") or f"{state['topic'] or '综合'} 研究报告"

    # Core findings
    findings = "\n".join(f"{i+1}. [{a['source']}] {a['title']}" for i, a in enumerate(articles[:8]))

    # Detailed analysis
    analysis = "\n\n".join(
        f"### {a['title']}\n来源：{a['source']}\n\n{a['summary']}" for a in articles
    )

    content = (
        f"## 核心发现\n\n{findings}\n\n"
        f"---\n\n## 分类分析\n\n{analysis}\n\n"
        f"---\n\n## 趋势与展望\n\n（需要配置 LLM API 生成趋势分析）\n\n"
        f"---\n\n## 信息来源\n\n" + "\n".join(f"- {s}" for s in source_set)
    )

    summary = f"本报告基于 {len(articles)} 篇收藏资讯整理生成（LLM 未配置，为基础聚合版）。"

    return {
        "report_title": report_title,
        "report_summary": summary,
        "report_content": content,
    }


def build_report_graph() -> StateGraph:
    """Build the LangGraph workflow: collect → generate → END."""
    graph = StateGraph(ReportState)
    graph.add_node("collect", collect_articles)
    graph.add_node("generate", generate_report)
    graph.set_entry_point("collect")
    graph.add_edge("collect", "generate")
    graph.add_edge("generate", END)
    return graph.compile()


async def generate_report_with_langgraph(
    articles: list[dict],
    topic: str | None = None,
    title: str | None = None,
    prompt: str | None = None,
) -> dict:
    """Generate a report using LangGraph workflow."""
    app = build_report_graph()

    result = await app.ainvoke({
        "topic": topic,
        "title": title,
        "prompt": prompt,
        "articles": articles,
        "report_title": "",
        "report_summary": "",
        "report_content": "",
    })

    return {
        "title": result.get("report_title", "研究报告"),
        "summary": result.get("report_summary", ""),
        "full_content": result.get("report_content", ""),
    }
