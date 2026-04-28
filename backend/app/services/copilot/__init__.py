"""Copilot — DeepAgents 一主六从架构。

对外接口：run_copilot(), SSE stream, 工具注册
"""

from app.services.copilot.agent import get_deep_agent, reset_deep_agent
