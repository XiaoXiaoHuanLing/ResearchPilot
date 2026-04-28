"""咨询质量审查模块。

采集后自动评分，低于阈值直接丢弃不保存DB。
评分维度：内容长度(25) / 乱码检测(25) / 结构完整度(25) / 语言纯度(25)
"""

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ─── 评分阈值 ───
SCORE_DISCARD = 40      # <40 丢弃
SCORE_MEDIUM = 60       # 40-59 保存但标记 medium


@dataclass
class QualityResult:
    """质量审查结果"""
    passed: bool          # 是否通过（>=40分）
    score: float          # 0-100
    label: str            # "high" / "medium" / "low"(low不存DB)
    reason: str           # 评分原因简述


def quality_check(title: str, content: str, url: str = "") -> QualityResult:
    """对采集到的咨询进行质量审查。

    Args:
        title: 文章标题
        content: 提取的正文内容
        url: 原始URL

    Returns:
        QualityResult: 评分结果，passed=False 建议丢弃
    """
    # 特殊规则：标题为空直接丢弃
    if not title or title.strip() in ("无标题", "untitled", ""):
        return QualityResult(passed=False, score=0, label="low", reason="标题为空")

    # 内容为空直接丢弃
    if not content or len(content.strip()) < 30:
        return QualityResult(passed=False, score=5, label="low", reason="内容过短或为空")

    scores = {}
    reasons = []

    # ─── 维度1: 内容长度 (0-25) ───
    content_len = len(content.strip())
    if content_len < 50:
        scores["length"] = 2
        reasons.append("内容过短(<50字)")
    elif content_len < 200:
        scores["length"] = 10
        reasons.append("内容较短")
    elif content_len < 1000:
        scores["length"] = 20
    else:
        scores["length"] = 25

    # ─── 维度2: 乱码检测 (0-25) ───
    garbage_ratio = _compute_garbage_ratio(content)
    if garbage_ratio > 0.30:
        scores["garbage"] = 0
        reasons.append(f"乱码占比过高({garbage_ratio:.0%})")
    elif garbage_ratio > 0.10:
        scores["garbage"] = 10
        reasons.append(f"乱码占比较高({garbage_ratio:.0%})")
    else:
        scores["garbage"] = 25

    # ─── 维度3: 结构完整度 (0-25) ───
    structure_score = 0
    if "\n\n" in content:
        structure_score += 8  # 有段落分隔
    if re.search(r"[\n^]\s*[-*•]\s", content):
        structure_score += 8  # 有列表标记
    if re.search(r"#{1,6}\s", content) or re.search(r"[\n^]\d+[.、]\s", content):
        structure_score += 9  # 有标题/编号标记
    scores["structure"] = structure_score

    # ─── 维度4: 语言纯度 (0-25) ───
    lang_score = _compute_language_score(content)
    scores["language"] = lang_score
    if lang_score < 10:
        reasons.append("语言纯度低(非中英文为主)")

    # ─── 汇总 ───
    total = sum(scores.values())
    if total >= SCORE_MEDIUM:
        label = "high"
    elif total >= SCORE_DISCARD:
        label = "medium"
    else:
        label = "low"

    reason = "; ".join(reasons) if reasons else "质量合格"
    passed = total >= SCORE_DISCARD

    return QualityResult(passed=passed, score=total, label=label, reason=reason)


def _compute_garbage_ratio(text: str) -> float:
    """计算乱码字符占比。

    乱码定义：非CJK统一汉字、非ASCII可打印字符、非常见标点、非常见CJK标点
    """
    if not text:
        return 1.0

    total_chars = len(text)
    if total_chars == 0:
        return 1.0

    # 正常字符模式
    normal_pattern = re.compile(
        r'[\u4e00-\u9fff]'      # CJK统一汉字
        r'|[\u3000-\u303f]'      # CJK标点
        r'|[\uff00-\uffef]'      # 全角字符
        r'|[a-zA-Z0-9]'          # ASCII字母数字
        r'|[\s.,;:!?\'"()\[\]{}<>/@#$%^&*+=\-_~|\\]'  # ASCII标点
        r'|[\u0080-\u024f]'      # 拉丁扩展(含欧洲语言)
    )

    normal_count = len(normal_pattern.findall(text))
    garbage_count = total_chars - normal_count

    return garbage_count / total_chars


def _compute_language_score(text: str) -> float:
    """计算语言纯度评分 (0-25)。

    中文占比>60%或英文占比>60% → 25分，否则按比例递减
    """
    if not text:
        return 0

    # 统计中文字符
    cjk_count = len(re.findall(r'[\u4e00-\u9fff]', text))
    # 统计英文字符
    en_count = len(re.findall(r'[a-zA-Z]', text))
    total = cjk_count + en_count

    if total == 0:
        return 5  # 纯数字/符号内容

    cjk_ratio = cjk_count / total
    en_ratio = en_count / total

    dominant = max(cjk_ratio, en_ratio)

    if dominant > 0.70:
        return 25
    elif dominant > 0.50:
        return 18
    elif dominant > 0.30:
        return 10
    else:
        return 5
