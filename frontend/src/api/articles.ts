// Articles API
import { parseJson, API_BASE } from './index'
import type { Article, IngestUrlResult, CollectTopicResult } from '../types/article'

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

export async function ingestUrl(url: string, topic: string) {
  const res = await fetch(`${API_BASE}/api/articles/ingest/url`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url, topic }),
  })
  return parseJson<IngestUrlResult>(res)
}

export async function collectTopic(topicId: number) {
  const res = await fetch(`${API_BASE}/api/articles/collect`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ topic_id: topicId }),
  })
  return parseJson<CollectTopicResult>(res)
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
