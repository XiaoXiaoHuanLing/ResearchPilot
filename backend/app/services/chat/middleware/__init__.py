"""智能对话中间件包。

Context防线（三层，按粒度递增）:
- ResultOffloadMiddleware: awrap_tool_call，工具返回值>3000字符截断+写入backend(/results/offloaded/)
- SummarizationMiddleware: 框架原生，自定义 trigger/keep/prompt（在 agent.py 中后置 patch）
- EarlyMessageArchiveMiddleware: pre_process，messages>100时归档早期消息到ChromaDB

容错+安全（三级）:
- ToolErrorGuardMiddleware: awrap_tool_call，工具异常分类+建议动作（给Agent看）
- SubAgentResilienceMiddleware: awrap_tool_call，Sub Agent异常+后备保底（换策略不重试）

注：所有自定义中间件均使用 awrap_tool_call（框架标准接口），
    on_tool_end / on_tool_error / on_sub_agent_error 不是框架回调，不会被调用。
"""

from app.services.chat.middleware.result_offload import ResultOffloadMiddleware
from app.services.chat.middleware.early_archive import EarlyMessageArchiveMiddleware
from app.services.chat.middleware.tool_error_guard import ToolErrorGuardMiddleware
from app.services.chat.middleware.sub_agent_resilience import SubAgentResilienceMiddleware

__all__ = [
    "ResultOffloadMiddleware",
    "EarlyMessageArchiveMiddleware",
    "ToolErrorGuardMiddleware",
    "SubAgentResilienceMiddleware",
]
