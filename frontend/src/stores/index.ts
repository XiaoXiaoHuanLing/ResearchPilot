import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  fetchTopics as apiFetchTopics,
  createTopic as apiCreateTopic,
  updateTopic as apiUpdateTopic,
  deleteTopic as apiDeleteTopic,
  fetchArticles as apiFetchArticles,
  toggleBookmark as apiToggleBookmark,
  ingestUrl as apiIngestUrl,
  collectTopic as apiCollectTopic,
  fetchReports as apiFetchReports,
  generateReport as apiGenerateReport,
  askQuestion as apiAskQuestion,
} from '../api'
import type { Topic, TopicCreatePayload, Article, ReportItem, QaResponse, IngestUrlResult, CollectTopicResult } from '../types'

export const useTopicStore = defineStore('topics', () => {
  const topics = ref<Topic[]>([])
  const loading = ref(false)

  async function load() {
    loading.value = true
    try {
      topics.value = await apiFetchTopics()
    } finally {
      loading.value = false
    }
  }

  async function create(payload: TopicCreatePayload) {
    const created = await apiCreateTopic(payload)
    topics.value = [created, ...topics.value]
    return created
  }

  async function update(id: number, payload: TopicCreatePayload) {
    const updated = await apiUpdateTopic(id, payload)
    topics.value = topics.value.map((t) => (t.id === updated.id ? updated : t))
    return updated
  }

  async function remove(id: number) {
    await apiDeleteTopic(id)
    topics.value = topics.value.filter((t) => t.id !== id)
  }

  return { topics, loading, load, create, update, remove }
})

export const useArticleStore = defineStore('articles', () => {
  const articles = ref<Article[]>([])
  const loading = ref(false)
  const filterTopic = ref<string | null>(null)
  const filterKeyword = ref<string | null>(null)
  const filterBookmarked = ref<boolean | null>(null)

  /** Load with current filter params */
  async function load() {
    loading.value = true
    try {
      articles.value = await apiFetchArticles({
        topic: filterTopic.value,
        keyword: filterKeyword.value,
        bookmarked: filterBookmarked.value,
      })
    } finally {
      loading.value = false
    }
  }

  /** Load ALL articles, resetting filters */
  async function loadAll() {
    filterTopic.value = null
    filterKeyword.value = null
    filterBookmarked.value = null
    loading.value = true
    try {
      articles.value = await apiFetchArticles()
    } finally {
      loading.value = false
    }
  }

  async function bookmark(id: number, bookmarked: boolean) {
    const updated = await apiToggleBookmark(id, bookmarked)
    articles.value = articles.value.map((a) => (a.id === updated.id ? updated : a))
    return updated
  }

  async function ingest(url: string, topic: string, autoBookmark: boolean = false): Promise<IngestUrlResult> {
    const result = await apiIngestUrl(url, topic, autoBookmark)
    if (result.id) {
      await load() // Refresh the list
    }
    return result
  }

  async function collect(topicId: number): Promise<CollectTopicResult> {
    const result = await apiCollectTopic(topicId)
    await load() // Refresh the list
    return result
  }

  return { articles, loading, filterTopic, filterKeyword, filterBookmarked, load, loadAll, bookmark, ingest, collect }
})

export const useReportStore = defineStore('reports', () => {
  const reports = ref<ReportItem[]>([])
  const loading = ref(false)

  async function load() {
    loading.value = true
    try {
      reports.value = await apiFetchReports()
    } finally {
      loading.value = false
    }
  }

  async function generate(topic?: string, title?: string) {
    const report = await apiGenerateReport(topic, title)
    reports.value = [report, ...reports.value]
    return report
  }

  return { reports, loading, load, generate }
})

export const useQaStore = defineStore('qa', () => {
  const result = ref<QaResponse | null>(null)
  const loading = ref(false)

  async function ask(question: string) {
    loading.value = true
    try {
      result.value = await apiAskQuestion(question)
    } finally {
      loading.value = false
    }
  }

  return { result, loading, ask }
})
