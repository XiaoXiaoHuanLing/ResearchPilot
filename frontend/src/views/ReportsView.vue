<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import {
  NCard, NButton, NSpace, NModal, NInput, NFormItem, NTag, NSpin, NEmpty, NCheckbox, NPopconfirm, useMessage,
} from 'naive-ui'
import { useReportStore, useTopicStore, useArticleStore } from '../stores'
import { getReportExportUrl } from '../api'
import type { ReportItem } from '../types'

const reportStore = useReportStore()
const topicStore = useTopicStore()
const articleStore = useArticleStore()
const message = useMessage()

const showGenerate = ref(false)
const generating = ref(false)
const genTitle = ref('')
const genPrompt = ref('')
const selectedArticleIds = ref<number[]>([])

const detailReport = ref<ReportItem | null>(null)
const showDetail = ref(false)

// Articles for selection in report generation
const bookmarkedArticles = computed(() => articleStore.articles.filter(a => a.bookmarked))

async function openGenerateModal() {
  // Load articles for selection
  if (articleStore.articles.length === 0) await articleStore.loadAll()
  selectedArticleIds.value = []
  genTitle.value = ''
  genPrompt.value = ''
  showGenerate.value = true
}

async function handleGenerate() {
  if (selectedArticleIds.value.length === 0) {
    message.warning('请至少选择一篇资讯')
    return
  }
  generating.value = true
  try {
    const report = await reportStore.generate({
      title: genTitle.value || null,
      article_ids: selectedArticleIds.value,
      prompt: genPrompt.value || null,
    })
    message.success('报告已生成：' + report.title)
    showGenerate.value = false
  } catch (e: any) {
    message.error(e?.message || '生成失败')
  } finally {
    generating.value = false
  }
}

async function handleDelete(id: number) {
  try {
    await reportStore.remove(id)
    message.success('报告已删除')
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  }
}

async function openDetail(report: ReportItem) {
  detailReport.value = report
  showDetail.value = true
  // Load content from API
  try {
    const { getReportContent } = await import('../api/reports')
    const res = await getReportContent(report.id)
    ;(detailReport.value as any)._content = res.content
  } catch (e: any) {
    console.warn('Failed to load report content:', e)
  }
}

function toggleArticleSelection(id: number) {
  const idx = selectedArticleIds.value.indexOf(id)
  if (idx >= 0) selectedArticleIds.value.splice(idx, 1)
  else selectedArticleIds.value.push(id)
}

function selectAll() {
  selectedArticleIds.value = bookmarkedArticles.value.map(a => a.id)
}

function selectNone() {
  selectedArticleIds.value = []
}

function copyReportText(report: ReportItem) {
  // V2: content loaded from file via API
  const text = '# ' + report.title + '\n\n' + ((detailReport.value as any)?._content || '')
  navigator.clipboard.writeText(text).then(() => message.success('已复制')).catch(() => message.error('复制失败'))
}

onMounted(async () => {
  try { await topicStore.load(); await reportStore.load() } catch (e) { console.error(e) }
})
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-2xl font-bold text-cyan-400">报告中心</h1>
        <p class="text-slate-400 text-sm mt-1">选择资讯 · 个性化生成 · 共 {{ reportStore.reports.length }} 份</p>
      </div>
      <n-button type="primary" @click="openGenerateModal">生成报告</n-button>
    </div>

    <n-spin :show="reportStore.loading">
      <n-empty v-if="reportStore.reports.length === 0 && !reportStore.loading" description="暂无报告" />
      <div class="space-y-4">
        <n-card v-for="report in reportStore.reports" :key="report.id" hoverable>
          <div class="flex items-start justify-between">
            <div class="flex-1 min-w-0 cursor-pointer" @click="openDetail(report)">
              <h3 class="text-lg font-semibold text-slate-100">{{ report.title }}</h3>
              <div class="text-xs text-slate-500 mt-1">{{ report.created_at }}<span v-if="report.topic"> · {{ report.topic }}</span></div>
            </div>
            <n-space>
              <n-button size="small" ghost @click="copyReportText(report)">复制</n-button>
              <a :href="getReportExportUrl(report.id, 'markdown')" target="_blank">
                <n-button size="small" ghost>MD</n-button>
              </a>
              <a :href="getReportExportUrl(report.id, 'pdf')" target="_blank">
                <n-button size="small" ghost>PDF</n-button>
              </a>
              <n-button size="small" type="primary" ghost @click="openDetail(report)">详情</n-button>
              <n-popconfirm @positive-click="handleDelete(report.id)">
                <template #trigger>
                  <n-button size="small" type="error" ghost>删除</n-button>
                </template>
                确定删除此报告？
              </n-popconfirm>
            </n-space>
          </div>
          <p class="text-sm text-slate-400 mt-3">
            <n-tag v-if="report.status === 'ready'" size="tiny" type="success" :bordered="false">已完成</n-tag>
            <n-tag v-else-if="report.status === 'generating'" size="tiny" type="info" :bordered="false">生成中</n-tag>
            <n-tag v-else-if="report.status === 'outline_ready'" size="tiny" type="warning" :bordered="false">待确认大纲</n-tag>
            <n-tag v-else size="tiny" :bordered="false">草稿</n-tag>
            <span v-if="report.quality_score" class="ml-2 text-slate-500">质量分: {{ report.quality_score }}</span>
          </p>
        </n-card>
      </div>
    </n-spin>

    <!-- Detail Modal -->
    <n-modal v-model:show="showDetail" preset="card" style="max-width:720px;" title="报告详情">
      <template v-if="detailReport">
        <h2 class="text-xl font-bold text-slate-100 mb-3">{{ detailReport.title }}</h2>
        <div class="text-sm text-slate-500 mb-4">{{ detailReport.created_at }}</div>
        <div class="bg-slate-800/50 rounded-lg p-5">
          <h3 class="text-sm font-semibold text-cyan-300 mb-2">报告内容</h3>
          <p v-if="(detailReport as any)?._content" class="text-slate-300 leading-relaxed whitespace-pre-line">{{ (detailReport as any)._content }}</p>
          <p v-else class="text-slate-500">加载中...</p>
        </div>
      </template>
      <template #footer>
        <n-space justify="end">
          <n-button v-if="detailReport" ghost @click="copyReportText(detailReport)">复制</n-button>
          <n-button @click="showDetail = false">关闭</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- Generate Modal -->
    <n-modal v-model:show="showGenerate" title="生成报告" preset="card" style="max-width:640px">
      <n-form>
        <n-form-item label="选择资讯（至少1篇）">
          <div class="w-full">
            <n-space class="mb-2">
              <n-button size="tiny" @click="selectAll">全选</n-button>
              <n-button size="tiny" @click="selectNone">清空</n-button>
              <span class="text-xs text-slate-500">已选 {{ selectedArticleIds.length }} 篇</span>
            </n-space>
            <div class="max-h-48 overflow-y-auto space-y-1">
              <div v-for="article in bookmarkedArticles" :key="article.id"
                class="flex items-center gap-2 py-1 px-2 rounded hover:bg-slate-700/40 cursor-pointer"
                @click="toggleArticleSelection(article.id)">
                <n-checkbox :checked="selectedArticleIds.includes(article.id)" @update:checked="toggleArticleSelection(article.id)" />
                <span class="text-sm text-slate-300 flex-1 truncate">{{ article.title }}</span>
                <n-tag size="tiny" :bordered="false">{{ article.topic }}</n-tag>
              </div>
              <n-empty v-if="bookmarkedArticles.length === 0" description="暂无收藏的资讯" size="small" />
            </div>
          </div>
        </n-form-item>
        <n-form-item label="报告标题（可选）">
          <n-input v-model:value="genTitle" placeholder="留空自动生成" />
        </n-form-item>
        <n-form-item label="个性化提示（可选）">
          <n-input v-model:value="genPrompt" type="textarea" :rows="2"
            placeholder="例如：重点关注技术突破方面，风格简洁，侧重分析而非罗列" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showGenerate = false">取消</n-button>
          <n-button type="primary" :loading="generating" :disabled="selectedArticleIds.length === 0" @click="handleGenerate">
            生成报告 (已选{{ selectedArticleIds.length }}篇资讯)
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>
