from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, topics, articles, chat, reports, knowledge_base, tasks, chat_sessions, copilot
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine, SessionLocal
from app.services.seed import seed_demo_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown events."""
    # Startup
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_demo_data(db)

    from app.services.scheduler import init_scheduler, shutdown_scheduler
    init_scheduler()

    # Initialize SQLite checkpointers for chat & copilot agents
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from pathlib import Path

    cp_dir = Path(settings.storage_base_dir) / "checkpoints"
    cp_dir.mkdir(parents=True, exist_ok=True)

    # Chat checkpointer
    chat_conn = await aiosqlite.connect(str(cp_dir / "chat.db"))
    chat_saver = AsyncSqliteSaver(chat_conn)
    await chat_saver.setup()
    from app.services.chat import agent as chat_agent_mod
    chat_agent_mod._checkpointer = chat_saver

    # Copilot checkpointer
    copilot_conn = await aiosqlite.connect(str(cp_dir / "copilot.db"))
    copilot_saver = AsyncSqliteSaver(copilot_conn)
    await copilot_saver.setup()
    from app.services.copilot import agent as copilot_agent_mod
    copilot_agent_mod._checkpointer = copilot_saver

    # 预加载 BM25 缓存（避免首次查询降级）
    try:
        from app.services.knowledge.bm25 import preload_bm25
        preload_bm25()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("BM25 preload skipped: %s", e)

    yield

    # Shutdown
    await chat_conn.close()
    await copilot_conn.close()
    shutdown_scheduler()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api", tags=["system"])
app.include_router(topics.router, prefix="/api/topics", tags=["topics"])
app.include_router(articles.router, prefix="/api/articles", tags=["articles"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(knowledge_base.router, prefix="/api/knowledge-bases", tags=["knowledge-bases"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(chat_sessions.router, prefix="/api/chat-sessions", tags=["chat-sessions"])
app.include_router(copilot.router, prefix="/api/copilot", tags=["copilot"])
