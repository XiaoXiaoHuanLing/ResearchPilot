"""RAG 评估数据集构建 — Round 1: 单文档问题生成（优化版 V3.1）。

核心优化（vs V3）：
- 问题+答案+原子事实+关键词+常识判断合并为一次 LLM 调用
- 知识依赖性检查内置到生成 prompt，不再单独调 LLM
- 节点过滤保留短但高密度内容（含数值/术语/代码）
- 强调多节点综合问题（优先利用组内多节点信息）
- 模型 fallback：配额耗尽自动切换下一个模型
- 断点续建：每 N 组保存中间结果
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── Round 1 合并 prompt（问题+答案+事实+关键词+常识判断，一次输出） ───

SINGLE_DOC_QA_PROMPT = """你是一个RAG评估数据集构造专家。以下是来自同一文档的 {group_size} 个内容节点。
请基于这些节点生成评估用问答对。

## 节点内容

{nodes_text}

## 【绝对不可违反的核心规则】

1. 每个问题的答案必须100%完整地包含在你标注的依赖节点中，与之外的其他节点无关
2. 任何不相关的节点都绝对不能回答这个问题（即问题的答案只存在于你标注的依赖节点里）
3. 问题必须足够具体，不能有任何歧义
4. 绝对不能生成可以通过常识回答的问题（必须依赖本知识库的专业/内部信息）
5. 绝对不能生成答案在节点中只出现一次且非常隐蔽的问题（如某个不起眼的数值埋在长段落末尾）
6. 问题要符合真实用户的提问习惯，不要使用生硬的学术表述

## 【多节点综合优先】

优先生成需要综合多个节点信息才能回答的问题。
如果一个事实只用1个节点就能完整回答，且该事实过于简单（如单个数值），优先级较低。
好的多节点问题示例：
- "故障确认后的排查步骤中，日志查询分别需要查哪两种日志主题？各自的查询条件是什么？"（综合了确认步骤+查询参数）
- "紧急窗口内的操作和验证步骤中，分别提到了哪些阈值标准？"（综合了操作阈值+验证阈值）

## 【类型多样性强制要求】

你生成的每个问题必须覆盖不同的类型，禁止全部生成fact型！
每组节点最多生成1个fact型问题，其余必须是其他类型。
各类型比例目标：fact ≤ 30%, keyword ≥ 25%, vague ≥ 20%, negation ≥ 25%

- **fact**：直接事实型，如"AutoMerging的默认阈值是多少？"（最多1个/组）
- **keyword**：包含特定术语的精确查询，如"simple_ratio_thresh参数的含义"（必出）
- **vague**：模糊短查询（≤10字），如"检索策略"、"告警阈值"（必出）
- **negation**：否定/验证式，如"BM25是否支持增量更新？"、"日志查询是否必须指定时间范围？"（必出）

## 【输出必填字段】

每个问题的 answer、answer_facts、key_phrases、is_common_sense 四个字段必须全部填写，不得遗漏。
- answer：至少2-3句话，包含必要的细节和推理
- answer_facts：3-6个原子事实
- key_phrases：2-5个核心关键词/术语
- is_common_sense：true=常识可答/false=必须查资料

## 输出格式

输出JSON数组。如果这些节点中没有值得出题的独立信息点，输出空数组 []。
宁可少输出，也不要输出凑数的问题。

```json
[
  {{
    "question": "问题文本",
    "question_type": "fact/keyword/vague/negation",
    "difficulty": "easy/medium/hard",
    "dependency_node_ids": ["依赖的节点ID列表（使用[节点 xxx]中的短ID，多节点问题时填2个以上）"],
    "topic": "该问题的主题，用一句话描述这个问题在探究什么（如：CPU告警的分类与识别）",
    "why_not_common_sense": "为什么这不是常识问题",
    "is_common_sense": false,
    "answer": "基于依赖节点的完整答案，至少2-3句话，包含必要的细节和推理",
    "answer_facts": ["原子事实1", "原子事实2", "原子事实3"],
    "key_phrases": ["关键词1", "关键词2", "关键词3"]
  }}
]
```"""


# ══════════════════════════════════════════════════════════
# LLM 调用 — 支持 fallback + checkpoint
# ══════════════════════════════════════════════════════════

def _load_model_list() -> list[str]:
    """从环境变量加载模型优先级列表"""
    models = []
    for key in ["EVAL_LLM_MODEL", "DASHSCOPE_MODEL_NAME",
                "DASHSCOPE_MODEL_NAME_FALLBACK", "DASHSCOPE_MODEL_NAME_FALLBACK_2"]:
        val = os.environ.get(key, "").strip()
        if val and val not in models:
            models.append(val)
    if not models:
        models = ["qwen3.5-plus-2026-02-15"]
    return models


def call_llm(
    prompt: str,
    llm_config: dict,
    temperature: float = 0.7,
    max_tokens: int = 800,
    max_retries: int = 3,
) -> str:
    """调用 LLM 生成文本，支持模型 fallback + 重试"""
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage

    model_list = _load_model_list()
    # 如果 llm_config 指定了模型，优先使用
    primary = llm_config.get("model", "")
    if primary and primary in model_list:
        model_list.remove(primary)
        model_list.insert(0, primary)
    elif primary:
        model_list.insert(0, primary)

    api_key = llm_config["api_key"]
    base_url = llm_config.get("base_url")

    last_err = None
    for model in model_list:
        for attempt in range(max_retries):
            try:
                llm = ChatOpenAI(
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                    streaming=False,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=120,
                    max_retries=0,
                )
                resp = llm.invoke([HumanMessage(content=prompt)])
                content = resp.content.strip()
                if content:
                    return content
            except Exception as e:
                last_err = e
                err_str = str(e)
                # 配额耗尽 → 切换模型
                if "403" in err_str or "429" in err_str or "AllocationQuota" in err_str or "quota" in err_str.lower():
                    logger.warning("Model %s quota exhausted, switching...", model)
                    break  # 跳到下一个模型
                # 其他错误 → 重试
                wait = (attempt + 1) * 3
                logger.warning("LLM call failed (model=%s, attempt %d/%d), retrying in %ds: %s",
                               model, attempt + 1, max_retries, wait, err_str[:100])
                time.sleep(wait)

    # 所有模型都失败
    if last_err:
        raise last_err
    raise RuntimeError("All models exhausted, no response generated")


def call_llm_structured(prompt: str, llm_config: dict, temperature: float = 0.3, max_tokens: int = 1000) -> dict:
    """调用 LLM 并解析 JSON 响应"""
    raw = call_llm(prompt, llm_config, temperature=temperature, max_tokens=max_tokens)
    return parse_json_response(raw)


def parse_json_response(response: str) -> list[dict] | dict:
    """从 LLM 响应中解析 JSON（容忍 markdown 代码块包裹）"""
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response)
    if match:
        json_str = match.group(1).strip()
    else:
        json_str = response.strip()

    try:
        result = json.loads(json_str)
        return result if isinstance(result, (list, dict)) else [result]
    except json.JSONDecodeError:
        pass

    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = json_str.find(start_char)
        end = json_str.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            try:
                result = json.loads(json_str[start:end + 1])
                return result if isinstance(result, (list, dict)) else [result]
            except json.JSONDecodeError:
                continue

    logger.warning("Failed to parse JSON response: %s", response[:200])
    return [] if json_str.lstrip().startswith("[") else {}


# ══════════════════════════════════════════════════════════
# 节点预处理 — 保留短但高密度节点
# ══════════════════════════════════════════════════════════

# 高密度内容指示模式：数值、技术术语、代码片段、参数配置
_HIGH_DENSITY_PATTERNS = re.compile(
    r'(\d+\.?\d*\s*[%毫秒GBMBKB次条个台]|'   # 数值+单位
    r'[a-zA-Z_]\w*\s*[=：:]\s*|'              # 参数赋值
    r'[a-zA-Z_]{3,}|'                          # 英文术语（3字以上）
    r'[<>]=?\s*\d+|'                           # 阈值比较
    r'`[^`]+`|'                                # 代码片段
    r'http[s]?://|'                            # URL
    r'\{[^}]+\})'                              # JSON/配置块
)


def load_nodes_from_docstore() -> list[dict]:
    """从 docstore 加载所有节点，返回标准化格式"""
    from app.services.knowledge.docstore import get_docstore

    store = get_docstore()
    if not hasattr(store, "docs"):
        return []

    from llama_index.core.schema import NodeRelationship

    nodes = []
    for key, node in store.docs.items():
        metadata = getattr(node, "metadata", {}) or {}
        text = getattr(node, "text", "") or getattr(node, "get_content", lambda: "")()

        parent_id = None
        rels = getattr(node, "relationships", {}) or {}
        if NodeRelationship.PARENT in rels:
            parent_id = rels[NodeRelationship.PARENT].node_id

        children = getattr(node, "children", None)
        is_leaf = children is None or len(children) == 0

        nodes.append({
            "node_id": getattr(node, "node_id", key),
            "text": text,
            "metadata": metadata,
            "parent_id": parent_id,
            "is_leaf": is_leaf,
        })

    return nodes


def _is_high_density_text(text: str) -> bool:
    """判断短文本是否是高密度干货内容"""
    if not text.strip():
        return False
    # 含数值+单位、参数配置、代码、英文术语
    if _HIGH_DENSITY_PATTERNS.search(text):
        return True
    # 含中文专业术语组合（连续名词短语 4+ 字）
    if re.search(r'[一-鿿]{4,}(?:的|与|及|和|或)[一-鿿]{2,}', text):
        return True
    return False


def filter_valuable_nodes(nodes: list[dict], min_length: int = 30) -> list[dict]:
    """过滤掉不值得出题的水节点

    逻辑：
    - 短文本 (< 30字) 但含高密度内容 → 保留
    - 短文本 (< 30字) 且无高密度内容 → 过滤
    - 中长文本 (30-80字) → 保留（可能是精炼的干货段落）
    - 长文本 (≥ 80字) 但平均行极短（纯列表/目录） → 过滤
    """
    valuable = []
    for node in nodes:
        text = node.get("text", "")
        text_len = len(text)

        # 极短 + 无高密度 → 过滤（纯标题、导航、分隔符）
        if text_len < min_length:
            if _is_high_density_text(text):
                valuable.append(node)
            continue

        # 中长文本 → 检查是否纯列举/目录型
        lines = [l for l in text.split("\n") if l.strip()]
        if lines:
            avg_line_len = sum(len(l) for l in lines) / len(lines)
            # 平均行极短 → 可能是目录/索引，除非整体含高密度内容
            if avg_line_len < 12 and not _is_high_density_text(text):
                continue

        valuable.append(node)

    return valuable


def group_adjacent_nodes(
    doc_nodes: list[dict],
    group_size: int = 5,
    overlap: int = 1,
) -> list[list[dict]]:
    """将文档节点按顺序分组，带重叠"""
    groups = []
    step = group_size - overlap
    for start in range(0, len(doc_nodes), step):
        group = doc_nodes[start:start + group_size]
        if len(group) >= 2:
            groups.append(group)
        if start + group_size >= len(doc_nodes):
            break
    return groups


def format_nodes_for_prompt(nodes: list[dict], max_chars_per_node: int = 600) -> str:
    """格式化节点文本，每个节点标注 ID + 截断内容"""
    parts = []
    for node in nodes:
        text = node.get("text", "")
        if len(text) > max_chars_per_node:
            text = text[:max_chars_per_node] + "..."
        parts.append(f"[节点 {node['node_id'][:8]}] {text}")
    return "\n\n".join(parts)


def _resolve_short_ids(short_ids: list[str], group: list[dict]) -> list[str]:
    """将 LLM 输出的短 ID 解析为完整 node_id"""
    resolved = []
    for sid in short_ids:
        sid = sid.strip().strip("[]")
        if not sid:
            continue
        for node in group:
            full_id = node["node_id"]
            if full_id.startswith(sid) or sid in full_id:
                resolved.append(full_id)
                break
        else:
            logger.warning("Cannot resolve short ID: %s", sid)
    return resolved


# ══════════════════════════════════════════════════════════
# 断点续建 — checkpoint 保存/加载
# ══════════════════════════════════════════════════════════

def _save_checkpoint(qa_pairs: list[dict], doc_counts: dict, question_id: int, path: str) -> None:
    """保存中间结果到 checkpoint 文件"""
    data = {
        "qa_pairs": qa_pairs,
        "doc_counts": doc_counts,
        "question_id": question_id,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Checkpoint save failed: %s", e)


def _load_checkpoint(path: str) -> dict | None:
    """加载 checkpoint 文件"""
    if not Path(path).exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info("Loaded checkpoint: %d questions, question_id=%d, saved at %s",
                    len(data.get("qa_pairs", [])), data.get("question_id", 0), data.get("timestamp", ""))
        return data
    except Exception as e:
        logger.warning("Checkpoint load failed: %s", e)
        return None


# ══════════════════════════════════════════════════════════
# 后补：answer / answer_facts / key_phrases 缺失修复
# ══════════════════════════════════════════════════════════

_MISSING_FIELDS_PROMPT = """以下问题的部分字段缺失，请基于参考上下文补全。

问题：{question}
主题：{topic}

参考上下文：
{contexts}

当前已有信息：
答案：{current_answer}
原子事实：{current_facts}
关键词：{current_key_phrases}

请补全缺失字段，输出格式：
答案：<完整答案，至少2-3句话>
原子事实：
- <事实1>
- <事实2>
- <事实3>
关键词：<关键词1>，<关键词2>，<关键词3>"""


def _fix_missing_fields(qa: dict, node_store: dict[str, dict], llm_config: dict) -> None:
    """补全缺失的 answer / answer_facts / key_phrases"""
    has_answer = bool(qa.get("reference", "").strip())
    has_facts = bool(qa.get("answer_facts"))
    has_phrases = bool(qa.get("key_phrases"))

    if has_answer and has_facts and has_phrases:
        return

    # 收集上下文
    contexts = []
    for nid in qa.get("dependency_node_ids", []):
        node = node_store.get(nid)
        if node:
            contexts.append(node.get("text", "")[:500])
    if not contexts:
        contexts = qa.get("reference_contexts", [])
    if not contexts:
        return

    context_text = "\n---\n".join(contexts)

    try:
        resp = call_llm(
            _MISSING_FIELDS_PROMPT.format(
                question=qa["question"],
                topic=qa.get("topic", ""),
                contexts=context_text[:2000],
                current_answer=qa.get("reference", "（无）"),
                current_facts="；".join(qa.get("answer_facts", [])) or "（无）",
                current_key_phrases="、".join(qa.get("key_phrases", [])) or "（无）",
            ),
            llm_config,
            temperature=0.3,
            max_tokens=600,
        )
        _parse_fix_response(resp, qa)
    except Exception as e:
        logger.warning("Failed to fix missing fields for %s: %s", qa.get("id"), e)


def _parse_fix_response(response: str, qa: dict) -> None:
    """解析补全响应"""
    lines = response.strip().split("\n")
    in_facts = False
    facts = []
    answer = ""

    for line in lines:
        line = line.strip()
        if line.startswith("答案：") or line.startswith("答案:"):
            answer = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            in_facts = False
        elif line.startswith("原子事实：") or line.startswith("原子事实:"):
            in_facts = True
        elif line.startswith("关键词：") or line.startswith("关键词:"):
            in_facts = False
            phrases_text = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            if phrases_text and not qa.get("key_phrases"):
                qa["key_phrases"] = [p.strip() for p in re.split(r"[，,、]", phrases_text) if p.strip()]
        elif in_facts and line.startswith("- "):
            facts.append(line[2:].strip())
        elif in_facts and re.match(r"^\d+\.", line):
            facts.append(re.sub(r"^\d+\.", "", line).strip())

    if answer and not qa.get("reference", "").strip():
        qa["reference"] = answer
    if facts and not qa.get("answer_facts"):
        qa["answer_facts"] = facts


# ══════════════════════════════════════════════════════════
# Round 1 主入口
# ══════════════════════════════════════════════════════════

def generate_round1_questions(
    all_doc_nodes: dict[int, list[dict]],  # kb_doc_id → nodes
    llm_config: dict,
    target_total: int = 80,
    group_size: int = 5,
    overlap: int = 1,
    max_per_group: int = 3,
    max_chars_per_node: int = 600,
    min_node_length: int = 30,
    checkpoint_path: str | None = None,
    checkpoint_every: int = 5,
) -> tuple[list[dict], dict[str, dict]]:
    """Round 1: 生成整个数据集的单文档问题

    Returns:
        (qa_pairs, node_store) — node_store: node_id → node data 全局映射
    """
    # 全局 node store
    node_store: dict[str, dict] = {}
    for doc_id, nodes in all_doc_nodes.items():
        for node in nodes:
            node_store[node["node_id"]] = node

    # 每个文档的配额
    total_nodes = sum(len(n) for n in all_doc_nodes.values())
    doc_quotas = {}
    for doc_id, nodes in all_doc_nodes.items():
        doc_quotas[doc_id] = max(3, round(target_total * len(nodes) / total_nodes))

    all_qa: list[dict] = []
    doc_counts: dict[int, int] = {doc_id: 0 for doc_id in all_doc_nodes}
    question_id = 0
    start_doc_idx = 0
    start_group_idx = 0

    # ── 断点续建 ──
    if checkpoint_path:
        ckpt = _load_checkpoint(checkpoint_path)
        if ckpt:
            all_qa = ckpt["qa_pairs"]
            doc_counts = ckpt["doc_counts"]
            question_id = ckpt["question_id"]
            logger.info("Resuming from checkpoint: %d existing questions", len(all_qa))

    type_counts: dict[str, int] = {}
    for qa in all_qa:
        t = qa.get("question_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    doc_ids = list(all_doc_nodes.keys())

    for di, doc_id in enumerate(doc_ids):
        if di < start_doc_idx:
            continue

        nodes = all_doc_nodes[doc_id]
        valuable_nodes = filter_valuable_nodes(nodes, min_length=min_node_length)
        if not valuable_nodes:
            logger.info("Doc %d: no valuable nodes after filtering", doc_id)
            continue

        groups = group_adjacent_nodes(valuable_nodes, group_size, overlap)
        quota = doc_quotas[doc_id]
        logger.info("Doc %d: %d valuable nodes → %d groups, quota=%d",
                     doc_id, len(valuable_nodes), len(groups), quota)

        for gi, group in enumerate(groups):
            if doc_counts[doc_id] >= quota:
                break

            nodes_text = format_nodes_for_prompt(group, max_chars_per_node)
            prompt = SINGLE_DOC_QA_PROMPT.format(
                group_size=len(group),
                nodes_text=nodes_text,
            )

            try:
                raw_response = call_llm(prompt, llm_config, temperature=0.7, max_tokens=1500)
                qa_entries = parse_json_response(raw_response)
                if isinstance(qa_entries, dict):
                    qa_entries = [qa_entries]
            except Exception as e:
                logger.warning("Doc %d group %d: LLM call failed: %s", doc_id, gi, e)
                # 保存 checkpoint 后继续
                if checkpoint_path:
                    _save_checkpoint(all_qa, doc_counts, question_id, checkpoint_path)
                continue

            if not qa_entries:
                logger.debug("Doc %d group %d: no questions generated (acceptable)", doc_id, gi)
                continue

            for entry in qa_entries[:max_per_group]:
                if doc_counts[doc_id] >= quota:
                    break

                question = entry.get("question", "").strip()
                if not question:
                    continue

                # ── 常识判断（内置在 prompt 中） ──
                is_common_sense = entry.get("is_common_sense", False)
                if is_common_sense:
                    logger.info("Excluded (common sense): %s", question[:80])
                    continue

                # 回填完整 node_id
                dep_short_ids = entry.get("dependency_node_ids", [])
                resolved_ids = _resolve_short_ids(dep_short_ids, group)
                if not resolved_ids:
                    logger.warning("Question '%s' has no resolvable dependency_node_ids, skipping", question[:50])
                    continue

                # 收集依赖节点文本
                ref_contexts = []
                for nid in resolved_ids:
                    if nid in node_store:
                        ref_contexts.append(node_store[nid].get("text", "")[:500])

                # 从合并 prompt 的输出中提取所有字段
                answer = entry.get("answer", "")
                answer_facts = entry.get("answer_facts", [])
                key_phrases = entry.get("key_phrases", [])

                # 类型检查：每组最多1个fact
                q_type = entry.get("question_type", "fact")
                if q_type == "fact":
                    fact_in_group = type_counts.get("fact", 0)
                    if fact_in_group >= max(1, len(qa_entries) * 0.3):
                        # fact 型过多，跳过
                        logger.debug("Skipping fact-type question (too many facts in group): %s", question[:50])
                        continue

                qa = {
                    "id": f"q{question_id:03d}",
                    "question": question,
                    "user_input": question,
                    "question_type": q_type,
                    "difficulty": entry.get("difficulty", "medium").lower() if isinstance(entry.get("difficulty"), str) else "medium",
                    "hop_type": "single",
                    "hop_count": 1,
                    "topic": entry.get("topic", ""),
                    "why_not_common_sense": entry.get("why_not_common_sense", ""),
                    "dependency_node_ids": resolved_ids,
                    "reference_context_ids": resolved_ids,
                    "reference_contexts": ref_contexts,
                    "kb_doc_id": doc_id,
                    "kb_doc_ids": [doc_id],
                    "source": "generated",
                    "reference": answer,
                    "answer_facts": answer_facts if isinstance(answer_facts, list) else [],
                    "key_phrases": key_phrases if isinstance(key_phrases, list) else [],
                }

                # 父节点信息
                first_node = node_store.get(resolved_ids[0])
                if first_node and first_node.get("parent_id"):
                    qa["parent_context_id"] = first_node["parent_id"]

                all_qa.append(qa)
                question_id += 1
                doc_counts[doc_id] += 1
                type_counts[q_type] = type_counts.get(q_type, 0) + 1

            # ── checkpoint ──
            if checkpoint_path and (gi + 1) % checkpoint_every == 0:
                _save_checkpoint(all_qa, doc_counts, question_id, checkpoint_path)

        # 每个文档结束保存 checkpoint
        if checkpoint_path:
            _save_checkpoint(all_qa, doc_counts, question_id, checkpoint_path)

    # ── 补全缺失字段 ──
    missing_count = 0
    for qa in all_qa:
        if not qa.get("reference", "").strip() or not qa.get("answer_facts") or not qa.get("key_phrases"):
            missing_count += 1
            _fix_missing_fields(qa, node_store, llm_config)

    if missing_count:
        logger.info("Fixed missing fields for %d questions", missing_count)

    # 重新编号
    for i, qa in enumerate(all_qa):
        qa["id"] = f"q{i + 1:03d}"

    logger.info("Round 1 complete: %d questions, per doc: %s, by type: %s",
                len(all_qa), doc_counts, type_counts)
    return all_qa, node_store


# ══════════════════════════════════════════════════════════
# 兼容旧接口
# ══════════════════════════════════════════════════════════

def extract_single_hop_qa(
    leaf_nodes: list[dict],
    llm_config: dict,
    max_per_type: dict[str, int] | None = None,
) -> list[dict]:
    """[DEPRECATED] 旧版逐节点生成接口"""
    logger.warning("extract_single_hop_qa is deprecated, use generate_round1_questions instead")
    by_doc: dict[int, list[dict]] = {}
    for node in leaf_nodes:
        doc_id = node.get("metadata", {}).get("kb_doc_id", 0)
        by_doc.setdefault(doc_id, []).append(node)
    qa_pairs, _ = generate_round1_questions(by_doc, llm_config)
    return qa_pairs


def generate_answer_for_question(
    qa_entry: dict,
    node_store: dict[str, dict] | None = None,
    llm_config: dict | None = None,
) -> tuple[str, list[str]]:
    """基于依赖节点上下文生成答案 + 原子事实（供 verify.py 合并问题时用）"""
    if not llm_config:
        return "", []

    contexts = []
    dep_ids = qa_entry.get("dependency_node_ids", [])

    if node_store:
        for nid in dep_ids:
            node = node_store.get(nid)
            if node:
                contexts.append(node.get("text", "")[:500])
    else:
        contexts = qa_entry.get("reference_contexts", [])

    if not contexts:
        return "", []

    context_text = "\n---\n".join(contexts)

    try:
        answer_resp = call_llm(
            _MISSING_FIELDS_PROMPT.format(
                question=qa_entry["question"],
                topic=qa_entry.get("topic", ""),
                contexts=context_text[:2000],
                current_answer="（无）",
                current_facts="（无）",
                current_key_phrases="（无）",
            ),
            llm_config,
            temperature=0.3,
            max_tokens=600,
        )
        # 解析答案
        lines = answer_resp.strip().split("\n")
        answer = ""
        facts = []
        in_facts = False

        for line in lines:
            line = line.strip()
            if line.startswith("答案：") or line.startswith("答案:"):
                answer = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                in_facts = False
            elif line.startswith("原子事实：") or line.startswith("原子事实:"):
                in_facts = True
            elif in_facts and line.startswith("- "):
                facts.append(line[2:].strip())
            elif in_facts and re.match(r"^\d+\.", line):
                facts.append(re.sub(r"^\d+\.", "", line).strip())

        if not answer:
            answer = lines[0] if lines else ""
        if not facts:
            facts = [answer] if answer else []

        return answer, facts
    except Exception as e:
        logger.warning("Failed to generate answer for %s: %s", qa_entry.get("id"), e)
        return "", []


# ─── 保留兼容的旧版函数签名 ───

def extract_key_phrases(text: str, llm_config: dict) -> list[str]:
    """从文本中提取核心关键词（简化版，直接分词取核心短语）"""
    phrases = re.findall(r'[一-鿿]{2,6}|[a-zA-Z_]{3,20}', text)
    return phrases[:5]


def check_knowledge_dependency(question: str, llm_config: dict) -> bool:
    """单条知识依赖性检查（保留兼容）— 已内置到生成 prompt，此函数仅作兼容"""
    return True  # 生成阶段已过滤
