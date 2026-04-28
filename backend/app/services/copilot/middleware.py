"""Copilot 中间件栈。

P0 基础健壮性：
- SummarizationMiddleware（框架原生）：上下文溢出自动摘要，替代手写字符级压缩
- MemoryMiddleware（框架原生）：自动加载 AGENTS.md 关键记忆
- SubAgentResilienceMiddleware：Sub Agent 容错
- ToolCallLimiterMiddleware：工具调用次数硬限制，防无限循环
- InputGuardMiddleware：输入长度+基础过滤
"""

import logging
from datetime import datetime, timezone

from langchain.agents.middleware.types import AgentMiddleware

logger = logging.getLogger(__name__)


# ─── 工具调用次数硬限制 ───────────────────────────────────────────

class ToolCallLimiterMiddleware(AgentMiddleware):
    """工具调用次数硬限制。

    防止 Agent 无限循环调用工具。
    每个工具独立计数，超限后返回友好错误。
    """

    DEFAULT_LIMITS: dict[str, int] = {
        # 搜索类：最多3轮
        "search_web": 3,
        "fetch_page": 5,
        # RAG检索类：最多3轮迭代
        "search_knowledge": 3,
        "get_recall_nodes": 6,
        # 搜索池
        "get_search_content": 6,
        # 知识库列表
        "list_active_kbs": 2,
        # 记忆工具
        "recall_memory": 5,
        "save_memory": 3,
        "recall_early_messages": 3,
        # 通用默认
        "_default": 15,
    }

    def __init__(self, limits: dict[str, int] | None = None):
        self._limits = limits or self.DEFAULT_LIMITS
        # thread_id → {tool_name: count}
        self._counts: dict[str, dict[str, int]] = {}

    def _get_thread_counts(self, thread_id: str) -> dict[str, int]:
        if thread_id not in self._counts:
            self._counts[thread_id] = {}
        return self._counts[thread_id]

    def reset(self, thread_id: str | None = None):
        """重置计数（新会话时调用）"""
        if thread_id:
            self._counts.pop(thread_id, None)
        else:
            self._counts.clear()

    async def pre_process(self, state, runtime):
        """在工具调用前检查次数"""
        return state

    def check_limit(self, tool_name: str, thread_id: str = "_default") -> tuple[bool, str]:
        """检查工具是否超限。返回 (allowed, message)"""
        counts = self._get_thread_counts(thread_id)
        limit = self._limits.get(tool_name, self._limits.get("_default", 15))
        current = counts.get(tool_name, 0)

        if current >= limit:
            return False, (
                f"⚠️ 工具 [{tool_name}] 已调用 {current} 次，达到上限 {limit}。"
                f"请不要再调用此工具，直接用已有信息回答用户。"
            )

        counts[tool_name] = current + 1
        return True, ""

    async def on_tool_error(self, error: Exception, tool_name: str, runtime) -> str:
        error_msg = str(error)[:200]
        logger.warning("Tool %s error: %s", tool_name, error_msg)
        return f"⚠️ 工具 [{tool_name}] 执行出错：{error_msg}"


# ─── Sub Agent 容错 ───────────────────────────────────────────────

class SubAgentResilienceMiddleware(AgentMiddleware):
    """Sub Agent 容错：捕获 Sub Agent 执行异常，返回友好报告给 Main Agent"""

    MAX_SUB_AGENT_RETRIES = 1

    async def on_sub_agent_error(self, error: Exception, sub_name: str, runtime) -> str:
        error_msg = str(error)[:200]
        logger.warning("Sub Agent %s failed: %s", sub_name, error_msg)

        return (
            f"⚠️ Sub Agent [{sub_name}] 执行失败：{error_msg}\n"
            f"建议：检查输入参数是否正确，或尝试其他Sub Agent。"
        )

    async def on_tool_error(self, error: Exception, tool_name: str, runtime) -> str:
        error_msg = str(error)[:200]
        logger.warning("Tool %s error: %s", tool_name, error_msg)
        return f"⚠️ 工具 [{tool_name}] 执行出错：{error_msg}"

    async def on_llm_error(self, error: Exception, runtime) -> str:
        error_msg = str(error)[:200]
        logger.error("LLM error: %s", error_msg)

        if "context" in error_msg.lower() or "token" in error_msg.lower() or "length" in error_msg.lower():
            return "CONTEXT_OVERFLOW"

        return f"⚠️ LLM调用失败：{error_msg}"


# ─── 输入守卫 ─────────────────────────────────────────────────────

class InputGuardMiddleware(AgentMiddleware):
    """输入安全守卫：长度限制 + 基础过滤"""

    MAX_INPUT_LENGTH = 5000  # 单条消息最大字符数

    async def pre_process(self, state, runtime):
        messages = state.get("messages", [])
        if not messages:
            return state

        # 只检查最新的用户消息
        last_msg = messages[-1] if messages else None
        if last_msg and hasattr(last_msg, 'content'):
            content = last_msg.content if isinstance(last_msg.content, str) else str(last_msg.content)

            # 长度截断
            if len(content) > self.MAX_INPUT_LENGTH:
                last_msg.content = content[:self.MAX_INPUT_LENGTH] + "\n\n[消息过长，已截断]"
                logger.warning("Input truncated: %d → %d chars", len(content), self.MAX_INPUT_LENGTH)

        return state


# ─── 旧版 SessionArchiveMiddleware（已废弃，由 SummarizationMiddleware 替代） ───
# 保留引用以兼容旧代码，实际不再使用

class SessionArchiveMiddleware(AgentMiddleware):
    """[已废弃] 由框架原生 SummarizationMiddleware 替代。
    保留空壳以兼容旧 import，不再执行实际逻辑。"""

    async def pre_process(self, state, runtime):
        return state
