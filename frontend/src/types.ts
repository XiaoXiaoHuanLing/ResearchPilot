export interface Topic {
  id: number
  name: string
  description: string
  keywords: string[]
  schedule: string
  enabled: boolean
}

export interface TopicCreatePayload {
  name: string
  description: string
  keywords: string[]
  schedule: string
  enabled: boolean
}

export interface Article {
  id: number
  topic: string
  title: string
  source: string
  published_at: string
  summary: string
  url: string
  bookmarked: boolean
  content: string
}

export interface ReportItem {
  id: number
  title: string
  created_at: string
  summary: string
}

export interface QaResponse {
  question: string
  answer: string
  citations: Array<{
    article_id?: number
    title?: string
    source?: string
    url?: string
    relevance_score?: number
    snippet?: string
  }>
}

export interface IngestUrlResult {
  id: number | null
  title: string | null
  message: string
}

export interface CollectTopicResult {
  topic_name: string
  new_articles: number
  message: string
}
