"""Layer: Sub Agent 容错中间件。

捕获 Sub Agent 执行异常 + 后备 agent 保底重试。
retriever 后备 = 单次 search_knowledge
searcher 后备 = 单次 search_web

设计参考：docs/specs/2026-04-27-chat-module-redesign.md §四 4.2
"""

import logging

from langchain.agents.middleware.types import AgentMiddleware

logger = logging.getLogger(__name__)


class SubAgentResilienceMiddleware(AgentMiddleware):
    """Sub Agent 容错：捕获异常 + 后备保底重试 + LLM fallback。"""

    # 后备工具映射：Sub Agent 失败时用哪个工具做保底检索
    FALLBACK_TOOLS = {
        "retriever": "search_knowledge",
        "searcher": "search_web",
    }

    MAX_RETRIES = 1  # 后备重试最多1次

    def __init__(self, llm_with_fallbacks=None):
        """初始化。
        
        Args:
            llm_with_fallbacks: 带 fallback 的 LLM chain，用于 LLM 级别的 fallback。
        """
        self._llm_with_fallbacks = llm_with_fallbacks

    async def on_sub_agent_error(self, error: Exception, sub_name: str, runtime) -> str:
        """Sub Agent 执行失败时，返回友好报告 + 尝试后备方案。"""
        error_msg = str(error)[:200]
        logger.warning("SubAgentResilience: %s failed: %s", sub_name, error_msg)

        # 尝试后备保底
        fallback_tool = self.FALLBACK_TOOLS.get(sub_name)
        fallback_result = None

        if fallback_tool:
            try:
                fallback_result = await self._run_fallback(fallback_tool, sub_name, runtime)
                if fallback_result:
                    logger.info("SubAgentResilience: %s fallback succeeded with %s", sub_name, fallback_tool)
            except Exception as fb_err:
                logger.warning("SubAgentResilience: fallback %s also failed: %s", fallback_tool, fb_err)

        # 构建返回消息
        parts = [
            f"[{sub_name}] 执行失败: {error_msg}",
        ]

        if fallback_result:
            parts.append(f"后备方案({fallback_tool})执行成功，以下为保底结果:")
            parts.append(fallback_result[:2000])
        else:
            parts.append("建议: 检查输入参数是否正确，或尝试其他Sub Agent。")

        return "\n".join(parts)

    async def _run_fallback(self, tool_name: str, sub_name: str, runtime) -> str | None:
        """执行后备工具做保底检索。

        从 runtime 中获取工具执行器，调用指定工具。
        """
        try:
            # 尝试通过 runtime 的工具注册表调用
            # 不同框架版本接口可能不同，做兼容处理
            if hasattr(runtime, "tools") and runtime.tools:
                for t in runtime.tools:
                    if hasattr(t, "name") and t.name == tool_name:
                        # 构造简单调用
                        if tool_name == "search_knowledge":
                            result = await t.ainvoke({"query": "fallback search", "top_k": 3})
                            return str(result)
                        elif tool_name == "search_web":
                            result = await t.ainvoke({"query": "fallback search", "max_results": 3})
                            return str(result)
                        break

            # 如果 runtime 不支持直接调用，返回 None
            return None
        except Exception as e:
            logger.warning("SubAgentResilience fallback execution failed: %s", e)
            return None

    async def on_tool_error(self, error: Exception, tool_name: str, runtime) -> str:
        """工具级错误（由 ToolErrorGuardMiddleware 优先处理，这里是兜底）"""
        error_msg = str(error)[:200]
        logger.warning("SubAgentResilience tool error: %s: %s", tool_name, error_msg)

        if "context" in error_msg.lower() or "token" in error_msg.lower() or "length" in error_msg.lower():
            return "CONTEXT_OVERFLOW"

        return f"[{tool_name}] 执行出错: {error_msg}"

    async def on_llm_error(self, error: Exception, runtime) -> str:
        """LLM 调用失败"""
        error_msg = str(error)[:200]
        logger.error("SubAgentResilience LLM error: %s", error_msg)

        if any(kw in error_msg.lower() for kw in ["context", "token", "length"]):
            return "CONTEXT_OVERFLOW"

        return f"LLM调用失败: {error_msg}"
