"""统一的会话线程上下文。

被 RecallPool / SearchPool 共享，确保同一个 thread_id 贯穿一次请求。

使用模块级全局变量而非 contextvars。
原因：LangChain @tool 的 ainvoke 在新 context 中执行，
contextvars 值不会传播，导致 get_current_thread_id() 返回空串。
在单用户/低并发场景下，全局变量更可靠。
如果未来需要并发安全，可改用 async-local storage 或 LangGraph 的 state 传递。
"""

_current_thread_id = ""


def set_current_thread_id(thread_id: str) -> None:
    """设置当前请求的 thread_id"""
    global _current_thread_id
    _current_thread_id = thread_id


def get_current_thread_id() -> str:
    """获取当前请求的 thread_id"""
    return _current_thread_id


def clear_current_thread_id() -> None:
    """清除当前 thread_id"""
    global _current_thread_id
    _current_thread_id = ""
