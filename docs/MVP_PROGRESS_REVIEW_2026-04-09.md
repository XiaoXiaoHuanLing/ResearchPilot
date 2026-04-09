# ResearchPilot MVP Progress Review (2026-04-09)

## 1. Product goal recap
ResearchPilot is positioned as an AI research platform for open-source topic tracking and analysis. The MVP goal defined in docs is to deliver a directly viewable product with four minimum capabilities:
1. View tracked topics
2. View collected article records
3. Ask a retrieval-style question
4. View report records

## 2. What is already completed
### Documentation
- Product positioning is defined in `PRODUCT_OVERVIEW.md`
- Initial stack and MVP scope are defined in `PROJECT_INIT_PLAN.md`
- V1 architecture and module direction are defined in `TECH_ARCHITECTURE_V1.md`

### Backend
- FastAPI app bootstrapped
- CORS enabled
- Routes registered:
  - `/api/topics`
  - `/api/articles`
  - `/api/qa/query`
  - `/api/reports`
  - `/health`
- All current endpoints return mock/demo data successfully in code structure

### Frontend
- Vue 3 + TypeScript + Vite scaffold exists
- Single-page dashboard implemented in `src/App.vue`
- Frontend can request backend endpoints for:
  - topics
  - articles
  - reports
  - QA
- Current UI already presents a demoable landing dashboard

## 3. Current project stage
The project is currently at:

**Stage: clickable / viewable demo prototype with mock data**

It is beyond “idea only”, but not yet a true functional MVP with persistence, topic operations, or real retrieval.

## 4. Gaps between docs and implementation
### Missing from backend
- No database integration yet
- No SQLAlchemy models yet
- No create/update/delete topic operations
- No real article ingestion pipeline
- No bookmark / knowledge base layer
- No retrieval/RAG implementation yet
- No report generation pipeline yet
- No scheduler jobs yet
- No settings/config module yet

### Missing from frontend
- No routing / multi-page structure yet
- No loading / empty / error state polish
- No forms for topic creation/editing
- No article filtering/searching
- No report details page
- No citation link interactions
- No persistent state management layer beyond direct fetches

### Delivery/engineering missing pieces
- No `.git` repo initialized in current directory
- No run instructions verified end-to-end in this pass
- No tests yet
- No environment config strategy yet

## 5. Recommended next implementation order
To move from “demo shell” to “real MVP foundation”, the recommended order is:

### Phase 1 - solid local MVP foundation
1. Add backend settings module
2. Add lightweight storage layer
   - Prefer SQLite first for zero-config local MVP
3. Add real Topic CRUD
4. Add article list service backed by storage
5. Seed demo data automatically
6. Improve frontend to support real topic operations and state feedback

### Phase 2 - research workflow usefulness
7. Add bookmark / knowledge base collection model
8. Add article filtering and bookmarking UI
9. Replace mock QA with a simple retrieval stub over stored records
10. Add basic report generation from stored content

### Phase 3 - automation and scale-up
11. Add scheduler jobs
12. Add source ingestion pipeline
13. Upgrade SQLite to PostgreSQL when needed
14. Introduce LlamaIndex / RAG stack

## 6. Immediate action chosen for continuation
For the next step, the highest-value continuation is:

**Implement Phase 1 foundation**
- backend settings
- SQLite-backed storage
- topic/article/report persistence
- demo seed data
- frontend adaptation only if required

This keeps the project aligned with docs while making it actually runnable as a local MVP base.

## 7. Summary judgment
ResearchPilot is currently:
- **well-defined in docs**
- **bootstrapped in frontend and backend**
- **already demoable visually**
- **not yet functionally complete as a real MVP**

So the correct status is:

**Current progress: MVP prototype completed; real MVP foundation not yet completed.**
