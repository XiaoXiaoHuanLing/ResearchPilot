# ResearchPilot Next Steps

## Current priority
Implement real web source ingestion pipeline, then polish frontend + QA experience.

## Immediate implementation queue
1. Source ingestion pipeline (web fetch + article extraction + normalization + storage)
2. Knowledge base notes/annotations model
3. Frontend article detail view (full content + citation links)
4. Frontend QA citation → article linking
5. .env template + README setup instructions
6. Git initialization
7. Tests
8. SQLite → PostgreSQL upgrade when needed

## Tech stack alignment checklist
- [x] Vue 3 + TypeScript + Vite
- [x] Pinia
- [x] Vue Router
- [x] Tailwind CSS
- [x] Naive UI
- [x] FastAPI
- [x] SQLAlchemy + SQLite
- [x] APScheduler (wired + running)
- [x] LangChain + LangGraph (report generation workflow)
- [x] RAG retrieval (OpenAI embeddings + numpy + OpenAI LLM)
- [ ] LlamaIndex (deferred — lightweight RAG working)
- [ ] PostgreSQL (deferred)

## Re-entry instruction
1. Read `docs/WORK_LOG.md` — latest entries at top
2. Read `docs/MVP_PROGRESS_REVIEW_2026-04-09.md`
3. Continue from item 1 in the immediate implementation queue
