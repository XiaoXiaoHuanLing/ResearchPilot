// Knowledge Base types — V2 aligned with backend models

export interface KnowledgeBase {
  id: number
  name: string
  description: string
  kb_type: string       // always "upload" now
  enabled: boolean
  document_count: number
  chunk_count: number
  created_at: string
  updated_at: string
}

export interface KbDocument {
  id: number
  kb_id: number
  title: string
  file_path: string
  file_type: string       // txt, md, pdf, docx etc.
  source_type: string     // "upload" | "report"
  source: string
  index_status: string    // pending | indexing | indexed | failed
  chunk_count: number
  created_at: string
  indexed_at: string
}
