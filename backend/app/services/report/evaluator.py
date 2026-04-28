"""报告质量校验模块。

用另一个LLM对报告打分：完整性/逻辑清晰度/主题相关性/数据准确性/引用充分性。
"""

import logging
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class QualityReport:
    """报告质量评估结果"""
    total_score: float        # 0-10 总分
    completeness: float       # 完整性 0-10
    logic_clarity: float      # 逻辑清晰度 0-10
    topic_relevance: float    # 主题相关性 0-10
    data_accuracy: float      # 数据准确性 0-10
    citation_sufficiency: float  # 引用充分性 0-10
    conclusion: str           # "通过" / "可改进" / "不通过"
    suggestions: str          # 改进建议


EVAL_PROMPT = """你是报告质量评估专家。请对以下研究报告进行质量打分。

评估维度（每项0-10分）：

1. 完整性：大纲每个章节是否有实质内容？是否有遗漏？
2. 逻辑清晰度：章节组织是否合理？论述是否清晰？过渡是否自然？
3. 主题相关性：内容是否紧扣报告主题？有无偏题？
4. 数据准确性：数据和参数是否与提供的关键信息一致？有无编造？
5. 引用充分性：关键论断是否有来源支撑？是否有无来源的断言？

请严格按以下格式输出（每行一个维度）：
完整性: X/10 — 一句话简评
逻辑清晰度: X/10 — 一句话简评
主题相关性: X/10 — 一句话简评
数据准确性: X/10 — 一句话简评
引用充分性: X/10 — 一句话简评
总分: X/10
结论: 通过/可改进/不通过
改进建议: 如有建议请写出
"""


async def evaluate_report(report_content: str, key_facts_summary: str = "") -> QualityReport:
    """用LLM评估报告质量

    Args:
        report_content: 报告MD全文
        key_facts_summary: 提取的关键信息摘要（用于对比数据准确性）

    Returns:
        QualityReport: 评估结果
    """
    if not settings.llm_configured:
        return QualityReport(
            total_score=5.0, completeness=5, logic_clarity=5, topic_relevance=5,
            data_accuracy=5, citation_sufficiency=5, conclusion="可改进",
            suggestions="LLM未配置，无法自动评估",
        )

    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage

    # 用评估模型（如果有配置），否则用主模型
    eval_model = getattr(settings, 'eval_model_name', '') or settings.llm_model

    llm = ChatOpenAI(
        model=eval_model, api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        streaming=False,
        temperature=0.0, max_tokens=400, timeout=30,
    )

    user_content = f"报告内容：\n{report_content[:4000]}"
    if key_facts_summary:
        user_content += f"\n\n关键信息摘要：\n{key_facts_summary[:1000]}"

    try:
        response = llm.invoke([
            SystemMessage(content=EVAL_PROMPT),
            HumanMessage(content=user_content),
        ])
        return _parse_quality_report(response.content or "")
    except Exception as e:
        logger.error("Report quality evaluation failed: %s", e)
        return QualityReport(
            total_score=0, completeness=0, logic_clarity=0, topic_relevance=0,
            data_accuracy=0, citation_sufficiency=0, conclusion="不通过",
            suggestions=f"评估失败: {str(e)[:100]}",
        )


def _parse_quality_report(text: str) -> QualityReport:
    """解析LLM输出的质量评估"""
    import re

    defaults = {"completeness": 5.0, "logic_clarity": 5.0, "topic_relevance": 5.0,
                "data_accuracy": 5.0, "citation_sufficiency": 5.0}
    scores = dict(defaults)
    conclusion = "可改进"
    suggestions = ""

    for line in text.strip().split("\n"):
        line = line.strip()

        # 解析 "完整性: 8/10 — xxx"
        m = re.match(r'完整性[：:]\s*(\d+(?:\.\d+)?)/10', line)
        if m: scores["completeness"] = float(m.group(1))

        m = re.match(r'逻辑清晰度[：:]\s*(\d+(?:\.\d+)?)/10', line)
        if m: scores["logic_clarity"] = float(m.group(1))

        m = re.match(r'主题相关性[：:]\s*(\d+(?:\.\d+)?)/10', line)
        if m: scores["topic_relevance"] = float(m.group(1))

        m = re.match(r'数据准确性[：:]\s*(\d+(?:\.\d+)?)/10', line)
        if m: scores["data_accuracy"] = float(m.group(1))

        m = re.match(r'引用充分性[：:]\s*(\d+(?:\.\d+)?)/10', line)
        if m: scores["citation_sufficiency"] = float(m.group(1))

        m = re.match(r'总分[：:]\s*(\d+(?:\.\d+)?)/10', line)
        if m: 
            total = float(m.group(1))
        else:
            total = sum(scores.values()) / len(scores)

        m = re.match(r'结论[：:]\s*(.+)', line)
        if m:
            c = m.group(1).strip()
            if "不通过" in c: conclusion = "不通过"
            elif "通过" in c and "不" not in c: conclusion = "通过"
            else: conclusion = "可改进"

        m = re.match(r'改进建议[：:]\s*(.+)', line)
        if m: suggestions = m.group(1).strip()

    return QualityReport(
        total_score=total,
        completeness=scores["completeness"],
        logic_clarity=scores["logic_clarity"],
        topic_relevance=scores["topic_relevance"],
        data_accuracy=scores["data_accuracy"],
        citation_sufficiency=scores["citation_sufficiency"],
        conclusion=conclusion,
        suggestions=suggestions,
    )
