// Common types
export interface SystemStatus {
  llm_configured: boolean
  search_configured: boolean
  rag_available: boolean
  llm_model: string | null
  embedding_model: string | null
  search_provider: string | null
}
