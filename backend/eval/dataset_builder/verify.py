"""RAG 评估数据集构建 — Round 2 质量清洗 + Round 3 多源问题合成（V3.1）。

V3.1 优化：
- 消除全局 _global_node_store，改为参数传递
- 直接 import call_llm，不再用薄包装
- 修复 _check_single_source 异常时默认保留的 bug
- find_topic_overlapping_pairs 全量参与（不再截断到10题）
- _find_by_id 改为精确匹配
"""

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# 公共工具 — 直接使用 extract_qa 的
# ══════════════════════════════════════════════════════════

def compute_embeddings(texts: list[str], llm_config: dict | None = None) -> list[list[float]]:
    """计算文本的 embedding 向量"""
    try:
        from app.services.knowledge.engine import _get_index
        idx = _get_index()
        if idx is not None and hasattr(idx, "_embed_model"):
            embed_model = idx._embed_model
            return [embed_model.get_text_embedding(t[:512]) for t in texts]
    except Exception:
        pass

    if llm_config:
        try:
            from langchain_openai import OpenAIEmbeddings
            emb = OpenAIEmbeddings(
                model="text-embedding-v3",
                api_key=llm_config["api_key"],
                base_url=llm_config.get("base_url"),
            )
            return emb.embed_documents(texts)
        except Exception:
            pass

    logger.warning("No embedding available, semantic dedup will be skipped")
    return []


def cosine_similarity(a: list[float], b: list[float]) -> float:
    import numpy as np
    a_arr = np.array(a)
    b_arr = np.array(b)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))


def _get_question(qa: dict) -> str:
    """兼容 V3 的 question 字段和旧版的 user_input 字段"""
    return qa.get("question", "") or qa.get("user_input", "")


# ══════════════════════════════════════════════════════════
# Round 2: 语义去重 → LLM 主导合并
# ══════════════════════════════════════════════════════════

SIMILAR_MERGE_PROMPT = """以下是两个语义相似的RAG评估问题。请判断它们是否在问同一件事，如果是，生成一个合并后的优化问题。

## 问题A
问题：{question_a}
主题：{topic_a}
依赖节点：{dep_nodes_a}
问题类型：{type_a}

## 问题B
问题：{question_b}
主题：{topic_b}
依赖节点：{dep_nodes_b}
问题类型：{type_b}

## 判断规则

1. 如果两个问题本质在问同一件事（即使表述不同、角度不同），则应合并
2. 如果它们只是主题相关但在问不同的事情，不应合并
3. 合并后的问题应该：
   - 涵盖两个问题各自关注的信息点（不丢失任何一方有价值的信息需求）
   - 表述比原来的任何一个都更清晰自然
   - 依赖节点为两者的并集

## 输出

```json
{{
  "should_merge": true/false,
  "reason": "为什么应该/不应该合并",
  "merged_question": "合并后的问题文本（仅should_merge=true时填写）",
  "merged_question_type": "合并后的类型（fact/keyword/vague/negation）",
  "merged_difficulty": "合并后的难度",
  "merged_dependency_node_ids": ["合并后的依赖节点ID并集"],
  "merged_topic": "合并后的主题描述"
}}
```"""


def find_similar_candidates(qa_pairs: list[dict], threshold: float = 0.88) -> list[tuple[int, int, float]]:
    questions = [_get_question(qa) for qa in qa_pairs]
    embeddings = compute_embeddings(questions)

    if not embeddings or len(embeddings) != len(questions):
        logger.warning("Embedding computation failed, skipping semantic dedup")
        return []

    candidates = []
    for i in range(len(qa_pairs)):
        for j in range(i + 1, len(qa_pairs)):
            sim = cosine_similarity(embeddings[i], embeddings[j])
            if sim > threshold:
                candidates.append((i, j, sim))

    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates


def merge_similar_questions(
    qa_pairs: list[dict],
    llm_config: dict,
    node_store: dict[str, dict] | None = None,
    dedup_threshold: float = 0.88,
) -> list[dict]:
    """语义去重（LLM 主导合并）— 只对相似对调 LLM，次数很少"""
    from eval.dataset_builder.extract_qa import call_llm_structured, generate_answer_for_question, parse_json_response

    candidates = find_similar_candidates(qa_pairs, threshold=dedup_threshold)
    logger.info("Found %d similar candidate pairs (threshold=%.2f)", len(candidates), dedup_threshold)

    merged_indices = set()
    new_entries = []

    for i, j, sim in candidates:
        if i in merged_indices or j in merged_indices:
            continue

        merge_result = call_llm_structured(
            SIMILAR_MERGE_PROMPT.format(
                question_a=_get_question(qa_pairs[i]),
                topic_a=qa_pairs[i].get("topic", ""),
                dep_nodes_a=qa_pairs[i]["dependency_node_ids"],
                type_a=qa_pairs[i]["question_type"],
                question_b=_get_question(qa_pairs[j]),
                topic_b=qa_pairs[j].get("topic", ""),
                dep_nodes_b=qa_pairs[j]["dependency_node_ids"],
                type_b=qa_pairs[j]["question_type"],
            ),
            llm_config,
        )

        if not merge_result:
            continue

        # call_llm_structured 可能返回 list 或 dict
        if isinstance(merge_result, list):
            if not merge_result:
                continue
            merge_result = merge_result[0]
        if not isinstance(merge_result, dict):
            continue

        if merge_result.get("should_merge"):
            merged_ids = merge_result.get("merged_dependency_node_ids", [])
            if not merged_ids:
                merged_ids = list(set(
                    qa_pairs[i].get("dependency_node_ids", [])
                    + qa_pairs[j].get("dependency_node_ids", [])
                ))

            ref_contexts = []
            for nid in merged_ids:
                if node_store and nid in node_store:
                    ref_contexts.append(node_store[nid].get("text", "")[:500])
            if not ref_contexts:
                ref_contexts = (
                    qa_pairs[i].get("reference_contexts", [])
                    + qa_pairs[j].get("reference_contexts", [])
                )

            new_entry = {
                "id": f"q_merged_{len(new_entries):03d}",
                "question": merge_result.get("merged_question", ""),
                "user_input": merge_result.get("merged_question", ""),
                "question_type": merge_result.get("merged_question_type", qa_pairs[i]["question_type"]),
                "difficulty": (merge_result.get("merged_difficulty") or qa_pairs[i]["difficulty"]).lower() if isinstance(merge_result.get("merged_difficulty") or qa_pairs[i]["difficulty"], str) else "medium",
                "hop_type": "single",
                "hop_count": 1,
                "topic": merge_result.get("merged_topic", ""),
                "dependency_node_ids": merged_ids,
                "reference_context_ids": merged_ids,
                "reference_contexts": ref_contexts,
                "kb_doc_id": qa_pairs[i].get("kb_doc_id"),
                "kb_doc_ids": list(set(
                    qa_pairs[i].get("kb_doc_ids", [])
                    + qa_pairs[j].get("kb_doc_ids", [])
                )),
                "key_phrases": list(set(
                    qa_pairs[i].get("key_phrases", [])
                    + qa_pairs[j].get("key_phrases", [])
                )),
                "source": "merged",
                "merged_from": [qa_pairs[i]["id"], qa_pairs[j]["id"]],
                "reference": "",
                "answer_facts": [],
            }

            answer, facts = generate_answer_for_question(new_entry, node_store, llm_config)
            new_entry["reference"] = answer
            new_entry["answer_facts"] = facts

            new_entries.append(new_entry)
            merged_indices.add(i)
            merged_indices.add(j)

    result = [qa_pairs[i] for i in range(len(qa_pairs)) if i not in merged_indices]
    result.extend(new_entries)

    logger.info("Semantic dedup: %d → %d (merged %d pairs, created %d new entries)",
                len(qa_pairs), len(result), len(merged_indices) // 2, len(new_entries))
    return result


# ══════════════════════════════════════════════════════════
# Round 2: 过广+代表性 合并批量检查
# ══════════════════════════════════════════════════════════

BATCH_QUALITY_CHECK_PROMPT = """以下是RAG评估数据集的问题列表，请对每个问题进行两项质量检查：

1. **过广检测**：问题是否过于宽泛（任何相关文档都能沾边）？
   - 过广问题应给出收窄版本
   - 无法收窄的过广问题应剔除

2. **代表性检测**：问题是否能测试RAG系统的检索或回答能力？
   - 好问题：能测试系统对知识库内容的理解和检索能力（包括事实查询、流程理解、条件判断、否定验证等）
   - 差问题：仅凭字面匹配即可回答、答案与知识库无关、或问题本身无法被合理回答

## 问题列表

{questions_text}

## 输出格式

对每个问题输出判断，用JSON格式：

```json
[
  {{{{
    "id": "问题ID",
    "is_over_broad": false,
    "is_representative": true,
    "narrowed_version": ""
  }}}},
  {{{{
    "id": "问题ID",
    "is_over_broad": true,
    "is_representative": true,
    "narrowed_version": "收窄后的问题"
  }}}},
  {{{{
    "id": "问题ID",
    "is_over_broad": false,
    "is_representative": false,
    "narrowed_version": ""
  }}}}
]
```

只输出JSON数组，不要解释。"""


def batch_quality_check(
    qa_pairs: list[dict],
    llm_config: dict,
    node_store: dict[str, dict] | None = None,
    batch_size: int = 12,
) -> list[dict]:
    """Round 2 合并版：过广检测 + 代表性筛选（一次批量调 LLM）"""
    from eval.dataset_builder.extract_qa import call_llm_structured

    kept = []
    total_batches = (len(qa_pairs) + batch_size - 1) // batch_size

    for bi in range(total_batches):
        batch = qa_pairs[bi * batch_size:(bi + 1) * batch_size]

        questions_text = ""
        for qa in batch:
            dep_ids = qa.get("dependency_node_ids", [])
            summary = ""
            if node_store:
                texts = []
                for nid in dep_ids[:2]:
                    if nid in node_store:
                        texts.append(node_store[nid].get("text", "")[:80])
                summary = "；".join(texts)

            questions_text += (
                f"ID: {qa['id']} | 问题: {_get_question(qa)} | "
                f"主题: {qa.get('topic', '')} | 摘要: {summary}\n"
            )

        try:
            result = call_llm_structured(
                BATCH_QUALITY_CHECK_PROMPT.format(questions_text=questions_text),
                llm_config,
                temperature=0.0,
                max_tokens=800,
            )
        except Exception as e:
            logger.warning("Batch quality check failed: %s, keeping all in batch", e)
            kept.extend(batch)
            continue

        verdicts = {}
        if isinstance(result, list):
            for item in result:
                qid = item.get("id", "")
                verdicts[qid] = item
        elif isinstance(result, dict) and "id" in result:
            verdicts[result["id"]] = result

        for qa in batch:
            v = verdicts.get(qa["id"], {})

            # 过广检测
            is_over_broad = v.get("is_over_broad", False)
            narrowed = v.get("narrowed_version", "").strip()

            if is_over_broad and narrowed and narrowed != _get_question(qa):
                qa["question"] = narrowed
                qa["user_input"] = narrowed
                logger.info("Narrowed: %s", narrowed[:60])
            elif is_over_broad and not narrowed:
                logger.info("Removed (too broad, cannot narrow): %s", _get_question(qa)[:60])
                continue

            # 代表性检测
            is_representative = v.get("is_representative", True)
            if not is_representative:
                logger.info("Removed (not representative): %s", _get_question(qa)[:60])
                continue

            kept.append(qa)

    logger.info("Batch quality check: %d/%d kept", len(kept), len(qa_pairs))
    return kept


# ══════════════════════════════════════════════════════════
# Round 3: 主题交叉 + 合成
# ══════════════════════════════════════════════════════════

TOPIC_OVERLAP_PROMPT = """以下是来自两个不同文档的RAG评估问题列表。请找出主题有交叉的问题对——即两个问题在探究相关或互补的主题。

## 文档A（ID: {doc_a_id}）的问题

{questions_a}

## 文档B（ID: {doc_b_id}）的问题

{questions_b}

## 判断标准

"主题交叉"意味着：
- 两个问题涉及相同或紧密相关的概念/实体/流程
- 但它们提供的是不同视角、不同阶段或不同方面的信息
- 合并后可以产生一个更全面、更深入的问题

不是主题交叉：
- 只是因为都提到某个常见词（如"系统"、"数据"）
- 合并后没有额外价值（两问同一件事）

## 输出

```json
{{
  "overlapping_pairs": [
    {{
      "id_a": "问题A的ID",
      "id_b": "问题B的ID",
      "topic_a": "问题A的主题",
      "topic_b": "问题B的主题",
      "shared_theme": "交叉的共享主题",
      "overlap_reason": "为什么这两个问题的主题有交叉"
    }}
  ]
}}
```

如果没有主题交叉的问题对，输出空数组。"""


def _format_qa_brief(qa_list: list[dict]) -> str:
    lines = []
    for qa in qa_list:
        lines.append("ID: %s | 问题: %s | 主题: %s" % (qa['id'], _get_question(qa), qa.get('topic', '')))
    return "\n".join(lines)


def find_topic_overlapping_pairs(
    qa_pairs: list[dict],
    llm_config: dict,
) -> list[tuple[dict, dict, dict]]:
    from eval.dataset_builder.extract_qa import call_llm_structured

    by_doc: dict[int, list[dict]] = {}
    for qa in qa_pairs:
        if qa.get("hop_type") != "single":
            continue
        doc_id = qa.get("kb_doc_id")
        if doc_id:
            by_doc.setdefault(doc_id, []).append(qa)

    if len(by_doc) < 2:
        return []

    candidate_pairs = []
    doc_ids = list(by_doc.keys())

    for i in range(len(doc_ids)):
        for j in range(i + 1, len(doc_ids)):
            # 全量参与，不再截断
            qas_a = by_doc[doc_ids[i]]
            qas_b = by_doc[doc_ids[j]]
            # 但如果单文档问题太多，截到20条以控制 token
            qas_a_trimmed = qas_a[:20]
            qas_b_trimmed = qas_b[:20]

            overlap_result = call_llm_structured(
                TOPIC_OVERLAP_PROMPT.format(
                    doc_a_id=doc_ids[i],
                    questions_a=_format_qa_brief(qas_a_trimmed),
                    doc_b_id=doc_ids[j],
                    questions_b=_format_qa_brief(qas_b_trimmed),
                ),
                llm_config,
            )

            if not overlap_result:
                continue

            # call_llm_structured 可能返回 list，取第一个 dict
            if isinstance(overlap_result, list):
                if not overlap_result:
                    continue
                overlap_result = overlap_result[0] if isinstance(overlap_result[0], dict) else {}
            if not isinstance(overlap_result, dict):
                continue

            for match in overlap_result.get("overlapping_pairs", []):
                qa_a = _find_by_id(qas_a, match.get("id_a", ""))
                qa_b = _find_by_id(qas_b, match.get("id_b", ""))
                if qa_a and qa_b:
                    candidate_pairs.append((qa_a, qa_b, match))

    logger.info("Found %d topic-overlapping candidate pairs", len(candidate_pairs))
    return candidate_pairs


def _find_by_id(qa_list: list[dict], qa_id: str) -> dict | None:
    """精确匹配 ID"""
    if not qa_id:
        return None
    for qa in qa_list:
        if qa.get("id") == qa_id:
            return qa
    return None


MULTI_SOURCE_SYNTHESIS_PROMPT = """以下是两个来自不同文档的RAG评估问题，它们的主题有交叉，可能可以合并为一个更有深度的多源问题。

## 问题A（来自文档: {doc_a_id}）
问题：{question_a}
主题：{topic_a}
依赖节点内容：
{nodes_a_text}

## 问题B（来自文档: {doc_b_id}）
问题：{question_b}
主题：{topic_b}
依赖节点内容：
{nodes_b_text}

## 主题交叉
{overlap_reason}
共享主题：{shared_theme}

## 【核心要求】

1. 合并后的问题是对两个问题的**优化升级**，不是简单拼接
2. 必须同时需要两个文档的信息才能完整回答
3. 问题要自然流畅，像真实用户会提出的信息需求
4. 合并后的问题应该比原来任何一个都更有评估价值

如果两个问题不能自然合并（硬凑、没有逻辑关联），不要强行合并。

## 输出

```json
{{
  "can_merge": true/false,
  "reason": "为什么可以/不可以合并",
  "question": "合并优化后的最终问题（仅can_merge=true时填写）",
  "question_type": "multi_source",
  "difficulty": "medium/hard",
  "dependency_node_ids": ["所有依赖节点ID（A和B的并集）"],
  "nodes_from_doc_a": ["来自文档A的节点ID"],
  "nodes_from_doc_b": ["来自文档B的节点ID"],
  "topic": "合并后的主题描述",
  "answer": "基于两个文档依赖节点的完整答案",
  "answer_facts": ["原子事实列表"],
  "why_multi_source": "为什么需要两个文档才能完整回答"
}}
```"""


def _format_dependency_nodes(qa: dict, node_store: dict[str, dict] | None = None) -> str:
    parts = []
    for nid in qa.get("dependency_node_ids", []):
        if node_store and nid in node_store:
            text = node_store[nid].get("text", "")[:400]
            parts.append(f"[{nid[:8]}] {text}")
        else:
            parts.append(f"[{nid[:8]}] (节点文本不可用)")
    return "\n\n".join(parts) if parts else "（无可用节点文本）"


SINGLE_SOURCE_CHECK_PROMPT = """请尝试仅根据以下单一来源回答问题。不要使用任何外部知识。

来源：
{source_text}

问题：{question}

如果你能给出完整且正确的答案，输出：CAN_ANSWER_ALONE
如果你无法仅从此来源给出完整答案，输出：NEEDS_BOTH_SOURCES

判断：<CAN_ANSWER_ALONE 或 NEEDS_BOTH_SOURCES>"""


def _check_single_source(question: str, source_text: str, llm_config: dict) -> bool:
    """检查单一来源能否回答问题。返回 True=能单独回答（应剔除），False=不能单独回答（保留）"""
    from eval.dataset_builder.extract_qa import call_llm

    try:
        response = call_llm(
            SINGLE_SOURCE_CHECK_PROMPT.format(source_text=source_text[:1500], question=question),
            llm_config,
            temperature=0.0,
            max_tokens=100,
        )
        return "CAN_ANSWER_ALONE" in response.upper()
    except Exception:
        # 异常时保守处理：假设能单独回答 → 剔除未验证的多源问题
        logger.warning("Single-source check failed, assuming answerable alone (conservative reject)")
        return True


def synthesize_multi_source_questions(
    qa_pairs: list[dict],
    llm_config: dict,
    node_store: dict[str, dict] | None = None,
    max_multi_source: int = 30,
) -> list[dict]:
    """Round 3: 从单文档问题中合成多源问题"""
    from eval.dataset_builder.extract_qa import call_llm_structured, generate_answer_for_question

    candidate_pairs = find_topic_overlapping_pairs(qa_pairs, llm_config)
    if not candidate_pairs:
        return []

    multi_source_qa = []
    used_single_ids = set()

    for qa_a, qa_b, match in candidate_pairs:
        if len(multi_source_qa) >= max_multi_source:
            break
        if qa_a["id"] in used_single_ids or qa_b["id"] in used_single_ids:
            continue

        nodes_a_text = _format_dependency_nodes(qa_a, node_store)
        nodes_b_text = _format_dependency_nodes(qa_b, node_store)

        synthesis = call_llm_structured(
            MULTI_SOURCE_SYNTHESIS_PROMPT.format(
                doc_a_id=qa_a.get("kb_doc_id", "文档A"),
                question_a=_get_question(qa_a),
                topic_a=qa_a.get("topic", ""),
                nodes_a_text=nodes_a_text,
                doc_b_id=qa_b.get("kb_doc_id", "文档B"),
                question_b=_get_question(qa_b),
                topic_b=qa_b.get("topic", ""),
                nodes_b_text=nodes_b_text,
                overlap_reason=match.get("overlap_reason", ""),
                shared_theme=match.get("shared_theme", ""),
            ),
            llm_config,
        )

        if not synthesis:
            continue

        # call_llm_structured 可能返回 list，取第一个 dict
        if isinstance(synthesis, list):
            if not synthesis:
                continue
            synthesis = synthesis[0] if isinstance(synthesis[0], dict) else {}
        if not isinstance(synthesis, dict) or not synthesis.get("can_merge"):
            continue

        merged_question = synthesis.get("question", "").strip()
        if not merged_question:
            continue

        # 单源可答性验证
        a_can_alone = _check_single_source(merged_question, nodes_a_text, llm_config)
        b_can_alone = _check_single_source(merged_question, nodes_b_text, llm_config)
        if a_can_alone or b_can_alone:
            logger.info("Multi-source question can be answered from one source, skipping: %s", merged_question[:60])
            continue

        dep_ids_a = synthesis.get("nodes_from_doc_a", qa_a.get("dependency_node_ids", []))
        dep_ids_b = synthesis.get("nodes_from_doc_b", qa_b.get("dependency_node_ids", []))
        all_dep_ids = list(set(dep_ids_a + dep_ids_b))

        ref_contexts = []
        if node_store:
            for nid in all_dep_ids:
                if nid in node_store:
                    ref_contexts.append(node_store[nid].get("text", "")[:500])
        if not ref_contexts:
            ref_contexts = qa_a.get("reference_contexts", []) + qa_b.get("reference_contexts", [])

        entry = {
            "id": f"q_ms_{len(multi_source_qa):03d}",
            "question": merged_question,
            "user_input": merged_question,
            "question_type": "multi_source",
            "difficulty": synthesis.get("difficulty", "hard").lower() if isinstance(synthesis.get("difficulty"), str) else "hard",
            "hop_type": "multi_source",
            "hop_count": 1,
            "topic": synthesis.get("topic", ""),
            "dependency_node_ids": all_dep_ids,
            "reference_context_ids": all_dep_ids,
            "reference_contexts": ref_contexts,
            "reference": synthesis.get("answer", ""),
            "answer_facts": synthesis.get("answer_facts", []),
            "kb_doc_id": None,
            "kb_doc_ids": list(set(
                qa_a.get("kb_doc_ids", [qa_a.get("kb_doc_id")])
                + qa_b.get("kb_doc_ids", [qa_b.get("kb_doc_id")])
            ) - {None}),
            "key_phrases": list(set(
                qa_a.get("key_phrases", [])
                + qa_b.get("key_phrases", [])
            )),
            "source": "synthesized",
            "synthesized_from": [qa_a["id"], qa_b["id"]],
            "why_multi_source": synthesis.get("why_multi_source", ""),
        }

        if not entry["reference"]:
            answer, facts = generate_answer_for_question(entry, node_store, llm_config)
            entry["reference"] = answer
            entry["answer_facts"] = facts

        multi_source_qa.append(entry)
        used_single_ids.add(qa_a["id"])
        used_single_ids.add(qa_b["id"])

        logger.info("Synthesized multi-source question: %s", merged_question[:80])

    logger.info("Round 3: synthesized %d multi-source questions", len(multi_source_qa))
    return multi_source_qa


# ══════════════════════════════════════════════════════════
# 便捷入口 — node_store 通过参数传递
# ══════════════════════════════════════════════════════════

def run_round2(
    qa_pairs: list[dict],
    llm_config: dict,
    node_store: dict[str, dict] | None = None,
    dedup_threshold: float = 0.88,
) -> list[dict]:
    """运行 Round 2 质量清洗

    语义去重 → 批量过广+代表性检查
    """
    logger.info("=== Round 2: Quality Cleaning ===")
    logger.info("Input: %d questions", len(qa_pairs))

    logger.info("Step 6: Semantic dedup + LLM merge...")
    qa_pairs = merge_similar_questions(qa_pairs, llm_config, node_store=node_store, dedup_threshold=dedup_threshold)
    logger.info("After dedup: %d questions", len(qa_pairs))

    logger.info("Step 7+8: Batch quality check (over-broad + representativeness)...")
    qa_pairs = batch_quality_check(qa_pairs, llm_config, node_store=node_store)
    logger.info("After quality check: %d questions", len(qa_pairs))

    logger.info("Round 2 complete: %d questions remain", len(qa_pairs))
    return qa_pairs


def run_round3(
    qa_pairs: list[dict],
    llm_config: dict,
    node_store: dict[str, dict] | None = None,
    max_multi_source: int = 30,
) -> list[dict]:
    """运行 Round 3 多源问题合成"""
    logger.info("=== Round 3: Multi-Source Synthesis ===")
    multi_source = synthesize_multi_source_questions(qa_pairs, llm_config, node_store=node_store, max_multi_source=max_multi_source)
    logger.info("Round 3 complete: %d multi-source questions synthesized", len(multi_source))
    return multi_source


# ─── 向后兼容：保留 set_node_store 供旧调用 ───

_compat_node_store: dict[str, dict] | None = None

def set_node_store(store: dict[str, dict]) -> None:
    """[向后兼容] 设置 node store。新代码应通过参数传递 node_store。"""
    global _compat_node_store
    _compat_node_store = store

def _get_compat_node_store() -> dict[str, dict] | None:
    return _compat_node_store
