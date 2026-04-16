"""长期记忆工具 — recall_memory / save_memory

使用 ChromaDB 向量库存储，按 user_id 隔离。
L2 长期记忆：用户偏好、工作上下文、研究结论、历史摘要。
"""

import logging
from datetime import datetime
from uuid import uuid4

from langchain_core.tools import tool
from uuid import uuid4

def _uuid7_str():
    """生成唯一 ID（兼容旧版 Python）。"""
    return str(uuid4())

from app.core.config import settings

logger = logging.getLogger(__name__)

# ─── ChromaDB 单例 ────────────────────────────────────────────────────────

_chroma_client = None


def _get_chroma_client():
    """获取 ChromaDB 客户端（单例）。"""
    global _chroma_client
    if _chroma_client is None:
        import chromadb
        _chroma_client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        logger.info("ChromaDB client initialized at %s", settings.chroma_persist_dir)
    return _chroma_client


def _get_collection(user_id: str):
    """获取用户的记忆 collection（自动创建）。"""
    client = _get_chroma_client()
    collection_name = f"user_{user_id}_memory"
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )


# ─── 工具定义 ──────────────────────────────────────────────────────────────

@tool
def recall_memory(query: str) -> str:
    """从长期记忆中检索相关信息。

    当你需要回忆用户偏好、历史研究结论或重要决策时使用。
    基于语义相似度检索，不需要精确匹配。

    Args:
        query: 检索查询，描述你想回忆的内容
    """
    # ToolRuntime 通过 injected 工具参数获取，这里用简单方式
    # 在 deep_agent.py 中通过 context_schema 注入 user_id
    try:
        from langchain_core.tools import InjectedToolArg
        from typing import get_type_hints
    except ImportError:
        pass

    # 简化：通过全局 context 获取 user_id
    user_id = _get_current_user_id()
    if not user_id:
        return "无法访问长期记忆：缺少用户信息"

    try:
        collection = _get_collection(user_id)
        results = collection.query(
            query_texts=[query],
            n_results=5,
            where={"importance": {"$gte": "medium"}} if True else None,
        )

        if not results["documents"] or not results["documents"][0]:
            return "未找到相关记忆"

        parts = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            date_str = meta.get("created_at", "未知日期")
            parts.append(f"[{date_str}] {doc}")

        return "\n---\n".join(parts)
    except Exception as e:
        logger.warning("recall_memory failed: %s", e)
        return f"检索记忆失败: {type(e).__name__}"


@tool
def save_memory(content: str, importance: str = "medium") -> str:
    """保存重要信息到长期记忆。

    当你发现值得长期保留的信息时使用：
    - 用户明确表达的偏好
    - 重要的研究结论
    - 需要跨会话记住的决策

    Args:
        content: 要保存的记忆内容
        importance: "high" | "medium" | "low"
    """
    user_id = _get_current_user_id()
    if not user_id:
        return "无法保存记忆：缺少用户信息"

    try:
        collection = _get_collection(user_id)
        doc_id = f"mem_{_uuid7_str()}"
        collection.add(
            documents=[content],
            metadatas=[{
                "importance": importance,
                "created_at": datetime.now().isoformat(),
                "type": "auto_saved",
            }],
            ids=[doc_id],
        )
        logger.info("Saved memory for user %s: %s (importance=%s)", user_id, content[:50], importance)
        return f"已保存到长期记忆（重要性：{importance}）"
    except Exception as e:
        logger.warning("save_memory failed: %s", e)
        return f"保存记忆失败: {type(e).__name__}"


# ─── Context Helper ────────────────────────────────────────────────────────

_current_context = {}


def set_current_context(user_id: str, session_id: str = ""):
    """设置当前请求的上下文（在调用 agent 前设置）。"""
    _current_context["user_id"] = user_id
    _current_context["session_id"] = session_id


def clear_current_context():
    """清除当前请求的上下文。"""
    _current_context.clear()


def _get_current_user_id() -> str:
    """获取当前用户 ID。"""
    return _current_context.get("user_id", "default")
