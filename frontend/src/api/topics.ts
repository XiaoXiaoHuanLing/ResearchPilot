// Topics API
import { parseJson, API_BASE } from './index'
import type { Topic, TopicCreatePayload } from '../types/topic'

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
