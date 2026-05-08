"""Layer 1: 大结果自动卸载中间件。

工具返回值超阈值时截断 + 写入 backend 文件系统。
核心思想：工具返回大文本会直接撑爆 messages 上下文。
截断到安全长度，完整内容写入 backend 路径（Agent 可通过 read_file 读取）。

设计要点：
- 使用 awrap_tool_call 拦截工具调用结果（on_tool_end 不是框架回调，不会自动执行）
- 写入 /results/offloaded/ 路径（CompositeBackend 的 StateBackend 路由）
- Agent 可通过框架内置 read_file 工具按需读取卸载内容
- 走权限控制：/results/** 读+写均 allow，Agent 可读可写

覆盖范围：
- 对所有工具统一生效
- RecallPool/SearchPool 工具自身已控制输出长度，一般不会触发
- 主要覆盖：get_recall_nodes（一次读取太多节点）、get_search_content、其他可能返回大结果的工具
"""

import logging
import uuid

from langchain.agents.middleware.types import AgentMiddleware
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

# 卸载路径前缀（走 CompositeBackend 的 /results/ FilesystemBackend 路由）
# CompositeBackend 去掉 /results/ 前缀后，offloaded/xxx.md 对应物理目录 storage/offloaded/offloaded/xxx.md
# 所以路径设计为 /results/offloaded/，物理目录 = root_dir + offloaded/
OFFLOAD_PATH_PREFIX = "/results/offloaded"


class ResultOffloadMiddleware(AgentMiddleware):
    """大结果自动卸载：工具返回值超阈值时截断 + 写入 backend。

    卸载内容写入 /results/offloaded/ 路径，Agent 可通过框架 read_file 按需读取。
    """

    MAX_OUTPUT_CHARS = 3000  # 超过此长度自动卸载

    def __init__(self, max_output_chars: int = 3000, backend=None):
        self.MAX_OUTPUT_CHARS = max_output_chars
        self._backend = backend  # CompositeBackend 引用，由 agent.py 注入

    async def _offload_to_backend(self, file_name: str, content: str) -> str | None:
        """通过 backend 异步写入卸载文件。返回写入路径或 None。"""
        if self._backend is None:
            return None
        try:
            file_path = f"{OFFLOAD_PATH_PREFIX}/{file_name}"
            result = await self._backend.awrite(file_path, content)
            if result.error:
                logger.warning("ResultOffload backend awrite failed: %s", result.error)
                return None
            return file_path
        except AttributeError:
            # backend 没有 awrite，尝试同步 write
            try:
                file_path = f"{OFFLOAD_PATH_PREFIX}/{file_name}"
                result = self._backend.write(file_path, content)
                if result.error:
                    logger.warning("ResultOffload backend write failed: %s", result.error)
                    return None
                return file_path
            except Exception as e:
                logger.warning("ResultOffload backend write exception: %s", e)
                return None
        except Exception as e:
            logger.warning("ResultOffload backend awrite exception: %s", e)
            return None

    def _build_response(self, content: str, truncated: str, offload_path: str | None) -> str:
        """构建返回给 Agent 的内容"""
        if offload_path:
            return (
                truncated + "\n\n"
                f"[结果过长已截断，完整内容已卸载，可通过 read_file 读取]\n"
                f"卸载路径: {offload_path}\n"
                f"原始长度: {len(content)} 字符 | 截断保留: {self.MAX_OUTPUT_CHARS} 字符"
            )
        else:
            # 降级：仅截断，无法卸载（Agent 无法恢复完整内容）
            return truncated + "\n\n[结果过长已截断，卸载写入失败]"

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler,
    ) -> ToolMessage:
        """拦截工具调用结果，超长时截断+卸载到 backend。

        使用 awrap_tool_call 确保框架能正确调用（on_tool_end 不是框架接口）。
        """
        # 先执行工具
        result = await handler(request)

        # 只处理 ToolMessage 类型的结果
        if not isinstance(result, ToolMessage):
            return result

        tool_name = request.tool_call.get("name", "unknown")
        content = result.content

        # 只处理文本内容
        if not isinstance(content, str):
            return result

        if len(content) <= self.MAX_OUTPUT_CHARS:
            return result

        # 超长 → 截断 + 卸载
        truncated = content[:self.MAX_OUTPUT_CHARS]
        file_id = uuid.uuid4().hex[:8]
        file_name = f"{tool_name}_{file_id}.md"
        full_content = f"# Offloaded: {tool_name}\n\n{content}"

        offload_path = await self._offload_to_backend(file_name, full_content)

        logger.info(
            "ResultOffload: %s output %d chars -> truncated to %d, offload_path=%s",
            tool_name, len(content), self.MAX_OUTPUT_CHARS, offload_path or "FAILED",
        )

        # 构建新的 ToolMessage（保留原有元数据，只替换 content）
        new_content = self._build_response(content, truncated, offload_path)
        return ToolMessage(
            content=new_content,
            tool_call_id=result.tool_call_id,
            name=result.name,
            id=result.id,
            artifact=result.artifact,
            status=result.status,
        )
