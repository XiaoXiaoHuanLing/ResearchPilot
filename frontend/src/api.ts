import type { Topic, TopicCreatePayload, Article, ReportItem, QaResponse } from './types'

const API_BASE = ''

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`API request failed: ${res.status} ${text}`)
  }
  return res.json() as Promise<T>
}

// --- Topics ---

export async function fetchTopics(): Promise<Topic[]> {
  const res = await fetch(`${API_BASE}/api/topics`)
  return parseJson<Topic[]>(res)
}

export async function createTopic(payload: TopicCreatePayload): Promise<Topic> {
  const res = await fetch(`${API_BASE}/api/topics`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseJson<Topic>(res)
}

export async function updateTopic(id: number, payload: TopicCreatePayload): Promise<Topic> {
  const res = await fetch(`${API_BASE}/api/topics/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseJson<Topic>(res)
}

export async function deleteTopic(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/api/topics/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`API request failed: ${res.status}`)
}

// --- Articles ---

export interface ArticleFilterParams {
  topic?: string | null
  keyword?: string | null
  bookmarked?: boolean | null
}

export async function fetchArticles(params?: ArticleFilterParams): Promise<Article[]> {
  const searchParams = new URLSearchParams()
  if (params?.topic) searchParams.set('topic', params.topic)
  if (params?.keyword) searchParams.set('keyword', params.keyword)
  if (params?.bookmarked !== null && params?.bookmarked !== undefined)
    searchParams.set('bookmarked', String(params.bookmarked))

  const qs = searchParams.toString()
  const url = `${API_BASE}/api/articles${qs ? `?${qs}` : ''}`
  const res = await fetch(url)
  return parseJson<Article[]>(res)
}

export async function toggleBookmark(id: number, bookmarked: boolean): Promise<Article> {
  const res = await fetch(`${API_BASE}/api/articles/${id}/bookmark`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bookmarked }),
  })
  return parseJson<Article>(res)
}

export async function ingestUrl(url: string, topic: string, autoBookmark: boolean = false): Promise<{ id: number | null; title: string | null; message: string }> {
  const res = await fetch(`${API_BASE}/api/articles/ingest/url`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, topic, auto_bookmark: autoBookmark }),
  })
  return parseJson(res)
}

export async function collectTopic(topicId: number): Promise<{ topic_name: string; new_articles: number; message: string }> {
  const res = await fetch(`${API_BASE}/api/articles/collect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ topic_id: topicId }),
  })
  return parseJson(res)
}

// --- Reports ---

export async function fetchReports(): Promise<ReportItem[]> {
  const res = await fetch(`${API_BASE}/api/reports`)
  return parseJson<ReportItem[]>(res)
}

export async function generateReport(topic?: string, title?: string): Promise<ReportItem> {
  const res = await fetch(`${API_BASE}/api/reports/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ topic: topic ?? null, title: title ?? null }),
  })
  return parseJson<ReportItem>(res)
}

// --- QA ---

export async function askQuestion(question: string): Promise<QaResponse> {
  const res = await fetch(`${API_BASE}/api/qa/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  })
  return parseJson<QaResponse>(res)
}
