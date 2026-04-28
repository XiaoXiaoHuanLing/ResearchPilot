<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NCard, NGrid, NGi, NStatistic, NSpin, NAlert, NButton, NTag, NEmpty } from 'naive-ui'
import { useTopicStore, useArticleStore, useReportStore } from '../stores'
import { fetchSystemStatus } from '../api'
import type { SystemStatus } from '../types'

const topicStore = useTopicStore()
const articleStore = useArticleStore()
const reportStore = useReportStore()
const loading = ref(true)
const error = ref('')
const sysStatus = ref<SystemStatus | null>(null)

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const [_, __, ___, status] = await Promise.all([
      topicStore.load(),
      articleStore.loadAll(),
      reportStore.load(),
      fetchSystemStatus().catch(() => null),
    ])
    sysStatus.value = status
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

      <!-- System status card -->
      <n-card class="mb-4" size="small" title="⚙️ 系统能力状态">
        <n-grid :cols="4" :x-gap="12" item-responsive>
          <n-gi span="4 m:1">
            <div class="flex items-center gap-2">
              <n-tag type="success" size="small" :bordered="false">●</n-tag>
              <span class="text-sm text-slate-300">数据库</span>
            </div>
          </n-gi>
          <n-gi span="4 m:1">
            <div class="flex items-center gap-2">
              <n-tag :type="sysStatus?.llm_configured ? 'success' : 'warning'" size="small" :bordered="false">●</n-tag>
              <span class="text-sm text-slate-300">LLM / RAG</span>
              <span v-if="sysStatus?.llm_configured" class="text-xs text-cyan-400">{{ sysStatus.llm_model }}</span>
              <span v-else class="text-xs text-slate-500">未配置</span>
            </div>
          </n-gi>
          <n-gi span="4 m:1">
            <div class="flex items-center gap-2">
              <n-tag :type="sysStatus?.search_configured ? 'success' : 'warning'" size="small" :bordered="false">●</n-tag>
              <span class="text-sm text-slate-300">网络搜索</span>
              <span v-if="sysStatus?.search_provider" class="text-xs text-cyan-400">{{ sysStatus.search_provider }}</span>
              <span v-else class="text-xs text-slate-500">未配置</span>
            </div>
          </n-gi>
          <n-gi span="4 m:1">
            <div class="flex items-center gap-2">
              <n-tag type="success" size="small" :bordered="false">●</n-tag>
              <span class="text-sm text-slate-300">调度器</span>
            </div>
          </n-gi>
        </n-grid>
        <n-alert v-if="sysStatus && !sysStatus.llm_configured" type="info" class="mt-3" :bordered="false">
          配置 <strong>RESEARCHPILOT_OPENAI_API_KEY</strong> 后将启用 LlamaIndex RAG 问答和 LLM 报告分析。配置 <strong>RESEARCHPILOT_TAVILY_API_KEY</strong> 或 <strong>SERPER_API_KEY</strong> 后将启用真实网络采集。
        </n-alert>
      </n-card>

      <!-- Stat cards -->
      <n-grid :cols="4" :x-gap="16" :y-gap="16" responsive="screen" item-responsive>
        <n-gi span="4 m:1">
          <n-card>
            <n-statistic label="专题数">
              <template #prefix>📋</template>
              {{ topicStore.topics.length }}
            </n-statistic>
          </n-card>
        </n-gi>
        <n-gi span="4 m:1">
          <n-card>
            <n-statistic label="资讯数">
              <template #prefix>📰</template>
              {{ articleStore.articles.length }}
            </n-statistic>
          </n-card>
        </n-gi>
        <n-gi span="4 m:1">
          <n-card>
            <n-statistic label="收藏数">
              <template #prefix>⭐</template>
              {{ articleStore.articles.filter(a => a.bookmarked).length }}
            </n-statistic>
          </n-card>
        </n-gi>
        <n-gi span="4 m:1">
          <n-card>
            <n-statistic label="报告数">
              <template #prefix>📊</template>
              {{ reportStore.reports.length }}
            </n-statistic>
          </n-card>
        </n-gi>
      </n-grid>

      <!-- Content grid -->
      <n-grid :cols="3" :x-gap="16" :y-gap="16" responsive="screen" item-responsive class="mt-4">
        <!-- Recent articles -->
        <n-gi span="3 m:1">
          <n-card title="📰 最近资讯" size="small">
            <div v-for="article in articleStore.articles.slice(0, 6)" :key="article.id" class="py-3 border-b border-slate-700/40 last:border-0">
              <div class="flex items-center gap-2 flex-wrap">
                <n-tag size="small" :bordered="false" type="info">{{ article.topic }}</n-tag>
                <span class="text-slate-500 text-xs">{{ article.published_at }}</span>
                <n-tag v-if="article.bookmarked" size="small" type="warning" :bordered="false">⭐</n-tag>
              </div>
              <h4 class="mt-1 text-slate-200 font-medium text-sm">{{ article.title }}</h4>
              <p class="text-xs text-slate-400 mt-1 line-clamp-2">{{ article.summary }}</p>
            </div>
            <n-empty v-if="articleStore.articles.length === 0" description="暂无资讯" size="small" />
          </n-card>
        </n-gi>

        <!-- Active topics -->
        <n-gi span="3 m:1">
          <n-card title="🎯 活跃专题" size="small">
            <div v-for="topic in topicStore.topics.filter(t => t.enabled).slice(0, 5)" :key="topic.id" class="py-3 border-b border-slate-700/40 last:border-0">
              <div class="flex items-center justify-between">
                <h4 class="text-slate-200 font-medium text-sm">{{ topic.name }}</h4>
                <n-tag type="success" size="small" :bordered="false">运行中</n-tag>
              </div>
              <p class="text-xs text-slate-400 mt-1 line-clamp-2">{{ topic.description }}</p>
              <div class="flex flex-wrap gap-1 mt-2">
                <n-tag v-for="kw in topic.keywords.slice(0, 4)" :key="kw" size="tiny" :bordered="false" type="info">{{ kw }}</n-tag>
              </div>
              <div class="text-xs text-slate-500 mt-1">⏰ {{ topic.schedule }}</div>
            </div>
            <n-empty v-if="topicStore.topics.length === 0" description="暂无专题" size="small" />
          </n-card>
        </n-gi>

        <!-- Recent reports -->
        <n-gi span="3 m:1">
          <n-card title="📊 最新报告" size="small">
            <div v-for="report in reportStore.reports.slice(0, 5)" :key="report.id" class="py-3 border-b border-slate-700/40 last:border-0">
              <div class="flex items-center justify-between">
                <h4 class="text-slate-200 font-medium text-sm">{{ report.title }}</h4>
                <span class="text-xs text-slate-500">{{ report.created_at }}</span>
              </div>
              <p class="text-xs text-slate-400 mt-1 line-clamp-3">
                <n-tag size="tiny" :bordered="false" :type="report.status === 'ready' ? 'success' : 'warning'">{{ report.status }}</n-tag>
                <span v-if="report.quality_score" class="ml-1">质量: {{ report.quality_score }}</span>
              </p>
            </div>
            <n-empty v-if="reportStore.reports.length === 0" description="暂无报告" size="small" />
          </n-card>
        </n-gi>
      </n-grid>
    </n-spin>
  </div>
</template>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.line-clamp-3 {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
