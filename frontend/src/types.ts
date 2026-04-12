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
  topic: string
  created_at: string
  summary: string
  content: string
}

export interface KnowledgeBase {
  id: number
  name: string
  description: string
  kb_type: string
  is_default: boolean
  article_count: number
  created_at: string
}

export interface KbDocument {
  id: number
  kb_id: number
  title: string
  source: string
  file_type: string
  indexed: boolean
  created_at: string
}

export interface SystemStatus {
  llm_configured: boolean
  search_configured: boolean
  rag_available: boolean
  llm_model: string | null
  embedding_model: string | null
  search_provider: string | null
}

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

export interface ChatSession {
  id: number
  title: string
  mode: string
  created_at: string
  updated_at: string
  message_count: number
}

export interface ChatMessage {
  id: number
  role: string
  content: string
  mode: string
  search_used: boolean
  citations: ChatCitation[]
  created_at: string
}

export interface ChatCitation {
  article_id?: number | null
  title?: string | null
  source?: string | null
  url?: string | null
  published_at?: string | null
  topic?: string | null
  relevance_score?: number | null
  snippet?: string | null
}

export interface ChatResponse {
  question: string
  answer: string
  citations: ChatCitation[]
  search_used: boolean
  new_articles: number
  mode: string
}
