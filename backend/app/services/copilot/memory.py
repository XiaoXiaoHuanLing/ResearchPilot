"""四层记忆管理工具。

L0 工作记忆: 当前会话上下文
L1 会话快照: SQLite Checkpointer
L2 长期记忆: ChromaDB agent_memories 集合
L3 关键记忆: AGENTS.md 自动加载
"""

import uuid
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from langchain_core.tools import tool

from app.core.config import settings

logger = logging.getLogger(__name__)

AGENT_MEMORIES_COLLECTION = "agent_memories"


# ─── Context ───

@dataclass
class Context:
    user_id: str = "default"
    session_id: str = ""


_current_context = None


def set_current_context(user_id: str = "default", session_id: str = ""):
    global _current_context
    _current_context = Context(user_id=user_id, session_id=session_id)


def clear_current_context():
    global _current_context
    _current_context = None


def get_current_context():
    return _current_context


# ─── 记忆工具 ───

@tool
async def recall_memory(query: str, top_k: int = 5) -> str:
    """语义召回长期记忆。

    从向量库中检索与查询最相关的记忆。
    用于：回忆用户偏好、历史结论、长期任务等。
    """
    try:
        from app.services.knowledge.engine import _get_chroma_client, _embed_model
        client = _get_chroma_client()
        collection = client.get_or_create_collection(AGENT_MEMORIES_COLLECTION)

        query_embedding = _embed_model._get_query_embedding(query)

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
        )

        if not results["documents"] or not results["documents"][0]:
            return "未找到相关记忆。"

        lines = []
        for i, (doc, meta) in enumerate(zip(results["documents"][0], results["metadatas"][0])):
            tags = meta.get("tags", "")
            created = meta.get("created_at", "")
            lines.append(f"{i+1}. [{tags}] {doc[:200]} ({created})")

        return f"召回 {len(lines)} 条记忆：\n" + "\n".join(lines)
    except Exception as e:
        logger.warning("recall_memory failed: %s", e)
        return f"记忆召回失败: {str(e)[:100]}"


@tool
async def save_memory(content: str, tags: str = "") -> str:
    """写入长期记忆到向量库。

    用于：保存用户明确表达的偏好、长期任务、重要结论。
    不要滥用：只在用户明确要求或信息确实重要时保存。

    Args:
        content: 记忆内容
        tags: 标签，逗号分隔（如"偏好,任务"）
    """
    try:
        from app.services.knowledge.engine import _get_chroma_client, _embed_model
        client = _get_chroma_client()
        collection = client.get_or_create_collection(AGENT_MEMORIES_COLLECTION)

        doc_id = f"mem_{uuid.uuid4().hex[:8]}"
        embedding = _embed_model._get_text_embedding(content)

        collection.add(
            ids=[doc_id],
            documents=[content],
            embeddings=[embedding],
            metadatas=[{"tags": tags, "created_at": _now_str()}],
        )

        logger.info("Saved memory: %s", content[:80])
        return f"✅ 已保存记忆: {content[:100]}..."
    except Exception as e:
        logger.error("save_memory failed: %s", e)
        return f"❌ 记忆保存失败: {str(e)[:100]}"


def _now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
