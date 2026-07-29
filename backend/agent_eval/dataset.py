"""Agent 全链路评估 — 测试数据集。

扩展 RAG 评估数据集，新增 Agent 场景维度:
  kb_simple / kb_complex / search_simple / search_deep / hybrid / chitchat / out_of_scope
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parent
_DATA_DIR = _MODULE_DIR / "data"
_DATASETS_DIR = _DATA_DIR / "datasets"
_DATASETS_DIR.mkdir(parents=True, exist_ok=True)

# ─── 场景定义 ──────────────────────────────────────────────────

SCENARIOS = {
    "kb_simple": {
        "description": "简单KB问题，1次检索即可",
        "expected_routing": ["retriever"],
        "expected_tool_count_range": [1, 4],
        "expected_iteration_range": [1, 1],
    },
    "kb_complex": {
        "description": "复杂KB问题，需多轮迭代+rerank",
        "expected_routing": ["retriever"],
        "expected_tool_count_range": [4, 10],
        "expected_iteration_range": [2, 3],
    },
    "search_simple": {
        "description": "简单搜索问题",
        "expected_routing": ["searcher"],
        "expected_tool_count_range": [1, 3],
        "expected_iteration_range": [1, 1],
    },
    "search_deep": {
        "description": "深度搜索+页面抓取",
        "expected_routing": ["searcher"],
        "expected_tool_count_range": [4, 8],
        "expected_iteration_range": [2, 3],
    },
    "hybrid": {
        "description": "同时需要KB+搜索",
        "expected_routing": ["retriever", "searcher"],
        "expected_tool_count_range": [5, 12],
        "expected_iteration_range": [2, 4],
    },
    "chitchat": {
        "description": "闲聊/常识，不需要工具",
        "expected_routing": [],
        "expected_tool_count_range": [0, 1],
        "expected_iteration_range": [0, 0],
    },
    "out_of_scope": {
        "description": "超出能力范围，应优雅拒绝",
        "expected_routing": [],
        "expected_tool_count_range": [0, 2],
        "expected_iteration_range": [0, 1],
    },
}

# ─── 默认测试样本（手工构造） ──────────────────────────────────

_DEFAULT_SAMPLES = [
    # kb_simple
    {
        "id": "agent_kb_001",
        "user_input": "GPU显存隔离有哪些方式？",
        "scenario": "kb_simple",
        "expected_routing": ["retriever"],
        "expected_tool_count_range": [1, 4],
        "expected_iteration_range": [1, 1],
        "reference": "GPU显存隔离主要有MIG和GPU虚拟化两种方式。",
        "difficulty": "easy",
        "tags": [],
    },
    {
        "id": "agent_kb_002",
        "user_input": "K8s NetworkPolicy的默认行为是什么？",
        "scenario": "kb_simple",
        "expected_routing": ["retriever"],
        "expected_tool_count_range": [1, 4],
        "expected_iteration_range": [1, 1],
        "reference": "K8s NetworkPolicy默认拒绝所有入站流量，允许所有出站流量。",
        "difficulty": "easy",
        "tags": [],
    },
    # kb_complex
    {
        "id": "agent_kb_003",
        "user_input": "对比MIG和GPU虚拟化在显存隔离方面的差异，各自适用场景是什么？",
        "scenario": "kb_complex",
        "expected_routing": ["retriever"],
        "expected_tool_count_range": [4, 10],
        "expected_iteration_range": [2, 3],
        "reference": "MIG适用于需要硬件级隔离的场景，GPU虚拟化适用于多租户共享。",
        "difficulty": "hard",
        "tags": ["multi-hop", "comparison"],
    },
    {
        "id": "agent_kb_004",
        "user_input": "AI系统安全加固规范中，SSH和TLS分别有什么要求？它们之间的关系是什么？",
        "scenario": "kb_complex",
        "expected_routing": ["retriever"],
        "expected_tool_count_range": [4, 10],
        "expected_iteration_range": [2, 3],
        "reference": "SSH要求密钥认证禁用密码，TLS要求1.2+版本。",
        "difficulty": "hard",
        "tags": ["multi-hop", "reranker-required"],
    },
    # search_simple
    {
        "id": "agent_search_001",
        "user_input": "2026年最新的GPU型号有哪些？",
        "scenario": "search_simple",
        "expected_routing": ["searcher"],
        "expected_tool_count_range": [1, 3],
        "expected_iteration_range": [1, 1],
        "reference": "",
        "difficulty": "easy",
        "tags": ["realtime"],
    },
    {
        "id": "agent_search_002",
        "user_input": "最近一周AI领域有什么重大新闻？",
        "scenario": "search_simple",
        "expected_routing": ["searcher"],
        "expected_tool_count_range": [1, 3],
        "expected_iteration_range": [1, 1],
        "reference": "",
        "difficulty": "easy",
        "tags": ["realtime"],
    },
    # search_deep
    {
        "id": "agent_search_003",
        "user_input": "详细介绍一下Llama 4的技术架构和训练方法，包括模型参数量、训练数据、推理优化等",
        "scenario": "search_deep",
        "expected_routing": ["searcher"],
        "expected_tool_count_range": [4, 8],
        "expected_iteration_range": [2, 3],
        "reference": "",
        "difficulty": "hard",
        "tags": ["realtime", "deep-search"],
    },
    # hybrid
    {
        "id": "agent_hybrid_001",
        "user_input": "对比知识库中GPU运维规范和网上最新GPU运维最佳实践，有什么差异？",
        "scenario": "hybrid",
        "expected_routing": ["retriever", "searcher"],
        "expected_tool_count_range": [5, 12],
        "expected_iteration_range": [2, 4],
        "reference": "",
        "difficulty": "hard",
        "tags": ["multi-source", "comparison"],
    },
    {
        "id": "agent_hybrid_002",
        "user_input": "知识库中提到的MLflow版本是否是最新的？查一下MLflow当前最新版本",
        "scenario": "hybrid",
        "expected_routing": ["retriever", "searcher"],
        "expected_tool_count_range": [4, 10],
        "expected_iteration_range": [2, 3],
        "reference": "",
        "difficulty": "medium",
        "tags": ["multi-source", "fact-check"],
    },
    # chitchat
    {
        "id": "agent_chat_001",
        "user_input": "你好，你能做什么？",
        "scenario": "chitchat",
        "expected_routing": [],
        "expected_tool_count_range": [0, 1],
        "expected_iteration_range": [0, 0],
        "reference": "",
        "difficulty": "easy",
        "tags": [],
    },
    {
        "id": "agent_chat_002",
        "user_input": "什么是机器学习？",
        "scenario": "chitchat",
        "expected_routing": [],
        "expected_tool_count_range": [0, 1],
        "expected_iteration_range": [0, 0],
        "reference": "机器学习是AI的一个子领域，让计算机从数据中学习模式。",
        "difficulty": "easy",
        "tags": ["common-sense"],
    },
    # out_of_scope
    {
        "id": "agent_oos_001",
        "user_input": "帮我写一段Python冒泡排序代码",
        "scenario": "out_of_scope",
        "expected_routing": [],
        "expected_tool_count_range": [0, 2],
        "expected_iteration_range": [0, 1],
        "reference": "",
        "difficulty": "easy",
        "tags": ["code-generation"],
    },
    {
        "id": "agent_oos_002",
        "user_input": "计算1234×5678等于多少",
        "scenario": "out_of_scope",
        "expected_routing": [],
        "expected_tool_count_range": [0, 1],
        "expected_iteration_range": [0, 0],
        "reference": "",
        "difficulty": "easy",
        "tags": ["math"],
    },
]


def load_agent_dataset(path: str | Path | None = None) -> list[dict]:
    """加载 Agent 评估数据集。

    Args:
        path: JSONL 文件路径。None 则使用默认内置样本。

    Returns:
        样本列表
    """
    if path is None:
        logger.info("Using built-in default Agent eval dataset (%d samples)", len(_DEFAULT_SAMPLES))
        return _DEFAULT_SAMPLES.copy()

    path = Path(path)
    if not path.exists():
        logger.warning("Dataset file not found: %s, using defaults", path)
        return _DEFAULT_SAMPLES.copy()

    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                records.append(rec)
            except json.JSONDecodeError:
                continue

    logger.info("Loaded Agent dataset: %s (%d samples)", path, len(records))
    return records


def build_mixed_dataset(
    rag_dataset_path: str | Path,
    extra_samples: list[dict] | None = None,
    rag_scenario: str = "kb_complex",
) -> list[dict]:
    """从现有 RAG 数据集构建混合 Agent 评估数据集。

    将 RAG 数据集样本标记为 kb_simple/kb_complex 场景，
    再混入 search/hybrid/chitchat/out_of_scope 样本。

    Args:
        rag_dataset_path: RAG 评估 JSONL 文件
        extra_samples: 额外 Agent 场景样本
        rag_scenario: RAG 样本的默认场景分类

    Returns:
        混合数据集
    """
    # 加载 RAG 数据集
    rag_path = Path(rag_dataset_path)
    if not rag_path.exists():
        logger.warning("RAG dataset not found: %s", rag_path)
        return extra_samples or _DEFAULT_SAMPLES.copy()

    rag_records = []
    with open(rag_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                # 为 RAG 样本添加 Agent 场景字段
                rec.setdefault("scenario", _classify_rag_scenario(rec))
                rec.setdefault("expected_routing", ["retriever"])
                rec.setdefault("expected_tool_count_range", [2, 8])
                rec.setdefault("expected_iteration_range", [1, 3])
                rec.setdefault("tags", [])
                rag_records.append(rec)
            except json.JSONDecodeError:
                continue

    # 合并
    all_records = rag_records
    if extra_samples:
        all_records.extend(extra_samples)

    logger.info(
        "Mixed dataset: %d RAG + %d extra = %d total",
        len(rag_records), len(extra_samples or []), len(all_records),
    )
    return all_records


def _classify_rag_scenario(rec: dict) -> str:
    """根据 RAG 数据集样本特征推断 Agent 场景。"""
    qt = rec.get("question_type", "fact")
    diff = rec.get("difficulty", "medium")

    # vague + hard → kb_complex
    if qt == "vague" and diff == "hard":
        return "kb_complex"
    # negation → kb_complex (需要验证)
    if qt == "negation":
        return "kb_complex"
    # easy → kb_simple
    if diff == "easy":
        return "kb_simple"
    # medium → kb_complex (大部分需多轮)
    if diff in ("medium", "hard"):
        return "kb_complex"
    return "kb_simple"


def save_dataset(records: list[dict], name: str = "agent_eval") -> Path:
    """保存数据集到 JSONL 文件。"""
    timestamp = __import__("time").strftime("%Y%m%d_%H%M%S")
    dataset_dir = _DATASETS_DIR / f"{timestamp}_{name}"
    dataset_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = dataset_dir / "dataset.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 统计
    scenario_counts = {}
    for rec in records:
        s = rec.get("scenario", "unknown")
        scenario_counts[s] = scenario_counts.get(s, 0) + 1

    meta = {
        "timestamp": timestamp,
        "total_samples": len(records),
        "scenario_distribution": scenario_counts,
    }
    with open(dataset_dir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info("Dataset saved: %s (%d samples, scenarios: %s)", jsonl_path, len(records), scenario_counts)
    return jsonl_path
