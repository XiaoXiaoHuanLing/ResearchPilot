// Chat types (智能对话)
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

// RAGAS评测接口预留: 检索结果标准化输出
export interface RAGRetrievalResult {
  query: string
  documents: Array<{
    doc_id: string
    content: string
    score: number
    metadata: Record<string, any>
  }>
  retrieval_mode: 'dense' | 'hybrid' | 'filtered'
  latency_ms: number
}

// RAGAS评测接口预留: 答案+引用标准化输出
export interface RAGAnswerResult {
  query: string
  answer: string
  citations: ChatCitation[]
  faithfulness?: number    // 忠实度评分
  answer_relevancy?: number // 答案相关性评分
  context_precision?: number // 上下文精确率
  context_recall?: number    // 上下文召回率
  latency_ms: number
}
