"""报告关键信息提取 + 去重模块。

从素材中提取核心事实/数据/观点，去除废话、重复内容。
"""

import logging
from dataclasses import dataclass, field

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class KeyFact:
    """提取的关键信息"""
    content: str                 # 信息内容
    fact_type: str               # FACT / DATA / OPINION / CONCLUSION
    source: str                  # 来源标识
    source_id: int | None = None # 文章ID
    topic: str = ""              # 所属主题


EXTRACT_PROMPT = """你是信息提取专家。从以下文档中提取关键信息。

要求：
- 只提取核心事实、数据、参数、关键观点
- 不提取：废话、导航文本、广告、重复内容
- 每条信息一行，格式：[类型] 内容
- 类型：FACT(事实) / DATA(数据/参数) / OPINION(观点) / CONCLUSION(结论)

文档标题：{title}
来源：{source}
内容：
{content}
"""


async def extract_key_facts(materials: list[dict]) -> list[KeyFact]:
    """对每篇素材提取关键信息

    Args:
        materials: [{title, source, content, article_id, topic}, ...]

    Returns:
        去重后的 KeyFact 列表
    """
    all_facts = []

    for mat in materials:
        try:
            facts = await _extract_from_single(mat)
            all_facts.extend(facts)
        except Exception as e:
            logger.warning("Key fact extraction failed for '%s': %s", mat.get("title", "?"), e)

    # 去重
    deduped = _deduplicate_facts(all_facts)
    logger.info("Extracted %d facts from %d materials (after dedup: %d)",
                len(all_facts), len(materials), len(deduped))
    return deduped


async def _extract_from_single(material: dict) -> list[KeyFact]:
    """从单篇素材提取关键信息"""
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage

    title = material.get("title", "")
    source = material.get("source", "")
    content = material.get("content", "")
    article_id = material.get("article_id")
    topic = material.get("topic", "")

    if not content or len(content.strip()) < 50:
        return []

    # 截取前3000字，信息密度足够
    content_preview = content[:3000]

    if not settings.llm_configured:
        # 无LLM时降级：直接用前200字作为单条FACT
        return [KeyFact(content=content[:200], fact_type="FACT", source=source,
                        source_id=article_id, topic=topic)]

    llm = ChatOpenAI(
        model=settings.llm_model, api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        streaming=False,
        temperature=0.0, max_tokens=500, timeout=30,
    )

    prompt = EXTRACT_PROMPT.format(title=title, source=source, content=content_preview)
    response = llm.invoke([SystemMessage(content="你是信息提取专家。"), HumanMessage(content=prompt)])

    result_text = response.content or ""
    facts = _parse_facts(result_text, source, article_id, topic)
    return facts


def _parse_facts(text: str, source: str, source_id: int | None, topic: str) -> list[KeyFact]:
    """解析LLM输出的关键信息列表"""
    facts = []
    type_map = {"FACT": "FACT", "DATA": "DATA", "OPINION": "OPINION", "CONCLUSION": "CONCLUSION"}

    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # 尝试解析 [TYPE] content 格式
        import re
        m = re.match(r'\[(FACT|DATA|OPINION|CONCLUSION)\]\s*(.+)', line, re.IGNORECASE)
        if m:
            fact_type = type_map.get(m.group(1).upper(), "FACT")
            content = m.group(2).strip()
        else:
            # 无类型标记，默认FACT
            fact_type = "FACT"
            content = line.lstrip("- •0123456789). ").strip()

        if content and len(content) > 5:
            facts.append(KeyFact(content=content, fact_type=fact_type, source=source,
                                 source_id=source_id, topic=topic))

    return facts


def _deduplicate_facts(facts: list[KeyFact]) -> list[KeyFact]:
    """简单去重：相同前100字的内容只保留一条"""
    seen = set()
    deduped = []
    for fact in facts:
        key = fact.content[:100].strip()
        if key not in seen:
            seen.add(key)
            deduped.append(fact)
    return deduped
