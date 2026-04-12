"""专题工具 — list_topics, create_topic, update_topic, delete_topic

⚠️ list_topics 的 keywords 修复：TopicModel.keywords 是逗号分隔字符串，不是 JSON。
"""
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _list_topics_impl() -> str:
    from app.db.session import SessionLocal
    from app.db.models import TopicModel

    with SessionLocal() as db:
        topics = db.query(TopicModel).order_by(TopicModel.id).all()

    if not topics:
        return "暂无专题。你可以让我新建一个。"

    lines = []
    for t in topics:
        # keywords is comma-separated string, NOT JSON
        kws = [k.strip() for k in (t.keywords or "").split(",") if k.strip()]
        enabled = "🟢" if t.enabled else "🔴"
        lines.append(f"  {enabled} [{t.id}] {t.name} — 关键词: {', '.join(kws)} | 调度: {t.schedule or '无'}")

    return f"📋 共 {len(topics)} 个专题：\n" + "\n".join(lines)


@tool
def list_topics() -> str:
    """列出所有研究专题及其关键词和采集计划。"""
    return _list_topics_impl()


def _create_topic_impl(name: str, keywords: list[str], description: str = "", schedule: str = "", enabled: bool = True) -> str:
    from app.db.session import SessionLocal
    from app.db.models import TopicModel

    # Convert keywords list to comma-separated string for DB storage
    keywords_csv = ",".join(keywords)

    with SessionLocal() as db:
        topic = TopicModel(
            name=name, description=description,
            keywords=keywords_csv, schedule=schedule, enabled=enabled,
        )
        db.add(topic)
        db.commit()
        db.refresh(topic)

    return f"✅ 专题「{name}」已创建 (ID={topic.id})，关键词: {', '.join(keywords)}"


@tool
def create_topic(name: str, keywords: list[str], description: str = "", schedule: str = "", enabled: bool = True) -> str:
    """新建研究专题。

    参数：
    - name: 专题名称
    - keywords: 关键词列表
    - description: 描述
    - schedule: cron 调度表达式
    - enabled: 是否启用定时采集
    """
    return _create_topic_impl(name, keywords, description, schedule, enabled)


def _update_topic_impl(topic_id: int, name: str | None = None, keywords: list[str] | None = None,
                       description: str | None = None, schedule: str | None = None,
                       enabled: bool | None = None) -> str:
    from app.db.session import SessionLocal
    from app.db.models import TopicModel

    with SessionLocal() as db:
        topic = db.query(TopicModel).filter(TopicModel.id == topic_id).first()
        if not topic:
            return f"❌ 未找到专题 ID={topic_id}"

        if name is not None:
            topic.name = name
        if keywords is not None:
            topic.keywords = ",".join(keywords)
        if description is not None:
            topic.description = description
        if schedule is not None:
            topic.schedule = schedule
        if enabled is not None:
            topic.enabled = enabled
        db.commit()
        # Capture name before session closes
        updated_name = topic.name

    return f"✅ 专题「{updated_name}」已更新"


@tool
def update_topic(topic_id: int, name: str | None = None, keywords: list[str] | None = None,
                 description: str | None = None, schedule: str | None = None,
                 enabled: bool | None = None) -> str:
    """更新研究专题信息。只传需要修改的字段。"""
    return _update_topic_impl(topic_id, name, keywords, description, schedule, enabled)


def _delete_topic_impl(topic_id: int) -> str:
    from app.db.session import SessionLocal
    from app.db.models import TopicModel

    with SessionLocal() as db:
        topic = db.query(TopicModel).filter(TopicModel.id == topic_id).first()
        if not topic:
            return f"❌ 未找到专题 ID={topic_id}"
        name = topic.name
        db.delete(topic)
        db.commit()

    return f"✅ 专题「{name}」已删除"


@tool
def delete_topic(topic_id: int) -> str:
    """删除研究专题。⚠️ 会删除该专题下的所有文章！"""
    return _delete_topic_impl(topic_id)


TOOLS = [list_topics, create_topic, update_topic, delete_topic]

FUNC_MAP = {
    "list_topics": _list_topics_impl,
    "create_topic": _create_topic_impl,
    "update_topic": _update_topic_impl,
    "delete_topic": _delete_topic_impl,
}
