// Article types
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
  quality_score: number
  quality_label: string
  expires_at: string
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
