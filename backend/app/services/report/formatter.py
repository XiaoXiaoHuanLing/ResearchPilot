"""报告MD拼装 + 文件IO模块。

负责将各章节内容拼装为完整MD报告，保存到文件，读取文件内容。
"""

import logging
from pathlib import Path
from datetime import datetime, timezone

from app.core.config import settings

logger = logging.getLogger(__name__)


def assemble_report(outline: str, sections: dict[str, str], key_facts: list) -> str:
    """拼装完整MD报告

    Args:
        outline: 确认后的大纲MD文本
        sections: {章节标题: 章节内容MD}
        key_facts: 引用的关键信息列表

    Returns:
        完整MD文本
    """
    parts = []

    # 标题行（从大纲首行提取）
    first_line = outline.strip().split("\n")[0] if outline.strip() else "# 研究报告"
    parts.append(first_line)
    parts.append("")

    # 报告元信息
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts.append(f"> 生成时间：{now}")
    parts.append(f"> 素材数量：{len(key_facts)} 条关键信息")
    parts.append("")

    # 目录（从大纲生成，可选）
    toc = _generate_toc(outline)
    if toc:
        parts.append("## 目录")
        parts.append(toc)
        parts.append("")

    # 各章节
    for section_title, section_content in sections.items():
        parts.append(section_content)
        parts.append("")

    # 引用来源汇总
    seen_sources = set()
    for fact in key_facts:
        src = getattr(fact, 'source', '') if hasattr(fact, 'source') else fact.get('source', '')
        if src and src not in seen_sources:
            seen_sources.add(src)

    if seen_sources:
        parts.append("## 引用来源")
        parts.append("")
        for src in sorted(seen_sources):
            parts.append(f"- {src}")
        parts.append("")

    return "\n".join(parts)


def _generate_toc(outline: str) -> str:
    """从大纲生成目录"""
    import re
    toc_lines = []
    for line in outline.strip().split("\n"):
        line = line.strip()
        m = re.match(r'^(#{2,4})\s+(.+)', line)
        if m:
            level = len(m.group(1)) - 1  # ## → 1, ### → 2
            title = m.group(2).strip()
            # 去掉"（预期：...）"部分
            title = re.sub(r'[（(]预期[：:].+?[）)]', '', title).strip()
            indent = "  " * (level - 1)
            anchor = title.lower().replace(" ", "-")
            toc_lines.append(f"{indent}- [{title}](#{anchor})")

    return "\n".join(toc_lines) if toc_lines else ""


def save_report_file(report_id: int, title: str, content: str) -> str:
    """保存报告MD到文件

    Args:
        report_id: 报告ID
        title: 报告标题
        content: MD内容

    Returns:
        相对文件路径（相对于storage_base_dir）
    """
    report_dir = Path(settings.storage_base_dir) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    # 文件名：{id}_{安全标题}.md
    safe_title = _safe_filename(title)
    filename = f"{report_id}_{safe_title}.md"
    file_path = report_dir / filename

    file_path.write_text(content, encoding="utf-8")

    # 返回相对路径
    rel_path = str(file_path.relative_to(settings.storage_base_dir)).replace("\\", "/")
    logger.info("Report saved: %s (%d chars)", rel_path, len(content))
    return rel_path


def read_report_file(file_path: str) -> str:
    """从文件读取报告MD内容

    Args:
        file_path: 相对路径或绝对路径

    Returns:
        MD文本内容
    """
    full_path = Path(settings.storage_base_dir) / file_path
    if not full_path.exists():
        # 尝试绝对路径
        full_path = Path(file_path)

    if not full_path.exists():
        logger.warning("Report file not found: %s", file_path)
        return ""

    return full_path.read_text(encoding="utf-8")


def _safe_filename(title: str) -> str:
    """将标题转为安全文件名"""
    import re
    # 去除不安全字符，保留中文/英文/数字/下划线/连字符
    safe = re.sub(r'[^\w\u4e00-\u9fff\-]', '_', title)
    safe = re.sub(r'_+', '_', safe).strip('_')
    # 截断到50字符
    return safe[:50] if safe else "report"
