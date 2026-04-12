"""上下文文件工具 — write_context_file, read_context_file

虚拟文件系统：读写 Agent 状态中的 context_files dict。
"""
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _write_context_file_impl(filename: str, content: str) -> str:
    return f"✅ 已写入 {filename} ({len(content)} 字符)"


@tool
def write_context_file(filename: str, content: str) -> str:
    """将内容写入虚拟文件系统（工作记忆）。用于保存中间结果、计划等。"""
    return _write_context_file_impl(filename, content)


def _read_context_file_impl(filename: str) -> str:
    return f"（读取 {filename} — 内容在状态中）"


@tool
def read_context_file(filename: str) -> str:
    """从虚拟文件系统读取文件内容。"""
    return _read_context_file_impl(filename)


TOOLS = [write_context_file, read_context_file]

FUNC_MAP = {
    "write_context_file": _write_context_file_impl,
    "read_context_file": _read_context_file_impl,
}
