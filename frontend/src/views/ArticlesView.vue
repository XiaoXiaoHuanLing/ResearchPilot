<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import {
  NCard, NInput, NButton, NSpace, NTag, NSwitch, NSpin, NEmpty,
  NModal, NFormItem, NPopconfirm, useMessage,
} from 'naive-ui'
import { useArticleStore, useTopicStore } from '../stores'
import type { Article } from '../types'

const articleStore = useArticleStore()
const topicStore = useTopicStore()
const message = useMessage()

const keywordFilter = ref('')
const topicFilter = ref<string | null>(null)
const onlyBookmarked = ref(false)
const bookmarkLoading = ref<number | null>(null)
const searchDebounce = ref<ReturnType<typeof setTimeout> | null>(null)

const showIngest = ref(false)
const ingestUrl = ref('')
const ingestTopic = ref('')
const ingestLoading = ref(false)
const collectLoadingId = ref<number | null>(null)
const detailArticle = ref<Article | null>(null)
const showDetail = ref(false)

async function loadArticles() {
  articleStore.filterKeyword = keywordFilter.value || null
  articleStore.filterTopic = topicFilter.value
  articleStore.filterBookmarked = onlyBookmarked.value ? true : null
  await articleStore.load()
}

async function handleBookmark(article: Article, value: boolean) {
  bookmarkLoading.value = article.id
  try {
    await articleStore.bookmark(article.id, value)
    message.success(value ? '已收藏' : '已取消收藏')
  } catch (e: any) {
    message.error(e?.message || '操作失败')
  } finally {
    bookmarkLoading.value = null
  }
}

async function handleDelete(article: Article) {
  try {
    await articleStore.remove(article.id)
    message.success('已删除')
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  }
}

async function handleIngest() {
  if (!ingestUrl.value || !ingestTopic.value) {
    message.warning('请输入URL和选择专题')
    return
  }
  ingestLoading.value = true
  try {
    const result = await articleStore.ingest(ingestUrl.value, ingestTopic.value)
    if (result.id) {
      message.success(result.message)
      showIngest.value = false
      ingestUrl.value = ''
    } else {
      message.warning(result.message)
    }
  } catch (e: any) {
    message.error(e?.message || '采集失败')
  } finally {
    ingestLoading.value = false
  }
}

async function handleCollect(topicId: number) {
  collectLoadingId.value = topicId
  try {
    const result = await articleStore.collect(topicId)
    message.info(result.message)
  } catch (e: any) {
    message.error(e?.message || '采集失败')
  } finally {
    collectLoadingId.value = null
  }
}

function onKeywordInput() {
  if (searchDebounce.value) clearTimeout(searchDebounce.value)
  searchDebounce.value = setTimeout(() => loadArticles(), 400)
}

function openDetail(article: Article) {
  detailArticle.value = article
  showDetail.value = true
}

watch(topicFilter, () => loadArticles())
watch(onlyBookmarked, () => loadArticles())

onMounted(async () => {
  try {
    await topicStore.load()
    await loadArticles()
  } catch (e) { console.error('ArticlesView mount error:', e) }
})
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-cyan-400">资讯中心</h1>
      <n-space>
        <n-button type="primary" @click="showIngest = true">手动采集URL</n-button>
      </n-space>
    </div>

    <!-- Topic collect -->
    <n-card class="mb-4" size="small">
      <div class="mb-3 text-sm text-slate-400">专题快速采集</div>
      <n-space>
        <n-button v-for="topic in topicStore.topics.filter(t => t.enabled)" :key="topic.id"
          :loading="collectLoadingId === topic.id" size="small" ghost @click="handleCollect(topic.id)">
          采集「{{ topic.name }}」
        </n-button>
      </n-space>
    </n-card>

    <!-- Filters -->
    <n-card class="mb-4" size="small">
      <div class="flex items-center gap-4 flex-wrap">
        <n-input v-model:value="keywordFilter" placeholder="搜索标题/摘要..." clearable style="max-width:300px"
          @input="onKeywordInput" @clear="loadArticles" />
        <n-space>
          <n-tag v-for="topic in topicStore.topics" :key="topic.id"
            :type="topicFilter === topic.name ? 'info' : 'default'" style="cursor:pointer"
            @click="topicFilter = topicFilter === topic.name ? null : topic.name">{{ topic.name }}</n-tag>
        </n-space>
        <div class="flex items-center gap-2 ml-auto">
          <span class="text-sm text-slate-400">仅看收藏</span>
          <n-switch v-model:value="onlyBookmarked" size="small" />
        </div>
      </div>
    </n-card>

    <!-- Articles list -->
    <n-spin :show="articleStore.loading">
      <n-empty v-if="articleStore.articles.length === 0 && !articleStore.loading" description="没有匹配的资讯" />
      <div class="space-y-3">
        <n-card v-for="article in articleStore.articles" :key="article.id" hoverable size="small">
          <div class="flex items-start justify-between gap-3">
            <div class="flex-1 min-w-0 cursor-pointer" @click="openDetail(article)">
              <div class="flex items-center gap-2 mb-1 flex-wrap">
                <n-tag size="small" :bordered="false" type="info">{{ article.topic }}</n-tag>
                <span class="text-xs text-slate-500">{{ article.published_at }}</span>
                <n-tag v-if="article.bookmarked" size="small" type="warning" :bordered="false">已收藏</n-tag>
                <!-- V2: quality badge -->
                <n-tag v-if="article.quality_label === 'high'" size="small" type="success" :bordered="false">高质量</n-tag>
                <n-tag v-else-if="article.quality_label === 'medium'" size="small" :bordered="false">中等</n-tag>
                <n-tag v-else-if="article.quality_label === 'low'" size="small" type="error" :bordered="false">低质量</n-tag>
                <!-- V2: expires indicator -->
                <n-tag v-if="!article.bookmarked && article.expires_at" size="small" :bordered="false" type="default">
                  {{ article.expires_at }} 过期
                </n-tag>
              </div>
              <h3 class="text-base font-semibold text-slate-100 break-words">{{ article.title }}</h3>
              <p class="text-sm text-slate-400 mt-1 break-words">{{ article.summary }}</p>
              <div class="text-xs text-slate-500 mt-2">
                来源：{{ article.source }}
                <a v-if="article.url" :href="article.url" target="_blank" class="ml-2 text-cyan-400 hover:underline">原文</a>
              </div>
            </div>
            <n-space vertical size="small">
              <n-button :type="article.bookmarked ? 'warning' : 'default'" :ghost="!article.bookmarked" size="small"
                :loading="bookmarkLoading === article.id"
                @click.stop="handleBookmark(article, !article.bookmarked)">
                {{ article.bookmarked ? '取消收藏' : '收藏' }}
              </n-button>
              <n-popconfirm @positive-click="handleDelete(article)">
                <template #trigger>
                  <n-button size="small" type="error" ghost @click.stop>删除</n-button>
                </template>
                确定删除此资讯？此操作不可逆。
              </n-popconfirm>
            </n-space>
          </div>
        </n-card>
      </div>
    </n-spin>

    <!-- Detail Modal -->
    <n-modal v-model:show="showDetail" preset="card" style="max-width:720px;" title="资讯详情">
      <template v-if="detailArticle">
        <div class="mb-3 flex items-center gap-2 flex-wrap">
          <n-tag size="small" :bordered="false" type="info">{{ detailArticle.topic }}</n-tag>
          <span class="text-xs text-slate-500">{{ detailArticle.published_at }}</span>
          <n-tag v-if="detailArticle.bookmarked" size="small" type="warning" :bordered="false">已收藏</n-tag>
        </div>
        <h2 class="text-xl font-bold text-slate-100 mb-3">{{ detailArticle.title }}</h2>
        <div class="text-sm text-slate-500 mb-4">
          来源：{{ detailArticle.source }}
          <a v-if="detailArticle.url" :href="detailArticle.url" target="_blank" class="ml-2 text-cyan-400 hover:underline">原文</a>
        </div>
        <div class="bg-slate-800/50 rounded-lg p-4 mb-4">
          <h3 class="text-sm font-semibold text-cyan-300 mb-2">摘要</h3>
          <p class="text-slate-300 leading-relaxed">{{ detailArticle.summary }}</p>
        </div>
        <div v-if="detailArticle.content" class="bg-slate-800/50 rounded-lg p-4">
          <h3 class="text-sm font-semibold text-cyan-300 mb-2">全文</h3>
          <p class="text-slate-300 leading-relaxed whitespace-pre-line">{{ detailArticle.content }}</p>
        </div>
      </template>
    </n-modal>

    <!-- Ingest URL Modal — V2: 收藏≠入KB，去掉auto_bookmark -->
    <n-modal v-model:show="showIngest" title="手动采集URL" preset="card" style="max-width:520px">
      <n-form>
        <n-form-item label="网页URL">
          <n-input v-model:value="ingestUrl" placeholder="https://example.com/article" />
        </n-form-item>
        <n-form-item label="归属专题">
          <n-space>
            <n-tag v-for="topic in topicStore.topics" :key="topic.id"
              :type="ingestTopic === topic.name ? 'info' : 'default'" style="cursor:pointer"
              @click="ingestTopic = topic.name">{{ topic.name }}</n-tag>
          </n-space>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showIngest = false">取消</n-button>
          <n-button type="primary" :loading="ingestLoading" @click="handleIngest">开始采集</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>
