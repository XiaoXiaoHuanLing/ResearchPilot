// Knowledge Base API
import { parseJson, API_BASE } from './index'
import type { KnowledgeBase, KbDocument } from '../types/knowledge'

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

/** Upload a file to KB — matches backend POST /{kb_id}/documents/upload */
export async function uploadKbFile(kbId: number, file: File): Promise<KbDocument> {
  const fd = new FormData()
  fd.append('file', file)
  fd.append('source_type', 'upload')
  const res = await fetch(`${API_BASE}/api/knowledge-bases/${kbId}/documents/upload`, {
    method: 'POST', body: fd,
  })
  return parseJson<KbDocument>(res)
}

export async function deleteKbDocument(kbId: number, docId: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/knowledge-bases/${kbId}/documents/${docId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('删除失败')
}
