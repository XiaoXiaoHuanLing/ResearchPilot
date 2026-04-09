from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import TopicModel
from app.db.session import get_db
from app.schemas.topic import TopicCreate, TopicRead, TopicUpdate

router = APIRouter()


def _to_read_model(topic: TopicModel) -> TopicRead:
    return TopicRead(
        id=topic.id,
        name=topic.name,
        description=topic.description,
        keywords=[item.strip() for item in topic.keywords.split(",") if item.strip()],
        schedule=topic.schedule,
        enabled=topic.enabled,
    )


def _get_topic_or_404(topic_id: int, db: Session) -> TopicModel:
    topic = db.query(TopicModel).filter(TopicModel.id == topic_id).first()
    if topic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")
    return topic


def _sync_scheduler(topic: TopicModel, action: str = "schedule") -> None:
    """Sync topic schedule to APScheduler."""
    try:
        from app.services.scheduler import schedule_topic, unschedule_topic
        if action == "schedule" and topic.enabled:
            schedule_topic(topic.id, topic.name, topic.schedule, topic.keywords)
        else:
            unschedule_topic(topic.id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Scheduler sync failed for topic %d: %s", topic.id, e)


@router.get("", response_model=list[TopicRead])
def list_topics(db: Session = Depends(get_db)):
    topics = db.query(TopicModel).order_by(TopicModel.id.desc()).all()
    return [_to_read_model(topic) for topic in topics]


@router.post("", response_model=TopicRead, status_code=status.HTTP_201_CREATED)
def create_topic(payload: TopicCreate, db: Session = Depends(get_db)):
    topic = TopicModel(
        name=payload.name,
        description=payload.description,
        keywords=",".join(payload.keywords),
        schedule=payload.schedule,
        enabled=payload.enabled,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    _sync_scheduler(topic, "schedule")
    return _to_read_model(topic)


@router.put("/{topic_id}", response_model=TopicRead)
def update_topic(topic_id: int, payload: TopicUpdate, db: Session = Depends(get_db)):
    topic = _get_topic_or_404(topic_id, db)
    topic.name = payload.name
    topic.description = payload.description
    topic.keywords = ",".join(payload.keywords)
    topic.schedule = payload.schedule
    topic.enabled = payload.enabled
    db.commit()
    db.refresh(topic)
    _sync_scheduler(topic, "schedule" if topic.enabled else "unschedule")
    return _to_read_model(topic)


@router.delete("/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_topic(topic_id: int, db: Session = Depends(get_db)):
    topic = _get_topic_or_404(topic_id, db)
    _sync_scheduler(topic, "unschedule")
    db.delete(topic)
    db.commit()
