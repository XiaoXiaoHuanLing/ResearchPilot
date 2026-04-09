# ResearchPilot Backend

AI-powered open-source research platform backend.

## Quick Start

```bash
# Install dependencies (requires uv)
uv sync

# Copy and configure environment
cp .env.example .env
# Edit .env to add your OpenAI API key (optional but recommended)

# Start the server
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/topics` | List all topics |
| POST | `/api/topics` | Create a topic |
| PUT | `/api/topics/{id}` | Update a topic |
| DELETE | `/api/topics/{id}` | Delete a topic |
| GET | `/api/articles` | List articles (supports ?topic=&keyword=&bookmarked=) |
| POST | `/api/articles/{id}/bookmark` | Toggle article bookmark |
| POST | `/api/articles/ingest/url` | Ingest a URL into a topic |
| POST | `/api/articles/collect` | Trigger topic collection |
| POST | `/api/qa/query` | Ask a research question (RAG) |
| GET | `/api/reports` | List reports |
| POST | `/api/reports/generate` | Generate report from bookmarks |

## Configuration

Environment variables (prefix `RESEARCHPILOT_`):

- `OPENAI_API_KEY` — Required for RAG and LLM-powered reports
- `OPENAI_BASE_URL` — Optional, for proxy or alternative endpoint
- `EMBEDDING_MODEL` — Default: `text-embedding-3-small`
- `LLM_MODEL` — Default: `gpt-4o-mini`
- `DATABASE_URL` — Default: SQLite in project root

## Architecture

- **FastAPI** — Web framework
- **SQLAlchemy** — ORM + SQLite (upgrade to PostgreSQL when needed)
- **RAG Engine** — OpenAI embeddings + numpy cosine similarity
- **LangGraph** — Stateful report generation workflow
- **APScheduler** — Background topic collection scheduler
- **Ingestion** — httpx + readability-lxml + BeautifulSoup4
