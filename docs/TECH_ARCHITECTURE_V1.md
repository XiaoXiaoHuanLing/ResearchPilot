# ResearchPilot V1 Technical Architecture

## Frontend
- Vue 3
- TypeScript
- Vite
- Pinia
- Vue Router
- Tailwind CSS
- Naive UI

## Backend
- Python 3.12
- FastAPI
- uv for project and package management

## AI / Retrieval
- LlamaIndex for RAG and indexing
- LangChain for tool and chain orchestration
- LangGraph for stateful workflows
- DeepAgents reserved for future advanced agent collaboration
- MCP / Skills reserved for future tool integration and workflow packaging

## Database
- PostgreSQL (recommended target)
- MVP may start with SQLite if local zero-config startup is prioritized

## Scheduler
- APScheduler for topic-based periodic jobs

## V1 Modules
1. Topic management
2. Source fetching and normalization
3. Article processing and summarization
4. Knowledge base bookmarks and notes
5. Retrieval / citation pipeline
6. Q&A and analysis service
7. Report generation service

## Source Traceability Requirements
- Every answer should reference stored source records whenever possible
- Every report should include a source list
- Original URL, source name, and published time should be preserved

## Directory Conventions
- docs/: technical docs, API docs, and product docs
- backend/: FastAPI backend and AI pipeline
- frontend/: Vue application
