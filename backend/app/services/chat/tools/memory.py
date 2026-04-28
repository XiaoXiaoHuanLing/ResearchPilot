"""四层记忆工具。

L0 工作记忆: Agent state (messages) — 由 SummarizationMiddleware 管理
L1 会话快照: Checkpointer (SQLite) — 框架内置
L2 长期记忆: ChromaDB chat_memories 集合 — 本文件实现
L3 关键记忆: AGENTS.md — 框架 MemoryMiddleware 自动加载

L2 两种内容：
1. 用户偏好/习惯/工作 → 主Agent主动提取 → recall_memory/save_memory 读写
2. 早期消息归档 → EarlyMessageArchiveMiddleware自动触发 → recall_early_messages 读取
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger(__name__)

CHAT_MEMORIES_COLLECTION = "chat_memories"


# ─── ChromaDB 辅助 ───

def _get_memories_collection():
    """获取或创建 chat_memories 集合"""
    from app.services.knowledge.engine import _get_chroma_client
    client = _get_chroma_client()
    return client.get_or_create_collection(
        CHAT_MEMORIES_COLLECTION,
        metadata={"description": "Chat long-term memories: preferences, habits, archived messages"},
    )


def _embed_text(text: str) -> list[float]:
    """获取文本嵌入向量"""
    from app.services.knowledge.engine import _embed_model
    return _embed_model._get_text_embedding(text)


def _embed_query(text: str) -> list[float]:
    """获取查询嵌入向量"""
    from app.services.knowledge.engine import _embed_model
    return _embed_model._get_query_embedding(text)


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


# ─── L2.1: 用户偏好/习惯记忆 ───

@tool
async def recall_memory(query: str, top_k: int = 5) -> str:
    """语义召回长期记忆。

    从向量库中检索与查询最相关的记忆。
    用于：回忆用户偏好、历史结论、长期任务、经验教训等。

    Args:
        query: 查询内容
        top_k: 召回数量（3-10）
    """
    try:
        collection = _get_memories_collection()

        # 过滤只查偏好类记忆（category=preference 或 category=insight）
        query_embedding = _embed_query(query)

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where={"category": {"$in": ["preference", "insight", "task"]}},
        )

        if not results["documents"] or not results["documents"][0]:
            return "未找到相关记忆。"

        lines = []
        for i, (doc, meta) in enumerate(zip(results["documents"][0], results["metadatas"][0])):
            category = meta.get("category", "unknown")
            tags = meta.get("tags", "")
            created = meta.get("created_at", "")
            expired = meta.get("expires_at", "")
            expire_note = f" (过期: {expired})" if expired else ""
            lines.append(f"{i+1}. [{category}{' | '+tags if tags else ''}] {doc[:200]} ({created}{expire_note})")

        return f"召回 {len(lines)} 条记忆：\n" + "\n".join(lines)
    except Exception as e:
        logger.warning("recall_memory failed: %s", e)
        return f"记忆召回失败: {str(e)[:100]}"


@tool
async def save_memory(content: str, category: str = "preference", tags: str = "", expires_at: str = "") -> str:
    """写入长期记忆到向量库。

    用于：保存用户明确表达的偏好、长期任务、重要结论、经验教训。
    不要滥用：只在用户明确要求或信息确实重要时保存。

    Args:
        content: 记忆内容
        category: 类别 — "preference"(偏好/习惯) / "insight"(经验教训) / "task"(长期任务)
        tags: 标签，逗号分隔（如"偏好,编程"）
        expires_at: 过期时间，ISO格式（如"2026-06-01"），空=永不过期
    """
    try:
        collection = _get_memories_collection()

        doc_id = f"mem_{uuid.uuid4().hex[:8]}"
        embedding = _embed_text(content)

        metadata = {
            "category": category,
            "tags": tags,
            "created_at": _now_str(),
        }
        if expires_at:
            metadata["expires_at"] = expires_at

        collection.add(
            ids=[doc_id],
            documents=[content],
            embeddings=[embedding],
            metadatas=[metadata],
        )

        logger.info("Saved memory: %s [%s] %s", doc_id, category, content[:80])
        return f"已保存记忆({category}): {content[:100]}..."
    except Exception as e:
        logger.error("save_memory failed: %s", e)
        return f"记忆保存失败: {str(e)[:100]}"


# ─── L2.2: 早期消息归档召回 ───

@tool
async def recall_early_messages(query: str, top_k: int = 3) -> str:
    """语义召回早期会话归档消息。

    当对话很长时，早期消息会被归档到向量库。
    用此工具检索与当前问题相关的早期对话内容。

    Args:
        query: 查询内容
        top_k: 召回数量（3-5）
    """
    try:
        collection = _get_memories_collection()

        query_embedding = _embed_query(query)

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where={"category": "archived_message"},
        )

        if not results["documents"] or not results["documents"][0]:
            return "未找到相关的早期对话记录。"

        lines = []
        for i, (doc, meta) in enumerate(zip(results["documents"][0], results["metadatas"][0])):
            role = meta.get("role", "unknown")
            created = meta.get("created_at", "")
            lines.append(f"{i+1}. [{role}] {doc[:300]} ({created})")

        return f"召回 {len(lines)} 条早期对话：\n" + "\n".join(lines)
    except Exception as e:
        logger.warning("recall_early_messages failed: %s", e)
        return f"早期对话召回失败: {str(e)[:100]}"


# ─── 归档辅助函数（供 EarlyMessageArchiveMiddleware 调用） ───

async def archive_messages_to_chroma(messages: list[dict], thread_id: str = "") -> int:
    """将早期消息归档到 ChromaDB chat_memories 集合。

    Args:
        messages: [{role, content, ...}, ...] 格式的消息列表
        thread_id: 会话ID

    Returns:
        归档的消息条数
    """
    if not messages:
        return 0

    try:
        collection = _get_memories_collection()

        ids = []
        documents = []
        embeddings = []
        metadatas = []

        for msg in messages:
            content = msg.get("content", "")
            if not content or len(content) < 10:
                continue  # 跳过空消息和极短消息

            role = msg.get("role", "unknown")
            doc_id = f"arch_{thread_id}_{uuid.uuid4().hex[:8]}"

            try:
                embedding = _embed_text(content[:500])  # 只嵌入前500字节省计算
            except Exception:
                continue  # 嵌入失败跳过

            ids.append(doc_id)
            documents.append(content[:1000])  # 只存前1000字
            embeddings.append(embedding)
            metadatas.append({
                "category": "archived_message",
                "role": role,
                "thread_id": thread_id,
                "created_at": _now_str(),
            })

        if ids:
            collection.add(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            logger.info("Archived %d early messages for thread %s", len(ids), thread_id)

        return len(ids)
    except Exception as e:
        logger.error("archive_messages failed: %s", e)
        return 0
