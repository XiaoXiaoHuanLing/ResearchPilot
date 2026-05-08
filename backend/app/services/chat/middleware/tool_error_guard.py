"""Layer: 工具异常守护中间件。

通过 awrap_tool_call 环绕式监控所有工具调用，
工具异常自动封装为友好 ToolMessage，确保工具错误不会导致 Agent 崩溃。
Agent 收到分类错误+建议动作后可自主决定下一步。

注：on_tool_error 不是 AgentMiddleware 的框架接口，框架不会自动调用。
必须使用 awrap_tool_call 才能被框架正确执行。
"""

import logging

from langchain.agents.middleware.types import AgentMiddleware
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


class ToolErrorGuardMiddleware(AgentMiddleware):
    """工具异常守护：awrap_tool_call 环绕式监控，工具异常封装为友好 ToolMessage。"""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler,
    ) -> ToolMessage:
        """拦截工具调用，捕获异常后封装为友好提示+建议动作。"""
        try:
            result = await handler(request)
            return result
        except Exception as e:
            tool_name = request.tool_call.get("name", "unknown")
            error_msg = str(e)[:200]
            logger.warning("ToolErrorGuard: %s error: %s", tool_name, error_msg)

            suggestion = self._suggest_action(e, tool_name)
            friendly_msg = (
                f"[{tool_name}] 执行出错: {error_msg}\n"
                f"建议: {suggestion}"
            )

            # 封装为 ToolMessage，Agent 可以理解并自主决策下一步
            return ToolMessage(
                content=friendly_msg,
                tool_call_id=request.tool_call.get("id", ""),
                name=tool_name,
                status="error",
            )

    def _suggest_action(self, error: Exception, tool_name: str) -> str:
        """根据错误类型和工具名给出建议"""
        error_msg = str(error).lower()

        # 网络错误
        if any(kw in error_msg for kw in ["timeout", "connection", "network", "connect"]):
            if tool_name in ("search_web", "fetch_page"):
                return "网络连接失败，可以尝试稍后重试或换用知识库检索"
            return "网络连接失败，请稍后重试"

        # API 额度/限制
        if any(kw in error_msg for kw in ["rate limit", "quota", "429", "too many"]):
            return "API 调用次数已达上限，请停止调用此工具，用已有信息回答"

        # 数据库错误
        if any(kw in error_msg for kw in ["database", "sqlite", "chroma", "collection"]):
            if tool_name in ("search_knowledge", "get_recall_nodes", "list_active_kbs"):
                return "知识库服务暂时不可用，尝试改用联网搜索"
            return "数据服务暂时不可用，请稍后重试"

        # 参数错误
        if any(kw in error_msg for kw in ["valueerror", "typeerror", "invalid"]):
            return "参数格式有误，请检查输入参数后重试"

        # 上下文溢出
        if any(kw in error_msg for kw in ["context", "token", "length", "overflow"]):
            return "上下文过长，请精简输入内容"

        # 默认建议
        return "请检查输入参数，或尝试其他工具完成任务"
