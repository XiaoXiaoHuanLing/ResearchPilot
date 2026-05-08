"""Layer: Sub Agent 容错中间件。

通过 awrap_tool_call 环绕式监控 Sub Agent 工具调用（sub_agent_* 类工具），
Sub Agent 执行异常时自动后备保底（换策略降级到单工具，而非盲目重试）。

后备映射：
- retriever 失败 → 直接调 search_knowledge 做一次基础检索 → 保底结果
- searcher 失败  → 直接调 search_web 做一次基础搜索 → 保底结果

设计要点：
- 后备是换策略（从 Sub Agent 降级到单工具），比 LangGraph retry（重试同一个节点）更有可能成功
- on_sub_agent_error / on_tool_error 不是框架接口，必须用 awrap_tool_call

设计参考：docs/specs/2026-04-27-chat-module-redesign.md §四 4.2
"""

import logging

from langchain.agents.middleware.types import AgentMiddleware
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


class SubAgentResilienceMiddleware(AgentMiddleware):
    """Sub Agent 容错：awrap_tool_call 环绕监控，Sub Agent 异常时后备保底。"""

    # 后备工具映射：Sub Agent 失败时用哪个工具做保底检索
    FALLBACK_TOOLS = {
        "retriever": "search_knowledge",
        "searcher": "search_web",
    }

    def __init__(self, llm_with_fallbacks=None):
        """初始化。

        Args:
            llm_with_fallbacks: 带 fallback 的 LLM chain，保留引用供未来 LLM 级 fallback。
        """
        self._llm_with_fallbacks = llm_with_fallbacks

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler,
    ) -> ToolMessage:
        """拦截工具调用，Sub Agent 异常时尝试后备保底。"""
        tool_name = request.tool_call.get("name", "unknown")

        # 只处理 Sub Agent 相关的工具调用（deepagents 框架用 delegate_task 等工具调用 Sub Agent）
        is_sub_agent_call = tool_name.startswith("sub_agent_") or tool_name in ("delegate_task", "delegate")

        try:
            result = await handler(request)
            return result
        except Exception as e:
            error_msg = str(e)[:200]
            logger.warning("SubAgentResilience: tool %s failed: %s", tool_name, error_msg)

            if not is_sub_agent_call:
                # 非 Sub Agent 工具的异常，简单封装返回
                return ToolMessage(
                    content=f"[{tool_name}] 执行出错: {error_msg}",
                    tool_call_id=request.tool_call.get("id", ""),
                    name=tool_name,
                    status="error",
                )

            # Sub Agent 失败 → 尝试后备保底
            # 从错误信息中推断是哪个 Sub Agent 失败
            sub_name = self._infer_sub_agent(tool_name, error_msg)
            fallback_tool = self.FALLBACK_TOOLS.get(sub_name)
            fallback_result = None

            if fallback_tool:
                try:
                    fallback_result = await self._run_fallback(fallback_tool, request)
                    if fallback_result:
                        logger.info("SubAgentResilience: %s fallback succeeded with %s", sub_name, fallback_tool)
                except Exception as fb_err:
                    logger.warning("SubAgentResilience: fallback %s also failed: %s", fallback_tool, fb_err)

            # 构建返回消息
            parts = [f"[{sub_name}] 执行失败: {error_msg}"]

            if fallback_result:
                parts.append(f"后备方案({fallback_tool})执行成功，以下为保底结果:")
                parts.append(fallback_result[:2000])
            else:
                parts.append("建议: 检查输入参数是否正确，或尝试其他方式完成任务。")

            return ToolMessage(
                content="\n".join(parts),
                tool_call_id=request.tool_call.get("id", ""),
                name=tool_name,
                status="error",
            )

    def _infer_sub_agent(self, tool_name: str, error_msg: str) -> str:
        """从工具名和错误信息推断是哪个 Sub Agent 失败。"""
        # 直接匹配工具名
        if "retriever" in tool_name.lower() or "retriev" in error_msg.lower():
            return "retriever"
        if "searcher" in tool_name.lower() or "search" in error_msg.lower():
            return "searcher"
        # 默认返回 retriever（更常见）
        return "retriever"

    async def _run_fallback(self, tool_name: str, original_request: ToolCallRequest) -> str | None:
        """执行后备工具做保底检索。

        从原始请求中提取查询参数，调用指定工具。
        """
        try:
            # 从原始请求的 tool_call args 中提取查询关键词
            args = original_request.tool_call.get("args", {})
            query = ""
            if isinstance(args, dict):
                # 尝试从参数中提取查询
                query = args.get("query", args.get("task", args.get("description", "")))
            if isinstance(query, str) and len(query) > 5:
                query = query[:100]  # 截断避免过长
            else:
                query = "fallback search"  # 无法提取则用默认

            # 直接调用对应工具
            from app.services.chat.tools.thread_context import get_current_thread_id

            if tool_name == "search_knowledge":
                from app.services.chat.tools.reflexive_retriever import _search_knowledge_impl
                result = await _search_knowledge_impl(query, top_k=3, thread_id=get_current_thread_id())
                return str(result)
            elif tool_name == "search_web":
                from app.services.chat.tools.search import _search_web_impl
                result = await _search_web_impl(query, max_results=3, thread_id=get_current_thread_id())
                return str(result)

            return None
        except Exception as e:
            logger.warning("SubAgentResilience fallback execution failed: %s", e)
            return None
