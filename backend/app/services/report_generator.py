"""Report generation service using LangGraph for stateful workflow.

Workflow:
1. Collect: gather bookmarked articles for the topic
2. Analyze: use LLM to analyze and structure the content
3. Generate: produce the final report with sources
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
    articles: list[dict]
    analysis: str
    report_title: str
    report_summary: str
    source_list: str


def _get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.openai_api_key or None,
        base_url=settings.openai_base_url or None,
        temperature=0.4,
        max_tokens=3000,
    )


def collect_articles(state: ReportState) -> dict:
    """Step 1: Articles are already passed in, just validate."""
    if not state["articles"]:
        return {"analysis": "没有可用的收藏资讯来生成报告。"}
    return {"analysis": ""}


def analyze_content(state: ReportState) -> dict:
    """Step 2: Use LLM to analyze collected articles."""
    if not state["articles"]:
        return {"analysis": "无内容可分析。", "report_title": "空报告", "report_summary": "没有收藏的资讯可供分析。"}

    if not settings.openai_api_key:
        # Fallback: no LLM, build report from article summaries directly
        articles = state["articles"]
        source_set = sorted(set(a["source"] for a in articles))
        summary_parts = [f"- [{a['source']}] {a['title']}：{a['summary']}" for a in articles]
        report_title = state["title"] or f"{state['topic'] or '综合'} 研究报告"
        report_summary = (
            f"本报告基于 {len(articles)} 篇收藏资讯整理生成。\n"
            f"信息来源：{', '.join(source_set)}\n\n"
            + "\n".join(summary_parts)
        )
        return {
            "report_title": report_title,
            "report_summary": report_summary,
            "source_list": ", ".join(source_set),
        }

    # Real LLM analysis
    llm = _get_llm()
    articles_text = "\n\n".join(
        f"[来源{i+1}: {a['source']}] {a['title']}\n{a.get('content', a['summary'])}"
        for i, a in enumerate(state["articles"])
    )

    prompt = (
        f"你是一位专业的开源情报分析师。请基于以下收集到的资讯，生成一份结构化研究报告。\n\n"
        f"专题：{state['topic'] or '综合'}\n\n"
        f"收集到的资讯：\n{articles_text}\n\n"
        f"请输出：\n"
        f"1. 报告标题\n"
        f"2. 核心发现（3-5条要点）\n"
        f"3. 详细分析（按主题分类整理）\n"
        f"4. 趋势判断与展望\n"
        f"5. 信息来源列表"
    )

    try:
        response = llm.invoke(prompt)
        analysis = response.content
    except Exception as e:
        logger.error("LLM analysis failed: %s", e)
        # Fallback to simple aggregation
        articles = state["articles"]
        source_set = sorted(set(a["source"] for a in articles))
        summary_parts = [f"- [{a['source']}] {a['title']}：{a['summary']}" for a in articles]
        analysis = f"LLM 分析失败，以下为基础整理：\n\n" + "\n".join(summary_parts)

    report_title = state["title"] or f"{state['topic'] or '综合'} 研究报告"
    return {
        "report_title": report_title,
        "report_summary": analysis,
        "source_list": ", ".join(sorted(set(a["source"] for a in state["articles"]))),
    }


def generate_report(state: ReportState) -> dict:
    """Step 3: Finalize the report structure."""
    return {
        "report_title": state.get("report_title", "研究报告"),
        "report_summary": state.get("report_summary", ""),
    }


def build_report_graph() -> StateGraph:
    """Build the LangGraph workflow for report generation."""
    graph = StateGraph(ReportState)

    graph.add_node("collect", collect_articles)
    graph.add_node("analyze", analyze_content)
    graph.add_node("generate", generate_report)

    graph.set_entry_point("collect")
    graph.add_edge("collect", "analyze")
    graph.add_edge("analyze", "generate")
    graph.add_edge("generate", END)

    return graph.compile()


async def generate_report_with_langgraph(
    articles: list[dict],
    topic: str | None = None,
    title: str | None = None,
) -> dict:
    """Generate a report using the LangGraph workflow.

    Args:
        articles: List of article dicts with keys: title, source, summary, content
        topic: Optional topic filter
        title: Optional report title

    Returns:
        Dict with report_title and report_summary
    """
    app = build_report_graph()

    result = await app.ainvoke({
        "topic": topic,
        "title": title,
        "articles": articles,
        "analysis": "",
        "report_title": "",
        "report_summary": "",
        "source_list": "",
    })

    return {
        "title": result.get("report_title", "研究报告"),
        "summary": result.get("report_summary", ""),
    }
