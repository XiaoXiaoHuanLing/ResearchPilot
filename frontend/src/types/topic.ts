// Topic types
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
