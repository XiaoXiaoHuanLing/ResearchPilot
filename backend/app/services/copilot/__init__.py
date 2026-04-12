"""Copilot 智能助手模块。

架构：tools/ + llm/ + agent/ 解耦

公开接口（从 agent 层导出）：
- get_compiled_agent(): 获取预构建 ReAct Agent
- run_copilot(message, thread_id): 非流式对话
"""

from app.services.copilot.agent import get_compiled_agent, run_copilot
