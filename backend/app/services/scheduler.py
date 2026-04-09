"""Scheduler service using APScheduler for topic-based periodic collection.

Each enabled topic has a schedule (e.g. "每天 09:00") that determines
when collection jobs should run. In the MVP, jobs log their execution;
real web fetching will be added in the ingestion pipeline phase.
"""

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings

logger = logging.getLogger(__name__)

scheduler: BackgroundScheduler | None = None


def _parse_schedule(schedule_str: str) -> dict:
    """Parse a human-readable schedule string into cron kwargs.

    Supports patterns like:
    - "每天 09:00" → hour=9, minute=0
    - "每天 08:00 / 20:00" → two triggers (handled by caller)
    - "每周一 09:00" → day_of_week=mon, hour=9
    """
    schedule_str = schedule_str.strip()
    parts = schedule_str.replace("每天", "").replace("每周一", "mon").replace("每周二", "tue").split()

    # Simple parsing for MVP
    if "每天" in schedule_str or schedule_str.startswith("每天") or not any(d in schedule_str for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]):
        # Daily schedule
        times = []
        for part in schedule_str.split("/"):
            part = part.strip()
            for segment in part.split():
                if ":" in segment:
                    try:
                        h, m = segment.split(":")
                        times.append({"hour": int(h), "minute": int(m)})
                    except (ValueError, IndexError):
                        pass
        if not times:
            times = [{"hour": 9, "minute": 0}]
        return {"type": "daily", "times": times}
    else:
        # Weekly or custom - fallback to daily 09:00 for MVP
        return {"type": "daily", "times": [{"hour": 9, "minute": 0}]}


def _collection_job(topic_id: int, topic_name: str, keywords_csv: str) -> None:
    """Scheduled collection job for a topic.

    1. Parse keywords
    2. Run ingestion pipeline
    3. Log results
    """
    import asyncio
    from app.services.ingestion import run_topic_collection

    keywords = [k.strip() for k in keywords_csv.split(",") if k.strip()]
    logger.info(
        "[Scheduler] Collection job triggered for topic '%s' (id=%d) at %s",
        topic_name, topic_id, datetime.now().isoformat(),
    )
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're inside an async loop, create a task
            asyncio.ensure_future(run_topic_collection(topic_id, topic_name, keywords))
        else:
            loop.run_until_complete(run_topic_collection(topic_id, topic_name, keywords))
    except RuntimeError:
        # No event loop, create one
        asyncio.run(run_topic_collection(topic_id, topic_name, keywords))


def schedule_topic(topic_id: int, topic_name: str, schedule: str, keywords_csv: str = "") -> None:
    """Schedule collection jobs for a topic."""
    if scheduler is None:
        logger.warning("Scheduler not initialized, cannot schedule topic %d", topic_id)
        return

    parsed = _parse_schedule(schedule)
    job_prefix = f"topic_collect_{topic_id}"

    # Remove existing jobs for this topic
    existing = scheduler.get_jobs()
    for job in existing:
        if job.id.startswith(job_prefix):
            scheduler.remove_job(job.id)

    if parsed["type"] == "daily":
        for i, time_cfg in enumerate(parsed["times"]):
            job_id = f"{job_prefix}_{i}"
            trigger = CronTrigger(
                hour=time_cfg["hour"],
                minute=time_cfg["minute"],
            )
            scheduler.add_job(
                _collection_job,
                trigger=trigger,
                id=job_id,
                args=[topic_id, topic_name, keywords_csv],
                replace_existing=True,
            )
            logger.info("Scheduled job %s: daily at %02d:%02d for topic '%s'", job_id, time_cfg["hour"], time_cfg["minute"], topic_name)


def unschedule_topic(topic_id: int) -> None:
    """Remove all scheduled jobs for a topic."""
    if scheduler is None:
        return

    job_prefix = f"topic_collect_{topic_id}"
    existing = scheduler.get_jobs()
    for job in existing:
        if job.id.startswith(job_prefix):
            scheduler.remove_job(job.id)


def init_scheduler() -> None:
    """Initialize and start the background scheduler."""
    global scheduler

    if scheduler is not None:
        return

    scheduler = BackgroundScheduler()
    scheduler.start()
    logger.info("APScheduler started")

    # Load enabled topics from DB and schedule them
    try:
        from app.db.session import SessionLocal
        from app.db.models import TopicModel

        with SessionLocal() as db:
            topics = db.query(TopicModel).filter(TopicModel.enabled == True).all()  # noqa: E712
            for topic in topics:
                schedule_topic(topic.id, topic.name, topic.schedule, topic.keywords)
    except Exception as e:
        logger.error("Failed to load initial topic schedules: %s", e)


def shutdown_scheduler() -> None:
    """Gracefully shutdown the scheduler."""
    global scheduler
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        scheduler = None
        logger.info("APScheduler shutdown")
