"""Agentic RAG 工具：search_knowledge + get_recall_nodes + RecallPoolManager。

核心设计：
- Agent 自主驱动反思检索循环，工具只提供原子能力
- search_knowledge: 检索结果存 RecallPool，返回轻量确认（不进 messages）
- get_recall_nodes: Agent 按需获取指定节点完整内容
- RecallPool: 按 node_id 去重，会话级临时状态
"""

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger(__name__)

# ─── RecallPool 存储 ───

RECALL_POOL_DIR = None  # 延迟初始化


def _get_recall_pool_dir() -> Path:
    """获取 RecallPool 临时文件目录"""
    global RECALL_POOL_DIR
    if RECALL_POOL_DIR is None:
        RECALL_POOL_DIR = Path(settings.storage_base_dir) / "recall_pool"
        RECALL_POOL_DIR.mkdir(parents=True, exist_ok=True)
    return RECALL_POOL_DIR


# ─── 数据结构 ───

@dataclass
class RecalledNode:
    """召回池中的单个节点"""
    node_id: str           # 节点唯一标识（去重键）
    kb_id: int             # 所属知识库ID
    title: str             # 文档标题
    source: str            # 来源文档
    relevance_score: float # 相关度分数
    snippet: str           # 节点完整内容
    round: int             # 第几轮检索获得


# ─── RecallPoolManager ───

class RecallPoolManager:
    """召回池管理器：临时文件存储，按 node_id 去重

    生命周期：
    - 每次新用户消息到达时，API 层调用 reset_for_new_turn() 清空旧 pool
    - 一轮对话中（同一次 Agent 调用），pool 在多轮检索间累积去重
    - 超过 POOL_MAX_AGE_HOURS 的临时文件由 cleanup_stale_files() 自动清理
    """

    # 临时文件最大保留时间（小时）
    POOL_MAX_AGE_HOURS = 24

    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self._pool_path = _get_recall_pool_dir() / f"{thread_id}.json"
        self._nodes: dict[str, RecalledNode] | None = None

    def _load(self) -> dict[str, RecalledNode]:
        """从临时文件加载"""
        if self._nodes is not None:
            return self._nodes

        if self._pool_path.exists():
            try:
                with open(self._pool_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._nodes = {
                    nid: RecalledNode(**nd) for nid, nd in data.items()
                }
            except Exception as e:
                logger.warning("RecallPool load failed: %s", e)
                self._nodes = {}
        else:
            self._nodes = {}
        return self._nodes

    def _save(self):
        """保存到临时文件"""
        if self._nodes is None:
            return
        try:
            data = {nid: asdict(nd) for nid, nd in self._nodes.items()}
            with open(self._pool_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("RecallPool save failed: %s", e)

    def add_nodes(self, new_nodes: list[RecalledNode]) -> int:
        """追加节点，按 node_id 去重覆盖（保留更高分）。返回新增节点数。"""
        pool = self._load()
        added = 0
        for node in new_nodes:
            existing = pool.get(node.node_id)
            if existing is None:
                pool[node.node_id] = node
                added += 1
            elif node.relevance_score > existing.relevance_score:
                pool[node.node_id] = node  # 更高分覆盖
        self._save()
        return added

    def get_nodes(self, node_ids: list[str]) -> list[RecalledNode]:
        """获取指定节点"""
        pool = self._load()
        return [pool[nid] for nid in node_ids if nid in pool]

    def get_all_meta(self) -> list[dict]:
        """获取所有节点的元信息（node_id + title + score，不含 snippet）"""
        pool = self._load()
        return [
            {"node_id": nid, "title": nd.title, "relevance_score": nd.relevance_score}
            for nid, nd in pool.items()
        ]

    def get_all_node_ids(self) -> list[str]:
        """获取所有节点 ID"""
        pool = self._load()
        return list(pool.keys())

    def total_count(self) -> int:
        """获取节点总数"""
        return len(self._load())

    def clear(self):
        """清空召回池并删除临时文件"""
        self._nodes = {}
        try:
            if self._pool_path.exists():
                self._pool_path.unlink()
        except Exception:
            pass

    def reset_for_new_turn(self):
        """新对话轮次开始时重置召回池。

        与 clear() 相同——清空旧数据，确保不同轮次的检索结果不会混入。
        由 API 层在每次新用户消息到达时调用。
        """
        self.clear()
        logger.debug("RecallPool reset for new turn: %s", self.thread_id)

    @staticmethod
    def cleanup_stale_files(max_age_hours: int = 24):
        """清理超过 max_age_hours 的临时文件。

        可由定时任务或心跳调用，防止磁盘文件无限增长。
        """
        pool_dir = _get_recall_pool_dir()
        if not pool_dir.exists():
            return 0

        import time
        now = time.time()
        max_age_seconds = max_age_hours * 3600
        cleaned = 0

        for f in pool_dir.glob("*.json"):
            try:
                if now - f.stat().st_mtime > max_age_seconds:
                    f.unlink()
                    cleaned += 1
            except Exception:
                pass

        if cleaned > 0:
            logger.info("RecallPool cleanup: removed %d stale files (> %dh)", cleaned, max_age_hours)
        return cleaned


# ─── 工具实现 ───

async def _search_knowledge_impl(
    query: str,
    top_k: int = 5,
    kb_ids: str = "",
    thread_id: str = "",
) -> str:
    """知识库混合检索，结果存 RecallPool，返回轻量确认"""
    from app.services.knowledge.retriever import hybrid_retrieve

    # 解析 kb_ids
    target_kb_ids = None
    if kb_ids:
        target_kb_ids = [int(x.strip()) for x in kb_ids.split(",") if x.strip().isdigit()]

    # 执行检索
    result = await hybrid_retrieve(query, kb_ids=target_kb_ids, top_k=top_k)
    citations = result.get("citations", [])
    mode = result.get("retrieval_mode", "unknown")

    if not citations:
        return f"[{mode}] 知识库中未找到与查询相关的内容。"

    # 构建 RecalledNode 列表
    # 确定当前轮次
    pool = RecallPoolManager(thread_id) if thread_id else None
    current_round = 1
    if pool:
        existing_ids = pool.get_all_node_ids()
        # 简单推断轮次：已有节点数 / 平均每轮节点数 + 1
        current_round = (len(existing_ids) // max(top_k, 1)) + 1

    new_nodes = []
    for c in citations:
        node_id = c.get("node_id", f"node_{uuid.uuid4().hex[:8]}")
        new_nodes.append(RecalledNode(
            node_id=node_id,
            kb_id=c.get("kb_id", 0) or 0,
            title=c.get("title", ""),
            source=c.get("source", ""),
            relevance_score=c.get("relevance_score", 0) or 0,
            snippet=c.get("snippet", ""),
            round=current_round,
        ))

    # 存入 RecallPool
    if pool:
        added = pool.add_nodes(new_nodes)
        total = pool.total_count()
        existing_ids = set(pool.get_all_node_ids()) - {nd.node_id for nd in new_nodes}
    else:
        added = len(new_nodes)
        total = len(new_nodes)
        existing_ids = set()

    # 构建轻量确认返回
    lines = [f"本轮检索到 {added} 个新节点（去重后），召回池累计 {total} 个节点。"]
    lines.append("新增节点:")
    for nd in sorted(new_nodes, key=lambda x: x.relevance_score, reverse=True):
        lines.append(f"  [{nd.node_id}] {nd.title} | 相关度: {nd.relevance_score:.2f}")

    if existing_ids:
        lines.append(f"已有节点(本轮未覆盖): {', '.join(list(existing_ids)[:10])}")

    return "\n".join(lines)


async def _get_recall_nodes_impl(node_ids: str, thread_id: str = "") -> str:
    """获取召回池中指定节点的完整内容"""
    if not thread_id:
        return "⚠️ 无法访问召回池：缺少会话ID"

    pool = RecallPoolManager(thread_id)
    ids = [x.strip() for x in node_ids.split(",") if x.strip()]
    nodes = pool.get_nodes(ids)

    if not nodes:
        return "召回池中未找到指定节点。"

    lines = []
    for nd in sorted(nodes, key=lambda x: x.relevance_score, reverse=True):
        lines.append(
            f"[{nd.node_id}] {nd.title} (相关度: {nd.relevance_score:.2f}, KB: {nd.kb_id})\n"
            f"来源: {nd.source}\n"
            f"---\n{nd.snippet}\n"
        )

    return "\n".join(lines)


# ─── LangChain 工具 ───

# thread_id 通过统一上下文注入
from app.services.chat.tools.thread_context import (
    get_current_thread_id,
    set_current_thread_id,
    clear_current_thread_id,
)


@tool
async def search_knowledge(query: str, top_k: int = 5, kb_ids: str = "") -> str:
    """知识库混合检索：Dense+BM25→RRF→AutoMerging。

    检索结果自动追加到召回池（RecallPool），按节点ID去重覆盖。
    不返回完整节点内容，只返回本轮新增节点摘要。
    Agent 可调用 get_recall_nodes 按需阅读感兴趣的节点完整内容。

    Args:
        query: 查询问题（可直接使用改写后的查询，无需额外改写工具）
        top_k: 召回数量。简单问题3-5，复杂/宽泛问题8-12
        kb_ids: 限定的知识库ID列表，逗号分隔（空=全部启用的KB）
    """
    return await _search_knowledge_impl(query, top_k, kb_ids, get_current_thread_id())


@tool
async def get_recall_nodes(node_ids: str) -> str:
    """获取召回池中指定节点的完整内容。

    Agent 选择性阅读感兴趣的节点，用于判断信息是否充分。
    可一次获取多个节点，用逗号分隔。

    Args:
        node_ids: 节点ID列表，逗号分隔（如"node_7f3a,node_b2c1"）
    """
    return await _get_recall_nodes_impl(node_ids, get_current_thread_id())
