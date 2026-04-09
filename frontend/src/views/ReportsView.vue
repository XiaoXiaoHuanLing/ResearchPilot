<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  NCard, NButton, NSpace, NModal, NInput, NFormItem, NTag, NSpin, NEmpty, useMessage,
} from 'naive-ui'
import { useReportStore, useTopicStore } from '../stores'

const reportStore = useReportStore()
const topicStore = useTopicStore()
const message = useMessage()
const showGenerate = ref(false)
const generating = ref(false)

const genTopic = ref<string | null>(null)
const genTitle = ref('')

async function handleGenerate() {
  generating.value = true
  try {
    const report = await reportStore.generate(genTopic.value ?? undefined, genTitle.value || undefined)
    message.success(`报告已生成：${report.title}`)
    showGenerate.value = false
    genTopic.value = null
    genTitle.value = ''
  } catch (e) {
    message.error(e instanceof Error ? e.message : '生成失败')
  } finally {
    generating.value = false
  }
}

onMounted(async () => {
  if (topicStore.topics.length === 0) await topicStore.load()
  if (reportStore.reports.length === 0) await reportStore.load()
})
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-2xl font-bold text-cyan-400">报告中心</h1>
        <p class="text-slate-400 text-sm mt-1">基于收藏资讯自动生成研究报告</p>
      </div>
      <n-button type="primary" @click="showGenerate = true">生成报告</n-button>
    </div>

    <n-spin :show="reportStore.loading">
      <n-empty v-if="reportStore.reports.length === 0 && !reportStore.loading" description="暂无报告，点击右上角生成" />

      <div class="space-y-4">
        <n-card v-for="report in reportStore.reports" :key="report.id" hoverable>
          <div class="flex items-start justify-between">
            <div class="flex-1 min-w-0">
              <h3 class="text-lg font-semibold text-slate-100">{{ report.title }}</h3>
              <div class="text-xs text-slate-500 mt-1">{{ report.created_at }}</div>
            </div>
          </div>
          <p class="text-sm text-slate-400 mt-3 whitespace-pre-line break-words">{{ report.summary }}</p>
        </n-card>
      </div>
    </n-spin>

    <!-- Generate Modal -->
    <n-modal v-model:show="showGenerate" title="生成报告" preset="card" style="max-width: 480px">
      <n-form>
        <n-form-item label="专题筛选（可选）">
          <n-space>
            <n-tag
              v-for="topic in topicStore.topics"
              :key="topic.id"
              :type="genTopic === topic.name ? 'info' : 'default'"
              style="cursor: pointer"
              @click="genTopic = genTopic === topic.name ? null : topic.name"
            >
              {{ topic.name }}
            </n-tag>
          </n-space>
        </n-form-item>
        <n-form-item label="报告标题（可选）">
          <n-input v-model:value="genTitle" placeholder="留空将自动生成标题" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showGenerate = false">取消</n-button>
          <n-button type="primary" :loading="generating" @click="handleGenerate">生成</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>
