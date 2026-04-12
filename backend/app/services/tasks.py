"""Background task manager for async operations.

Provides:
- Task creation and tracking (with status, progress, results)
- Background execution of long-running operations (collection, reindex)
- SSE-compatible progress events for frontend consumption
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BackgroundTask:
    id: str
    type: str  # "collection", "reindex", "ingest_url"
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0  # 0.0 to 1.0
    message: str = ""
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None


# In-memory task store (simple, sufficient for single-server MVP)
_tasks: dict[str, BackgroundTask] = {}


def create_task(task_type: str, description: str = "") -> BackgroundTask:
    """Create a new background task entry."""
    task_id = f"{task_type}_{int(time.time() * 1000)}"
    task = BackgroundTask(id=task_id, type=task_type, message=description)
    _tasks[task_id] = task
    return task


def get_task(task_id: str) -> BackgroundTask | None:
    return _tasks.get(task_id)


def list_tasks(task_type: str | None = None, limit: int = 20) -> list[BackgroundTask]:
    """List tasks, optionally filtered by type."""
    tasks = list(_tasks.values())
    if task_type:
        tasks = [t for t in tasks if t.type == task_type]
    tasks.sort(key=lambda t: t.created_at, reverse=True)
    return tasks[:limit]


def cleanup_old_tasks(max_age_seconds: int = 3600) -> int:
    """Remove completed/failed tasks older than max_age_seconds."""
    now = time.time()
    to_remove = [
        tid for tid, t in _tasks.items()
        if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
        and t.completed_at is not None
        and (now - t.completed_at) > max_age_seconds
    ]
    for tid in to_remove:
        del _tasks[tid]
    return len(to_remove)


async def run_background_task(
    task: BackgroundTask,
    coro_factory: Callable[..., Any],
    *args,
    **kwargs,
) -> BackgroundTask:
    """Run a coroutine as a background task with progress tracking."""
    task.status = TaskStatus.RUNNING
    task.started_at = time.time()
    
    try:
        result = await coro_factory(*args, **kwargs)
        task.status = TaskStatus.COMPLETED
        task.progress = 1.0
        task.result = result
        task.completed_at = time.time()
        logger.info("Background task %s completed in %.1fs", task.id, task.completed_at - task.started_at)
    except Exception as e:
        task.status = TaskStatus.FAILED
        task.error = str(e)
        task.completed_at = time.time()
        logger.error("Background task %s failed: %s", task.id, e)
    
    return task


# --- Pre-built task runners ---

async def start_collection_task(topic_id: int, topic_name: str, keywords_csv: str) -> BackgroundTask:
    """Start a background collection task for a topic."""
    task = create_task("collection", f"采集专题「{topic_name}」")
    
    async def _run_collection():
        from app.services.ingestion import run_topic_collection
        keywords = [k.strip() for k in keywords_csv.split(",") if k.strip()]
        count = await run_topic_collection(topic_id, topic_name, keywords)
        task.message = f"专题「{topic_name}」采集完成，新增 {count} 篇资讯"
        return {"new_articles": count}
    
    # Run in background
    asyncio.create_task(run_background_task(task, _run_collection))
    return task


async def start_reindex_task() -> BackgroundTask:
    """Start a background reindex task."""
    task = create_task("reindex", "重建所有收藏文章索引")
    
    async def _run_reindex():
        from app.db.session import SessionLocal
        from app.services.rag.engine import reindex_all_articles
        with SessionLocal() as db:
            result = await reindex_all_articles(db)
        task.message = f"索引重建完成：成功 {result['success']} 篇，失败 {result['failed']} 篇"
        return result
    
    asyncio.create_task(run_background_task(task, _run_reindex))
    return task
