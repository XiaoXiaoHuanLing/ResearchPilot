"""主Agent基础工具：get_current_time, list_active_kbs"""

from langchain_core.tools import tool


@tool
def get_current_time() -> str:
    """获取当前时间、日期和时区信息。

    用于判断信息时效性，搜索时加入时间限定词。
    """
    from datetime import datetime
    import pytz

    tz = pytz.timezone("Asia/Shanghai")
    now = datetime.now(tz)
    weekdays = ['一', '二', '三', '四', '五', '六', '日']
    return (
        f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"时区：Asia/Shanghai (UTC+8)\n"
        f"星期：{weekdays[now.weekday()]}\n"
        f"地区：中国"
    )


@tool
def list_active_kbs() -> str:
    """查询当前启用的知识库列表。

    返回所有启用状态的知识库：ID、名称、文档数量、chunk数。
    无可用知识库时返回提示，主Agent可据此跳过KB检索。
    """
    from app.db.session import SessionLocal
    from app.db.models import KnowledgeBaseModel

    try:
        with SessionLocal() as db:
            kbs = db.query(KnowledgeBaseModel).filter(
                KnowledgeBaseModel.enabled == True  # noqa
            ).all()

            if not kbs:
                return "当前无启用的知识库，知识库检索将被跳过。"

            lines = []
            for kb in kbs:
                lines.append(f"- KB#{kb.id} {kb.name} (类型:{kb.kb_type}, 文档:{kb.document_count}, chunks:{kb.chunk_count})")
            return "可用知识库：\n" + "\n".join(lines)
    except Exception as e:
        return f"查询知识库列表失败: {str(e)[:100]}"
