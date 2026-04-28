"""Layer 1: 大结果自动卸载中间件。

工具返回值超阈值时截断 + 写入本地文件卸载。
核心思想：工具返回大文本会直接撑爆 messages 上下文。
截断到安全长度，完整内容写入文件（可事后查询）。

覆盖范围：
- 对所有工具统一生效
- RecallPool/SearchPool 工具自身已控制输出长度，一般不会触发
- 主要覆盖：get_recall_nodes（一次读取太多节点）、get_search_content、其他可能返回大结果的工具
"""

import logging
import uuid
from pathlib import Path

from langchain.agents.middleware.types import AgentMiddleware

from app.core.config import settings

logger = logging.getLogger(__name__)


class ResultOffloadMiddleware(AgentMiddleware):
    """大结果自动卸载：工具返回值超阈值时截断 + 写入文件。"""

    MAX_OUTPUT_CHARS = 3000  # 超过此长度自动卸载

    def __init__(self, max_output_chars: int = 3000):
        self.MAX_OUTPUT_CHARS = max_output_chars

    async def on_tool_end(self, output, tool_name, runtime) -> str:
        """工具返回后，如果超长则截断 + 卸载"""
        content = str(output)

        if len(content) <= self.MAX_OUTPUT_CHARS:
            return content

        # 截断
        truncated = content[:self.MAX_OUTPUT_CHARS]

        # 写入文件作为卸载存储
        try:
            offload_dir = Path(settings.storage_base_dir) / "offloaded"
            offload_dir.mkdir(parents=True, exist_ok=True)

            file_id = uuid.uuid4().hex[:8]
            offload_path = offload_dir / f"{tool_name}_{file_id}.md"

            with open(offload_path, "w", encoding="utf-8") as f:
                f.write(f"# Offloaded: {tool_name}\n\n")
                f.write(content)

            logger.info(
                "ResultOffload: %s output %d chars -> truncated to %d, full at %s",
                tool_name, len(content), self.MAX_OUTPUT_CHARS, offload_path.name,
            )

            # 返回截断内容 + 卸载路径提示
            return (
                truncated + "\n\n"
                f"[结果过长已截断，完整内容已卸载到 {offload_path.name}]\n"
                f"原始长度: {len(content)} 字符 | 截断保留: {self.MAX_OUTPUT_CHARS} 字符"
            )
        except Exception as e:
            logger.error("ResultOffload write failed: %s", e)
            # 降级：仅截断，不卸载
            return content[:self.MAX_OUTPUT_CHARS] + "\n\n[结果过长已截断]"
