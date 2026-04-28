// Chat API (智能对话)
import { parseJson, API_BASE } from './index'
import type { ChatResponse, ChatCitation } from '../types/chat'

export async function askQuestion(question: string): Promise<{ question: string; answer: string; citations: ChatCitation[] }> {
  const res = await fetch(`${API_BASE}/api/chat/query`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }),
  })
  return parseJson<{ question: string; answer: string; citations: ChatCitation[] }>(res)
}

export async function chatWithSearch(question: string, mode: string = 'hybrid', knowledgeBaseId: number | null = null, forceSearch = false, sessionId: number | null = null): Promise<ChatResponse> {
  const body: any = { question, mode, knowledge_base_id: knowledgeBaseId, force_search: forceSearch }
  if (sessionId != null) body.session_id = sessionId
  const res = await fetch(`${API_BASE}/api/chat/chat`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return parseJson<ChatResponse>(res)
}

// Sessions
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
