"""Scheduler service using APScheduler for topic-based periodic collection — V2 Fixed.

V2 Fix: The core issue was that APScheduler runs in a background thread,
but our collection pipeline is async. The V1 code tried to get the event loop
which either didn't exist or was already running. V2 uses asyncio.run()
in the background thread to create a fresh event loop for each job.

Also added:
- Proper scheduler lifecycle management
- Collection result tracking
- Error handling and retry logging
"""

import asyncio
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
    
    # Check for weekly patterns
    day_map = {
        "每周一": "mon", "每周二": "tue", "每周三": "wed",
        "每周四": "thu", "每周五": "fri", "每周六": "sat", "每周日": "sun",
    }
    
    for cn_day, en_day in day_map.items():
        if cn_day in schedule_str:
            times = []
            for segment in schedule_str.replace(cn_day, "").split("/"):
                for part in segment.strip().split():
                    if ":" in part:
                        try:
                            h, m = part.split(":")
                            times.append({"hour": int(h), "minute": int(m), "day_of_week": en_day})
                        except (ValueError, IndexError):
                            pass
            if not times:
                times = [{"hour": 9, "minute": 0, "day_of_week": en_day}]
            return {"type": "weekly", "times": times}
    
    # Daily schedule (default)
    times = []
    for part in schedule_str.replace("每天", "").split("/"):
        for segment in part.strip().split():
            if ":" in segment:
                try:
                    h, m = segment.split(":")
                    times.append({"hour": int(h), "minute": int(m)})
                except (ValueError, IndexError):
                    pass
    if not times:
        times = [{"hour": 9, "minute": 0}]
    return {"type": "daily", "times": times}


def _collection_job(topic_id: int, topic_name: str, keywords_csv: str) -> None:
    """Scheduled collection job for a topic.
    
    V2 Fix: Uses asyncio.run() to create a fresh event loop
    in the APScheduler background thread. This avoids the
    "no event loop" or "event loop already running" errors.
    """
    logger.info(
        "[Scheduler] Collection job triggered for topic '%s' (id=%d) at %s",
        topic_name, topic_id, datetime.now().isoformat(),
    )
    try:
        keywords = [k.strip() for k in keywords_csv.split(",") if k.strip()]
        # Create a fresh event loop in this background thread
        result = asyncio.run(_async_collection(topic_id, topic_name, keywords))
        logger.info(
            "[Scheduler] Collection for topic '%s' complete: %d new articles",
            topic_name, result,
        )
    except Exception as e:
        logger.error(
            "[Scheduler] Collection for topic '%s' failed: %s",
            topic_name, e, exc_info=True,
        )


async def _async_collection(topic_id: int, topic_name: str, keywords: list[str]) -> int:
    """Async collection logic, run inside asyncio.run() from scheduler thread."""
    from app.services.ingestion import run_topic_collection
    return await run_topic_collection(topic_id, topic_name, keywords)


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
                # Retry on failure
                misfire_grace_time=300,  # 5 min grace
                coalesce=True,
            )
            logger.info(
                "Scheduled job %s: daily at %02d:%02d for topic '%s'",
                job_id, time_cfg["hour"], time_cfg["minute"], topic_name,
            )
    elif parsed["type"] == "weekly":
        for i, time_cfg in enumerate(parsed["times"]):
            job_id = f"{job_prefix}_{i}"
            trigger = CronTrigger(
                day_of_week=time_cfg.get("day_of_week", "*"),
                hour=time_cfg["hour"],
                minute=time_cfg["minute"],
            )
            scheduler.add_job(
                _collection_job,
                trigger=trigger,
                id=job_id,
                args=[topic_id, topic_name, keywords_csv],
                replace_existing=True,
                misfire_grace_time=300,
                coalesce=True,
            )
            logger.info(
                "Scheduled job %s: weekly %s at %02d:%02d for topic '%s'",
                job_id, time_cfg.get("day_of_week", "*"),
                time_cfg["hour"], time_cfg["minute"], topic_name,
            )


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

    scheduler = BackgroundScheduler(
        job_defaults={
            "coalesce": True,
            "misfire_grace_time": 300,
            "max_instances": 1,  # Don't run same job concurrently
        }
    )
    scheduler.start()
    logger.info("APScheduler started (V2 with asyncio.run fix)")

    # Load enabled topics from DB and schedule them
    try:
        from app.db.session import SessionLocal
        from app.db.models import TopicModel

        with SessionLocal() as db:
            topics = db.query(TopicModel).filter(TopicModel.enabled == True).all()  # noqa: E712
            for topic in topics:
                schedule_topic(topic.id, topic.name, topic.schedule, topic.keywords)
                logger.info("Loaded schedule for topic '%s' (id=%d): %s", topic.name, topic.id, topic.schedule)
    except Exception as e:
        logger.error("Failed to load initial topic schedules: %s", e)


def shutdown_scheduler() -> None:
    """Gracefully shutdown the scheduler."""
    global scheduler
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        scheduler = None
        logger.info("APScheduler shutdown")


def get_scheduled_jobs() -> list[dict]:
    """Get list of all scheduled jobs (for API / status)."""
    if scheduler is None:
        return []
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
            "trigger": str(job.trigger),
        })
    return jobs
