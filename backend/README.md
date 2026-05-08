# ResearchPilot Backend

AI-powered open-source research platform backend.

## Quick Start

```bash
# Install dependencies (requires uv)
uv sync

# Copy and configure environment
cp .env.example .env
# Edit .env to add your model and embedding configuration

# Start the server
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Evaluation smoke run

Run the bundled local KB evaluation from the backend root with the uv-managed environment:

```bash
uv run researchpilot-ragas-eval --kb-id 123 --top-k 5
uv run researchpilot-ragas-eval --kb-id 123 --top-k 5 --use-ragas
```

If `--use-ragas` still reports `"scoring_backend": "placeholder"`, first rerun `uv sync` in this directory and then execute the command again with `uv run` so the installed `ragas` and `datasets` packages come from the project environment.

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

Environment variables are loaded from `.env` via `app/core/config.py`.

Key settings for the local RAG and evaluation path include:

- `DASHSCOPE_API_KEY` — shared API key for chat and embeddings
- `DASHSCOPE_BASE_URL` — OpenAI-compatible DashScope endpoint
- `DASHSCOPE_MODEL_NAME` — chat model name
- `DASHSCOPE_MODEL_EMBEDDING_NAME` — embedding model name
- `DATABASE_URL` — defaults to SQLite in project root

## Architecture

- **FastAPI** — Web framework
- **SQLAlchemy** — ORM + SQLite (upgrade to PostgreSQL when needed)
- **RAG Engine** — OpenAI-compatible embeddings + local vector retrieval
- **LangGraph** — Stateful report generation workflow
- **APScheduler** — Background topic collection scheduler
- **Ingestion** — httpx + readability-lxml + BeautifulSoup4
