"""Copilot 工具包 — 按功能域拆分。

每个工具文件定义 @tool 函数 + FUNC_MAP (name→callable)。
ALL_TOOLS: 所有 LangChain Tool 对象（用于 bind_tools）
TOOL_FUNC_MAP: name → 实际执行函数（解决 async @tool 的 func=None 问题）
"""

# Lazy imports — only access ALL_TOOLS / TOOL_FUNC_MAP when needed
# This allows individual tool modules to be developed independently

def _load():
    from .search import TOOLS as _st, FUNC_MAP as _sf
    from .rag import TOOLS as _rt, FUNC_MAP as _rf
    from .topic import TOOLS as _tt, FUNC_MAP as _tf
    from .article import TOOLS as _at, FUNC_MAP as _af
    from .knowledge_base import TOOLS as _kt, FUNC_MAP as _kf
    from .report import TOOLS as _pt, FUNC_MAP as _pf
    from .system import TOOLS as _syt, FUNC_MAP as _syf
    from .context import TOOLS as _ct, FUNC_MAP as _cf

    all_tools = _st + _rt + _tt + _at + _kt + _pt + _syt + _ct
    func_map = {}
    for fm in [_sf, _rf, _tf, _af, _kf, _pf, _syf, _cf]:
        func_map.update(fm)
    return all_tools, func_map


_all_tools = None
_func_map = None


def get_all_tools():
    global _all_tools
    if _all_tools is None:
        _all_tools, _func_map = _load()
    return _all_tools


def get_tool_func_map():
    global _func_map
    if _func_map is None:
        _all_tools, _func_map = _load()
    return _func_map


ALL_TOOLS = property(lambda self: get_all_tools())
TOOL_FUNC_MAP = property(lambda self: get_tool_func_map())
