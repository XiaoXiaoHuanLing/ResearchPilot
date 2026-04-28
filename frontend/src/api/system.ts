// System API
import { parseJson, API_BASE } from './index'

export async function fetchSystemStatus() {
  const res = await fetch(`${API_BASE}/api/status`)
  return parseJson<{llm_configured:boolean;search_configured:boolean;rag_available:boolean;llm_model:string|null;embedding_model:string|null;search_provider:string|null}>(res)
}

export async function fetchRagStats() {
  const res = await fetch(`${API_BASE}/api/rag/stats`)
  return parseJson<any>(res)
}

export async function fetchSchedulerJobs() {
  const res = await fetch(`${API_BASE}/api/scheduler/jobs`)
  return parseJson<{ jobs: Array<{ id: string; next_run: string | null; trigger: string }> }>(res)
}
