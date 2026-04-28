"""智能对话中间件包。

Context防线:
- ResultOffloadMiddleware: 工具返回值>3000字符截断+写入backend
- SummarizationMiddleware: 框架原生，自定义参数+prompt（在 agent.py 中配置）

Memory:
- EarlyMessageArchiveMiddleware: messages>100时归档早期消息到ChromaDB

容错+安全:
- ToolErrorGuardMiddleware: 工具异常封装为友好ToolMessage
- SubAgentResilienceMiddleware: Sub Agent异常+后备保底重试
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
