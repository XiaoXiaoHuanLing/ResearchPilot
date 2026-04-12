import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  fetchTopics as apiFetchTopics,
  createTopic as apiCreateTopic,
  updateTopic as apiUpdateTopic,
  deleteTopic as apiDeleteTopic,
  fetchArticles as apiFetchArticles,
  toggleBookmark as apiToggleBookmark,
  deleteArticle as apiDeleteArticle,
  ingestUrl as apiIngestUrl,
  collectTopic as apiCollectTopic,
  fetchReports as apiFetchReports,
  generateReport as apiGenerateReport,
  deleteReport as apiDeleteReport,
  fetchKnowledgeBases as apiFetchKBs,
  createKnowledgeBase as apiCreateKB,
  deleteKnowledgeBase as apiDeleteKB,
  fetchKbDocuments as apiFetchKbDocs,
  uploadKbDocument as apiUploadKbDoc,
  uploadKbFile as apiUploadKbFile,
  deleteKbDocument as apiDeleteKbDoc,
} from '../api'
import type { Topic, TopicCreatePayload, Article, ReportItem, KnowledgeBase, KbDocument, IngestUrlResult, CollectTopicResult } from '../types'

export const useTopicStore = defineStore('topics', () => {
  const topics = ref<Topic[]>([])
  const loading = ref(false)

  async function load() {
    loading.value = true
    try { topics.value = await apiFetchTopics() } finally { loading.value = false }
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

  async function load() {
    loading.value = true
    try {
      articles.value = await apiFetchArticles({
        topic: filterTopic.value, keyword: filterKeyword.value, bookmarked: filterBookmarked.value,
      })
    } finally { loading.value = false }
  }

  async function loadAll() {
    filterTopic.value = null; filterKeyword.value = null; filterBookmarked.value = null
    loading.value = true
    try { articles.value = await apiFetchArticles() } finally { loading.value = false }
  }

  async function bookmark(id: number, bookmarked: boolean) {
    const updated = await apiToggleBookmark(id, bookmarked)
    articles.value = articles.value.map((a) => (a.id === updated.id ? updated : a))
    return updated
  }

  async function remove(id: number) {
    await apiDeleteArticle(id)
    articles.value = articles.value.filter((a) => a.id !== id)
  }

  async function ingest(url: string, topic: string, autoBookmark: boolean = false): Promise<IngestUrlResult> {
    const result = await apiIngestUrl(url, topic, autoBookmark)
    if (result.id) await load()
    return result
  }

  async function collect(topicId: number): Promise<CollectTopicResult> {
    const result = await apiCollectTopic(topicId)
    await load()
    return result
  }

  return { articles, loading, filterTopic, filterKeyword, filterBookmarked, load, loadAll, bookmark, remove, ingest, collect }
})

export const useReportStore = defineStore('reports', () => {
  const reports = ref<ReportItem[]>([])
  const loading = ref(false)

  async function load() {
    loading.value = true
    try { reports.value = await apiFetchReports() } finally { loading.value = false }
  }

  async function generate(payload: { title?: string | null; article_ids?: number[] | null; prompt?: string | null }) {
    const report = await apiGenerateReport(payload)
    reports.value = [report, ...reports.value]
    return report
  }

  async function remove(id: number) {
    await apiDeleteReport(id)
    reports.value = reports.value.filter((r) => r.id !== id)
  }

  return { reports, loading, load, generate, remove }
})

export const useKbStore = defineStore('knowledgeBases', () => {
  const knowledgeBases = ref<KnowledgeBase[]>([])
  const loading = ref(false)
  const currentDocs = ref<KbDocument[]>([])

  async function load() {
    loading.value = true
    try { knowledgeBases.value = await apiFetchKBs() } finally { loading.value = false }
  }

  async function create(name: string, description: string = '') {
    const kb = await apiCreateKB(name, description)
    knowledgeBases.value = [kb, ...knowledgeBases.value]
    return kb
  }

  async function remove(id: number) {
    await apiDeleteKB(id)
    knowledgeBases.value = knowledgeBases.value.filter((kb) => kb.id !== id)
  }

  async function loadDocuments(kbId: number) {
    currentDocs.value = await apiFetchKbDocs(kbId)
  }

  async function uploadDocument(kbId: number, title: string, content: string) {
    const doc = await apiUploadKbDoc(kbId, title, content)
    currentDocs.value = [doc, ...currentDocs.value]
    // Update count
    const kb = knowledgeBases.value.find(k => k.id === kbId)
    if (kb) kb.article_count++
    return doc
  }

  async function uploadFile(kbId: number, file: File) {
    const doc = await apiUploadKbFile(kbId, file)
    currentDocs.value = [doc, ...currentDocs.value]
    const kb = knowledgeBases.value.find(k => k.id === kbId)
    if (kb) kb.article_count++
    return doc
  }

  async function removeDocument(kbId: number, docId: number) {
    await apiDeleteKbDoc(kbId, docId)
    currentDocs.value = currentDocs.value.filter(d => d.id !== docId)
    const kb = knowledgeBases.value.find(k => k.id === kbId)
    if (kb) kb.article_count = Math.max(0, kb.article_count - 1)
  }

  return { knowledgeBases, loading, currentDocs, load, create, remove, loadDocuments, uploadDocument, uploadFile, removeDocument }
})
