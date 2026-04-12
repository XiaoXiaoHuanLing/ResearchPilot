import type { Topic, TopicCreatePayload, Article, ReportItem, QaResponse, KnowledgeBase, KbDocument, ChatResponse, ChatCitation } from './types'

const API_BASE = ''

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let msg = `API request failed: ${res.status}`
    try { const j = JSON.parse(text); if (j.detail) msg = j.detail } catch {}
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

// --- System Status ---

export async function fetchSystemStatus() {
  const res = await fetch(`${API_BASE}/api/status`)
  return parseJson<{llm_configured:boolean;search_configured:boolean;rag_available:boolean;llm_model:string|null;embedding_model:string|null;search_provider:string|null}>(res)
}

// --- Topics ---

export async function fetchTopics(): Promise<Topic[]> {
  const res = await fetch(`${API_BASE}/api/topics`)
  return parseJson<Topic[]>(res)
}

export async function createTopic(payload: TopicCreatePayload): Promise<Topic> {
  const res = await fetch(`${API_BASE}/api/topics`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  return parseJson<Topic>(res)
}

export async function updateTopic(id: number, payload: TopicCreatePayload): Promise<Topic> {
  const res = await fetch(`${API_BASE}/api/topics/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  return parseJson<Topic>(res)
}

export async function deleteTopic(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/topics/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`删除失败: ${res.status}`)
}

// --- Articles ---

export async function fetchArticles(params?: { topic?: string | null; keyword?: string | null; bookmarked?: boolean | null }): Promise<Article[]> {
  const sp = new URLSearchParams()
  if (params?.topic) sp.set('topic', params.topic)
  if (params?.keyword) sp.set('keyword', params.keyword)
  if (params?.bookmarked != null) sp.set('bookmarked', String(params.bookmarked))
  const qs = sp.toString()
  const res = await fetch(`${API_BASE}/api/articles${qs ? `?${qs}` : ''}`)
  return parseJson<Article[]>(res)
}

export async function toggleBookmark(id: number, bookmarked: boolean): Promise<Article> {
  const res = await fetch(`${API_BASE}/api/articles/${id}/bookmark`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ bookmarked }),
  })
  return parseJson<Article>(res)
}

export async function deleteArticle(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/articles/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    const j = await res.json().catch(() => ({ detail: '删除失败' }))
    throw new Error(j.detail || '删除失败')
  }
}

export async function ingestUrl(url: string, topic: string, autoBookmark = false) {
  const res = await fetch(`${API_BASE}/api/articles/ingest/url`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url, topic, auto_bookmark: autoBookmark }),
  })
  return parseJson<{ id: number | null; title: string | null; message: string }>(res)
}

export async function collectTopic(topicId: number) {
  const res = await fetch(`${API_BASE}/api/articles/collect`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ topic_id: topicId }),
  })
  return parseJson<{ topic_name: string; new_articles: number; message: string }>(res)
}

// --- Reports ---

export async function fetchReports(): Promise<ReportItem[]> {
  const res = await fetch(`${API_BASE}/api/reports`)
  return parseJson<ReportItem[]>(res)
}

export async function generateReport(payload: { title?: string; article_ids?: number[]; prompt?: string }): Promise<ReportItem> {
  const res = await fetch(`${API_BASE}/api/reports/generate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  return parseJson<ReportItem>(res)
}

export async function deleteReport(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/reports/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('删除失败')
}

export function getReportExportUrl(id: number, format: 'markdown' | 'pdf'): string {
  return `${API_BASE}/api/reports/${id}/export/${format}`
}

// --- Knowledge Bases ---

export async function fetchKnowledgeBases(): Promise<KnowledgeBase[]> {
  const res = await fetch(`${API_BASE}/api/knowledge-bases`)
  return parseJson<KnowledgeBase[]>(res)
}

export async function createKnowledgeBase(name: string, description: string = ''): Promise<KnowledgeBase> {
  const res = await fetch(`${API_BASE}/api/knowledge-bases`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, description }),
  })
  return parseJson<KnowledgeBase>(res)
}

export async function deleteKnowledgeBase(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/knowledge-bases/${id}`, { method: 'DELETE' })
  if (!res.ok) {
    const j = await res.json().catch(() => ({ detail: '删除失败' }))
    throw new Error(j.detail || '删除失败')
  }
}

export async function fetchKbDocuments(kbId: number): Promise<KbDocument[]> {
  const res = await fetch(`${API_BASE}/api/knowledge-bases/${kbId}/documents`)
  return parseJson<KbDocument[]>(res)
}

export async function uploadKbDocument(kbId: number, title: string, content: string, fileType: string = 'text'): Promise<KbDocument> {
  const res = await fetch(`${API_BASE}/api/knowledge-bases/${kbId}/documents`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title, content, file_type: fileType }),
  })
  return parseJson<KbDocument>(res)
}

export async function uploadKbFile(kbId: number, file: File): Promise<KbDocument> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`${API_BASE}/api/knowledge-bases/${kbId}/documents/upload-file`, {
    method: 'POST', body: fd,
  })
  return parseJson<KbDocument>(res)
}

export async function deleteKbDocument(kbId: number, docId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/knowledge-bases/${kbId}/documents/${docId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('删除失败')
}

// --- QA ---

export async function askQuestion(question: string): Promise<QaResponse> {
  const res = await fetch(`${API_BASE}/api/qa/query`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }),
  })
  return parseJson<QaResponse>(res)
}

// --- Chat ---

export async function chatWithSearch(question: string, mode: string = 'hybrid', knowledgeBaseId: number | null = null, forceSearch = false, sessionId: number | null = null): Promise<ChatResponse> {
  const body: any = { question, mode, knowledge_base_id: knowledgeBaseId, force_search: forceSearch }
  if (sessionId != null) body.session_id = sessionId
  const res = await fetch(`${API_BASE}/api/qa/chat`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return parseJson<ChatResponse>(res)
}

export async function collectTopicAsync(topicId: number): Promise<{ task_id: string; topic_name: string; message: string }> {
  const res = await fetch(`${API_BASE}/api/articles/collect`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ topic_id: topicId, async_mode: true }),
  })
  return parseJson<{ task_id: string; topic_name: string; message: string }>(res)
}

export async function fetchTaskStatus(taskId: string) {
  const res = await fetch(`${API_BASE}/api/tasks/${taskId}`)
  return parseJson<{ id: string; type: string; status: string; progress: number; message: string; error: string | null; result: any }>(res)
}

export async function fetchRagStats() {
  const res = await fetch(`${API_BASE}/api/rag/stats`)
  return parseJson<any>(res)
}

export async function fetchSchedulerJobs() {
  const res = await fetch(`${API_BASE}/api/scheduler/jobs`)
  return parseJson<{ jobs: Array<{ id: string; next_run: string | null; trigger: string }> }>(res)
}

// --- Chat Sessions ---

export async function createChatSession(title: string = '新对话', mode: string = 'hybrid') {
  const res = await fetch(`${API_BASE}/api/chat-sessions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title, mode }),
  })
  return parseJson<{ id: number; title: string; mode: string; created_at: string; updated_at: string; message_count: number }>(res)
}

export async function fetchChatSessions() {
  const res = await fetch(`${API_BASE}/api/chat-sessions`)
  return parseJson<Array<{ id: number; title: string; mode: string; created_at: string; updated_at: string; message_count: number }>>(res)
}

export async function fetchChatSessionHistory(sessionId: number) {
  const res = await fetch(`${API_BASE}/api/chat-sessions/${sessionId}/history`)
  return parseJson<Array<{ id: number; role: string; content: string; mode: string; search_used: boolean; citations: any[]; created_at: string }>>(res)
}

export async function deleteChatSession(sessionId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/chat-sessions/${sessionId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('删除失败')
}
