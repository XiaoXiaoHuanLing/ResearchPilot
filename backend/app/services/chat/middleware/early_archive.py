"""Layer: 早期消息自动归档中间件。

当 messages > 100 时，自动将前 50 条消息归档到 ChromaDB chat_memories 集合。
归档后从 state 中移除这些消息，减轻上下文压力。

关键注意：归档移除早期消息后，SummarizationMiddleware 的 cutoff_index 可能偏移。
本中间件在 pre_process 阶段执行（在 SummarizationMiddleware 之前），
确保 SummarizationMiddleware 看到的是归档后的 messages。

设计参考：docs/specs/2026-04-27-chat-module-redesign.md §三 3.5
"""

import logging

from langchain.agents.middleware.types import AgentMiddleware

logger = logging.getLogger(__name__)


class EarlyMessageArchiveMiddleware(AgentMiddleware):
    """早期消息自动归档：messages > 阈值时归档早期消息到 ChromaDB。"""

    ARCHIVE_THRESHOLD = 100  # messages 数量超过此值触发归档
    ARCHIVE_COUNT = 50        # 归档前多少条消息

    def __init__(self, threshold: int = 100, archive_count: int = 50):
        self.ARCHIVE_THRESHOLD = threshold
        self.ARCHIVE_COUNT = archive_count

    async def pre_process(self, state, runtime):
        """检查 messages 数量，超过阈值时归档早期消息。"""
        messages = state.get("messages", [])
        if not messages or len(messages) <= self.ARCHIVE_THRESHOLD:
            return state

        logger.info(
            "EarlyMessageArchive: messages=%d > threshold=%d, archiving first %d",
            len(messages), self.ARCHIVE_THRESHOLD, self.ARCHIVE_COUNT,
        )

        # 提取要归档的消息
        to_archive = messages[:self.ARCHIVE_COUNT]

        # 转为 dict 格式
        archive_dicts = []
        for msg in to_archive:
            role = getattr(msg, "type", "unknown")  # HumanMessage.type="human", etc.
            content = ""
            if hasattr(msg, "content"):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content:
                archive_dicts.append({"role": role, "content": content})

        # 归档到 ChromaDB
        try:
            from app.services.chat.tools.memory import archive_messages_to_chroma

            thread_id = ""
            try:
                thread_id = getattr(runtime, "thread_id", "") or ""
            except Exception:
                pass

            archived_count = await archive_messages_to_chroma(archive_dicts, thread_id)
            logger.info("EarlyMessageArchive: archived %d messages", archived_count)
        except Exception as e:
            logger.error("EarlyMessageArchive: archive failed: %s", e)
            # 归档失败不影响主流程，继续使用原始 messages
            return state

        # 从 state 中移除已归档的消息
        # 注意：保留 system message（通常是 messages[0]）
        remaining = messages[self.ARCHIVE_COUNT:]
        if messages and hasattr(messages[0], "type") and messages[0].type == "system":
            # 保留 system message
            remaining = [messages[0]] + remaining

        state["messages"] = remaining
        logger.info(
            "EarlyMessageArchive: messages %d -> %d (archived %d)",
            len(messages), len(remaining), archived_count,
        )

        return state
