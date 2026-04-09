<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import {
  NCard, NInput, NButton, NSpace, NTag, NSwitch, NSpin, NEmpty,
  NModal, NFormItem, NCheckbox, useMessage,
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

// Ingest URL modal
const showIngest = ref(false)
const ingestUrl = ref('')
const ingestTopic = ref('')
const ingestAutoBookmark = ref(true)
const ingestLoading = ref(false)

// Collect loading state
const collectLoadingId = ref<number | null>(null)

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
  } catch (e) {
    message.error(e instanceof Error ? e.message : '操作失败')
  } finally {
    bookmarkLoading.value = null
  }
}

async function handleIngest() {
  if (!ingestUrl.value || !ingestTopic.value) {
    message.warning('请输入URL和选择专题')
    return
  }
  ingestLoading.value = true
  try {
    const result = await articleStore.ingest(ingestUrl.value, ingestTopic.value, ingestAutoBookmark.value)
    if (result.id) {
      message.success(result.message)
      showIngest.value = false
      ingestUrl.value = ''
    } else {
      message.warning(result.message)
    }
  } catch (e) {
    message.error(e instanceof Error ? e.message : '采集失败')
  } finally {
    ingestLoading.value = false
  }
}

async function handleCollect(topicId: number) {
  collectLoadingId.value = topicId
  try {
    const result = await articleStore.collect(topicId)
    message.info(result.message)
  } catch (e) {
    message.error(e instanceof Error ? e.message : '采集失败')
  } finally {
    collectLoadingId.value = null
  }
}

function onKeywordInput() {
  if (searchDebounce.value) clearTimeout(searchDebounce.value)
  searchDebounce.value = setTimeout(() => loadArticles(), 400)
}

watch(topicFilter, () => loadArticles())
watch(onlyBookmarked, () => loadArticles())

onMounted(async () => {
  if (topicStore.topics.length === 0) await topicStore.load()
  await loadArticles()
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

    <!-- Topic collect buttons -->
    <n-card class="mb-4">
      <div class="mb-3 text-sm text-slate-400">专题快速采集</div>
      <n-space>
        <n-button
          v-for="topic in topicStore.topics.filter(t => t.enabled)"
          :key="topic.id"
          :loading="collectLoadingId === topic.id"
          size="small"
          ghost
          @click="handleCollect(topic.id)"
        >
          采集「{{ topic.name }}」
        </n-button>
      </n-space>
    </n-card>

    <!-- Filters -->
    <n-card class="mb-4">
      <div class="flex items-center gap-4 flex-wrap">
        <n-input
          v-model:value="keywordFilter"
          placeholder="搜索标题/摘要..."
          clearable
          style="max-width: 300px"
          @input="onKeywordInput"
          @clear="loadArticles"
        />
        <n-space>
          <n-tag
            v-for="topic in topicStore.topics"
            :key="topic.id"
            :type="topicFilter === topic.name ? 'info' : 'default'"
            style="cursor: pointer"
            @click="topicFilter = topicFilter === topic.name ? null : topic.name"
          >
            {{ topic.name }}
          </n-tag>
        </n-space>
        <div class="flex items-center gap-2 ml-auto">
          <span class="text-sm text-slate-400">仅看收藏</span>
          <n-switch v-model:value="onlyBookmarked" size="small" />
        </div>
      </div>
    </n-card>

    <!-- Articles list -->
    <n-spin :show="articleStore.loading">
      <n-empty v-if="articleStore.articles.length === 0 && !articleStore.loading" description="没有匹配的资讯记录" />

      <div class="space-y-3">
        <n-card v-for="article in articleStore.articles" :key="article.id" hoverable>
          <div class="flex items-start justify-between gap-3">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 mb-1 flex-wrap">
                <n-tag size="small" :bordered="false" type="info">{{ article.topic }}</n-tag>
                <span class="text-xs text-slate-500">{{ article.published_at }}</span>
                <n-tag v-if="article.bookmarked" size="small" type="warning" :bordered="false">已收藏</n-tag>
              </div>
              <h3 class="text-base font-semibold text-slate-100 break-words">{{ article.title }}</h3>
              <p class="text-sm text-slate-400 mt-1 break-words">{{ article.summary }}</p>
              <div class="text-xs text-slate-500 mt-2">
                来源：{{ article.source }}
                <a v-if="article.url" :href="article.url" target="_blank" class="ml-2 text-cyan-400 hover:underline">原文链接</a>
              </div>
            </div>
            <n-button
              :type="article.bookmarked ? 'warning' : 'default'"
              :ghost="!article.bookmarked"
              size="small"
              :loading="bookmarkLoading === article.id"
              @click="handleBookmark(article, !article.bookmarked)"
            >
              {{ article.bookmarked ? '取消收藏' : '收藏' }}
            </n-button>
          </div>
        </n-card>
      </div>
    </n-spin>

    <!-- Ingest URL Modal -->
    <n-modal v-model:show="showIngest" title="手动采集URL" preset="card" style="max-width: 520px">
      <n-form>
        <n-form-item label="网页URL">
          <n-input v-model:value="ingestUrl" placeholder="https://example.com/article" />
        </n-form-item>
        <n-form-item label="归属专题">
          <n-space>
            <n-tag
              v-for="topic in topicStore.topics"
              :key="topic.id"
              :type="ingestTopic === topic.name ? 'info' : 'default'"
              style="cursor: pointer"
              @click="ingestTopic = topic.name"
            >
              {{ topic.name }}
            </n-tag>
          </n-space>
        </n-form-item>
        <n-form-item label="采集后自动收藏">
          <n-checkbox v-model:checked="ingestAutoBookmark">自动收藏并加入知识库</n-checkbox>
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
