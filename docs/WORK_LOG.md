# ResearchPilot Work Log

## 2026-04-09 16:50 CST - RAG + LangGraph + APScheduler milestone

### Completed
- **Bug fix**: Dashboard returning empty data after navigating to other pages — root cause was Pinia store filter state persisting across pages. Added `loadAll()` method that resets filters.
- **Article content field**: Added `content` (full text) column to ArticleModel, updated schema and seed data
- **Lightweight RAG engine** (`app/services/rag/engine.py`):
  - Custom VectorStore backed by numpy + JSON files (no torch dependency)
  - Uses OpenAI embeddings API for vectorization
  - Cosine similarity search for retrieval
  - RAG query pipeline: retrieve → build context → LLM generation
  - Graceful fallback when no API key configured
  - Auto-index on bookmark action
- **LangGraph report generation** (`app/services/report_generator.py`):
  - StateGraph workflow: collect → analyze → generate
  - LLM-powered analysis when API key available
  - Fallback to structured aggregation without LLM
  - Integrated into `/api/reports/generate` endpoint
- **APScheduler integration** (`app/services/scheduler.py`):
  - Background scheduler initialized on app startup (lifespan)
  - Parse human-readable schedule strings ("每天 09:00")
  - Auto-schedule collection jobs for enabled topics
  - Sync on topic create/update/delete
  - Placeholder collection job (ready for ingestion pipeline)
- **App lifecycle**: Replaced deprecated `@app.on_event("startup")` with modern `lifespan` context manager
- **Dependency optimization**: Removed heavy llama-index/torch (~200MB), replaced with openai + numpy (~15MB)

### Current architecture
- Frontend: Vue 3 + TypeScript + Vite + Pinia + Vue Router + Tailwind CSS + Naive UI
- Backend: FastAPI + SQLAlchemy + SQLite
- RAG: OpenAI embeddings + numpy cosine similarity + OpenAI LLM
- Report: LangGraph StateGraph + LangChain + ChatOpenAI
- Scheduler: APScheduler + lifespan integration
- All gracefully functional without API key (fallback modes)

### Remaining gaps
- Real web source ingestion pipeline (fetch + normalize + store)
- Knowledge base notes/annotations beyond simple bookmark
- Frontend: article detail page with full content
- Frontend: QA citation should link to articles
- SQLite → PostgreSQL upgrade
- Tests
- Git initialization
- Environment config (.env template)

### Next step
- Implement source ingestion pipeline for real web data collection

---

## 2026-04-09 15:17 CST - Frontend refactor + Bookmark + Report generation milestone

### Completed
- Backend: Article bookmark toggle — `POST /api/articles/{id}/bookmark`
- Backend: Article filter API — `GET /api/articles?topic=&keyword=&bookmarked=`
- Backend: Report generation — `POST /api/reports/generate`
- Backend: pyproject.toml fix for uv/hatchling
- Frontend: Full tech stack upgrade (Vue Router, Pinia, Tailwind CSS, Naive UI, @vueuse/core)
- Frontend: Multi-page architecture with 5 views
- Frontend: Vite proxy for API

---

## 2026-04-09 11:20 CST - Topic CRUD milestone completed

### Completed
- Full Topic CRUD (create, read, update, delete) in backend + frontend

---

## 2026-04-09 06:13 CST - MVP progress review and backend foundation

### Completed
- Documentation, backend persistence, API endpoints, seed data
