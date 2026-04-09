# Project Init Plan

## Confirmed Stack
- Frontend: Vue 3 + TypeScript + Vite + Pinia + Vue Router
- UI: Tailwind CSS (can combine with Naive UI later)
- Backend: Python 3.12 + FastAPI + uv
- RAG: LlamaIndex
- Agent: LangChain + LangGraph + DeepAgents (reserved for later stages)
- Database target: PostgreSQL
- Scheduler: APScheduler

## MVP Goal
Deliver a directly viewable product with these minimum capabilities:
1. View tracked topics
2. View collected article records
3. Ask a retrieval-style question
4. View report records

## Notes
- Current machine shell exposes Python 3.13 by default. Project docs still pin Python 3.12 as the intended runtime baseline.
- Docs live under docs/ as requested.
