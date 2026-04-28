"""Background task management API routes."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.tasks import (
    get_task, list_tasks, cleanup_old_tasks,
    start_collection_task, start_reindex_task,
)

router = APIRouter()


class TaskRead(BaseModel):
    id: str
    type: str
    status: str
    progress: float
    message: str
    error: str | None
    result: Any = None


@router.get("", response_model=list[TaskRead])
def get_tasks(task_type: str | None = None, limit: int = 20):
    """List background tasks with optional type filter."""
    tasks = list_tasks(task_type=task_type, limit=limit)
    return [
        TaskRead(
            id=t.id, type=t.type, status=t.status.value,
            progress=t.progress, message=t.message,
            error=t.error, result=t.result,
        )
        for t in tasks
    ]


@router.get("/{task_id}", response_model=TaskRead)
def get_task_status(task_id: str):
    """Get status of a specific background task."""
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskRead(
        id=task.id, type=task.type, status=task.status.value,
        progress=task.progress, message=task.message,
        error=task.error, result=task.result,
    )


@router.post("/cleanup", response_model=dict)
def cleanup_tasks(max_age_seconds: int = 3600):
    """Clean up old completed/failed tasks."""
    removed = cleanup_old_tasks(max_age_seconds)
    return {"removed": removed}


@router.post("/cleanup-expired-articles", response_model=dict)
async def cleanup_expired_articles():
    """手动触发过期资讯清理（未收藏且已过期的文章）。"""
    from app.services.consultation.cleanup import cleanup_expired_articles as _cleanup
    count = await _cleanup()
    return {"expired_articles_removed": count}

