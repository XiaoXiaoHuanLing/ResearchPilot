"""咨询内容格式化模块。

将 readability 提取的粗文本转为结构化 Markdown。
优先保留原页面格式(标题/列表/表格)，降级时智能分段。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def format_content(raw_html: str, extracted_text: str) -> str:
    """将原始HTML转为结构化Markdown。

    策略:
    1. 优先路径: readability正文HTML → markdownify → MD（保留结构）
    2. 降级路径: 纯文本 → 智能分段格式化

    Args:
        raw_html: 原始网页HTML
        extracted_text: readability提取的纯文本

    Returns:
        格式化后的Markdown文本
    """
    # 方案1: HTML → Markdown
    formatted = _html_to_markdown(raw_html)

    if formatted and len(formatted.strip()) > len(extracted_text.strip()) * 0.5:
        # 转换成功，长度合理
        return formatted.strip()

    # 方案2: 纯文本智能分段
    logger.info("HTML→MD conversion insufficient, falling back to text formatting")
    return smart_paragraph_split(extracted_text)


def _html_to_markdown(raw_html: str) -> str:
    """用 markdownify 将正文HTML转为Markdown"""
    try:
        from markdownify import markdownify as md
        from readability import Document

        doc = Document(raw_html)
        summary_html = doc.summary(html_partial=True)

        # 转换：保留标题层级(ATX风格)，去除图片/脚本/样式
        formatted = md(
            summary_html,
            heading_style="ATX",
            strip=["img", "script", "style", "nav", "footer", "header"],
            convert=["table", "p", "h1", "h2", "h3", "h4", "h5", "h6",
                     "ul", "ol", "li", "strong", "em", "a", "blockquote", "pre", "code"],
        )

        # 清理：去除连续空行（最多保留2行）
        import re
        formatted = re.sub(r'\n{3,}', '\n\n', formatted)

        return formatted

    except ImportError:
        logger.warning("markdownify not installed, skipping HTML→MD conversion")
        return ""
    except Exception as e:
        logger.warning("HTML→MD conversion failed: %s", e)
        return ""


def smart_paragraph_split(text: str) -> str:
    """将纯文本智能分段格式化为Markdown。

    检测列表模式、表格模式，保留格式；
    其余按段落组织。
    """
    if not text or not text.strip():
        return ""

    lines = text.split("\n")
    formatted_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            formatted_lines.append("")  # 保留空行作为段落分隔
            continue

        # 检测列表模式: "1. xxx" / "- xxx" / "• xxx" / "◆ xxx"
        if _is_list_item(stripped):
            formatted_lines.append(f"- {_clean_list_item(stripped)}")
            continue

        # 检测表格模式: "参数 | 值" (至少2个|)
        if stripped.count("|") >= 2:
            formatted_lines.append(stripped)
            continue

        # 检测标题模式: 全大写短行 / 以"第X章"/"X、"开头
        if _looks_like_heading(stripped):
            formatted_lines.append(f"## {stripped}")
            formatted_lines.append("")
            continue

        # 普通段落
        formatted_lines.append(stripped)

    result = "\n".join(formatted_lines)

    # 清理连续空行
    import re
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result.strip()


def _is_list_item(line: str) -> bool:
    """检测是否为列表项"""
    import re
    patterns = [
        r'^\d+[.、)]\s',     # "1. xxx" / "1、xxx" / "1) xxx"
        r'^[-*•◆◇]\s',       # "- xxx" / "* xxx" / "• xxx"
        r'^[①②③④⑤⑥⑦⑧⑨⑩]\s',  # 圈号列表
    ]
    return any(re.match(p, line) for p in patterns)


def _clean_list_item(line: str) -> str:
    """统一列表标记为 Markdown 格式"""
    import re
    # 去除原有标记，内容保留
    line = re.sub(r'^\d+[.、)]\s', '', line)
    line = re.sub(r'^[-*•◆◇]\s', '', line)
    line = re.sub(r'^[①②③④⑤⑥⑦⑧⑨⑩]\s', '', line)
    return line


def _looks_like_heading(line: str) -> bool:
    """检测短行是否像标题"""
    if len(line) > 60:
        return False  # 标题通常较短
    import re
    patterns = [
        r'^第[一二三四五六七八九十\d]+[章节篇部]',  # "第一章"
        r'^[一二三四五六七八九十]+[、.]\s',          # "一、xxx"
        r'^[A-Z][A-Z\s]{2,}$',                      # 全大写英文
    ]
    return any(re.match(p, line) for p in patterns)
