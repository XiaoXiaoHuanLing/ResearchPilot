"""Custom middleware - SubAgentResilience + SessionArchive

SubAgentResilienceMiddleware: Catch Sub Agent execution exceptions, return friendly failure ToolMessage
SessionArchiveMiddleware: Archive key info to ChromaDB when session ends
"""

import logging
from datetime import datetime

from deepagents.middleware import SummarizationMiddleware
from langchain_core.messages import ToolMessage, AIMessage
from langchain.agents.middleware.types import AgentMiddleware, AgentState

logger = logging.getLogger(__name__)


def _extract_tc_info(tool_call):
    """Extract name, args, id from ToolCallRequest or similar object.
    
    ToolCallRequest has .tool_call (dict with name/args/id).
    Fallback for other object types.
    """
    if hasattr(tool_call, "tool_call") and isinstance(tool_call.tool_call, dict):
        tc = tool_call.tool_call
        return tc.get("name", ""), tc.get("args", {}), tc.get("id", "unknown")
    # Fallback: try direct attributes
    name = getattr(tool_call, "name", "")
    args = getattr(tool_call, "args", {})
    tc_id = getattr(tool_call, "id", "unknown")
    return name, args, tc_id


class SubAgentResilienceMiddleware(AgentMiddleware):
    """Sub Agent resilience middleware: catch task tool exceptions, return friendly failure.

    - Wraps task tool execution
    - On Sub Agent exception, returns ToolMessage with failure info + suggestions
    - Main Agent can then decide: retry / skip / substitute
    """

    state_schema = AgentState

    def wrap_tool_call(self, tool_call, handler):
        """Wrap tool call, add exception catching for task tool."""
        tc_name, tc_args, tc_id = _extract_tc_info(tool_call)

        if tc_name != "task":
            return handler(tool_call)

        try:
            return handler(tool_call)
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)[:300]
            subagent_type = tc_args.get("subagent_type", "?") if isinstance(tc_args, dict) else "?"
            description = str(tc_args.get("description", "?"))[:100] if isinstance(tc_args, dict) else "?"

            logger.warning(
                "SubAgentResilience: %s task failed: %s: %s",
                subagent_type, error_type, error_msg[:100],
            )

            return ToolMessage(
                content=(
                    f"⚠️ Sub Agent '{subagent_type}' 执行失败\n"
                    f"错误: {error_type}: {error_msg}\n"
                    f"任务: {description}\n\n"
                    f"你可以:\n"
                    f"- 重试: 用相同或调整后的描述再次调用 task\n"
                    f"- 绕过: 跳过此任务继续后续步骤\n"
                    f"- 替代: 换其他 Sub Agent 或直接回答用户"
                ),
                tool_call_id=tc_id,
            )

    async def awrap_tool_call(self, tool_call, handler):
        """Async version of resilience wrapper."""
        tc_name, tc_args, tc_id = _extract_tc_info(tool_call)

        if tc_name != "task":
            return await handler(tool_call)

        try:
            return await handler(tool_call)
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)[:300]
            subagent_type = tc_args.get("subagent_type", "?") if isinstance(tc_args, dict) else "?"
            description = str(tc_args.get("description", "?"))[:100] if isinstance(tc_args, dict) else "?"

            logger.warning(
                "SubAgentResilience: %s task failed (async): %s: %s",
                subagent_type, error_type, error_msg[:100],
            )

            return ToolMessage(
                content=(
                    f"⚠️ Sub Agent '{subagent_type}' 执行失败\n"
                    f"错误: {error_type}: {error_msg}\n"
                    f"任务: {description}\n\n"
                    f"你可以:\n"
                    f"- 重试: 用相同或调整后的描述再次调用 task\n"
                    f"- 绕过: 跳过此任务继续后续步骤\n"
                    f"- 替代: 换其他 Sub Agent 或直接回答用户"
                ),
                tool_call_id=tc_id,
            )


class SessionArchiveMiddleware(AgentMiddleware):
    """Session archive middleware: archive key info to ChromaDB when session ends.

    Triggers when: Main Agent outputs final reply (AIMessage with no tool_calls).
    Behavior:
    1. Extract key conclusions from this conversation
    2. Write to ChromaDB (via save_memory tool logic)
    3. Optional: update AGENTS.md key memory
    """

    state_schema = AgentState

    def after_agent(self, state, runtime):
        """Execute after agent finishes (main agent final reply)."""
        messages = state.get("messages", [])
        if not messages:
            return None

        last_msg = messages[-1]

        # Only trigger on main Agent's final reply (AIMessage with no tool_calls)
        if not isinstance(last_msg, AIMessage):
            return None
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            return None

        # Archive key info
        try:
            self._archive_session(messages)
        except Exception as e:
            logger.warning("SessionArchive failed: %s", e)

        return None

    async def aafter_agent(self, state, runtime):
        """Async version of session archive."""
        messages = state.get("messages", [])
        if not messages:
            return None

        last_msg = messages[-1]
        if not isinstance(last_msg, AIMessage):
            return None
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            return None

        try:
            self._archive_session(messages)
        except Exception as e:
            logger.warning("SessionArchive failed (async): %s", e)

        return None

    def _archive_session(self, messages):
        """Archive session key info to ChromaDB."""
        from app.services.copilot.agent.memory_tools import _get_current_user_id, _get_collection

        user_id = _get_current_user_id()
        if not user_id or user_id == "default":
            return

        # Extract user messages and final reply
        user_msgs = [m for m in messages if hasattr(m, "type") and m.type == "human"]
        ai_final = [m for m in messages if hasattr(m, "type") and m.type == "ai"
                     and not (hasattr(m, "tool_calls") and m.tool_calls)]

        if not user_msgs or not ai_final:
            return

        # Save summary to ChromaDB
        try:
            collection = _get_collection(user_id)
            summary = f"会话摘要: 用户问: {user_msgs[-1].content[:200]} | 回答: {ai_final[-1].content[:300]}"
            collection.add(
                documents=[summary],
                metadatas=[{
                    "importance": "low",
                    "created_at": datetime.now().isoformat(),
                    "type": "session_archive",
                }],
                ids=[f"archive_{datetime.now().strftime('%Y%m%d%H%M%S')}"],
            )
            logger.info("Session archived for user %s", user_id)
        except Exception as e:
            logger.warning("Archive to ChromaDB failed: %s", e)
