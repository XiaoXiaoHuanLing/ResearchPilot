<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NCard, NGrid, NGi, NStatistic, NSpin, NAlert, NButton, NTag } from 'naive-ui'
import { useTopicStore, useArticleStore, useReportStore } from '../stores'

const topicStore = useTopicStore()
const articleStore = useArticleStore()
const reportStore = useReportStore()
const loading = ref(true)
const error = ref('')

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    await Promise.all([topicStore.load(), articleStore.loadAll(), reportStore.load()])
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => refresh())
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-3xl font-bold text-cyan-400">ResearchPilot</h1>
        <p class="text-slate-400 mt-1">面向公开专题研究的 AI 助手：主动追踪 · 知识沉淀 · 问答分析 · 报告生成</p>
      </div>
      <n-button type="primary" ghost @click="refresh" :loading="loading">刷新数据</n-button>
    </div>

    <n-spin :show="loading">
      <n-alert v-if="error" type="error" class="mb-4">{{ error }}</n-alert>

      <n-grid :cols="4" :x-gap="16" :y-gap="16" responsive="screen" item-responsive>
        <n-gi span="4 m:1">
          <n-card><n-statistic label="专题数" :value="topicStore.topics.length" /></n-card>
        </n-gi>
        <n-gi span="4 m:1">
          <n-card><n-statistic label="资讯数" :value="articleStore.articles.length" /></n-card>
        </n-gi>
        <n-gi span="4 m:1">
          <n-card><n-statistic label="收藏数" :value="articleStore.articles.filter(a => a.bookmarked).length" /></n-card>
        </n-gi>
        <n-gi span="4 m:1">
          <n-card><n-statistic label="报告数" :value="reportStore.reports.length" /></n-card>
        </n-gi>
      </n-grid>

      <n-grid :cols="2" :x-gap="16" :y-gap="16" responsive="screen" item-responsive class="mt-4">
        <n-gi span="2 m:1">
          <n-card title="最近资讯">
            <div v-for="article in articleStore.articles.slice(0, 5)" :key="article.id" class="py-3 border-b border-slate-700/40 last:border-0">
              <div class="flex items-center gap-2 flex-wrap">
                <n-tag size="small" :bordered="false" type="info">{{ article.topic }}</n-tag>
                <span class="text-slate-500 text-xs">{{ article.published_at }}</span>
                <n-tag v-if="article.bookmarked" size="small" type="warning" :bordered="false">已收藏</n-tag>
              </div>
              <h4 class="mt-1 text-slate-200 font-medium">{{ article.title }}</h4>
              <p class="text-sm text-slate-400 mt-1">{{ article.summary }}</p>
            </div>
            <n-empty v-if="articleStore.articles.length === 0" description="暂无资讯" size="small" />
          </n-card>
        </n-gi>

        <n-gi span="2 m:1">
          <n-card title="活跃专题">
            <div v-for="topic in topicStore.topics.filter(t => t.enabled).slice(0, 5)" :key="topic.id" class="py-3 border-b border-slate-700/40 last:border-0">
              <div class="flex items-center justify-between">
                <h4 class="text-slate-200 font-medium">{{ topic.name }}</h4>
                <n-tag type="success" size="small" :bordered="false">运行中</n-tag>
              </div>
              <p class="text-sm text-slate-400 mt-1">{{ topic.description }}</p>
              <div class="flex flex-wrap gap-1 mt-2">
                <n-tag v-for="kw in topic.keywords.slice(0, 4)" :key="kw" size="tiny" :bordered="false" type="info">{{ kw }}</n-tag>
              </div>
            </div>
            <n-empty v-if="topicStore.topics.length === 0" description="暂无专题" size="small" />
          </n-card>
        </n-gi>
      </n-grid>
    </n-spin>
  </div>
</template>
