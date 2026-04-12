<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { NCard, NButton, NSpace, NModal, NForm, NFormItem, NInput, NTag, NPopconfirm, NSwitch, NSpin, NEmpty, useMessage } from 'naive-ui'
import { useTopicStore, useArticleStore } from '../stores'
import type { Topic, TopicCreatePayload } from '../types'

const topicStore = useTopicStore()
const articleStore = useArticleStore()
const message = useMessage()
const showModal = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)

const form = ref<TopicCreatePayload>({
  name: '',
  description: '',
  keywords: [],
  schedule: '每天 09:00',
  enabled: true,
})
const keywordInput = ref('')

function parseKeywords(input: string): string[] {
  return input.split(/[，,]/).map((s) => s.trim()).filter(Boolean)
}

function openCreate() {
  editingId.value = null
  form.value = { name: '', description: '', keywords: [], schedule: '每天 09:00', enabled: true }
  keywordInput.value = ''
  showModal.value = true
}

function openEdit(topic: Topic) {
  editingId.value = topic.id
  form.value = {
    name: topic.name,
    description: topic.description,
    keywords: topic.keywords,
    schedule: topic.schedule,
    enabled: topic.enabled,
  }
  keywordInput.value = topic.keywords.join(', ')
  showModal.value = true
}

async function handleSubmit() {
  const payload = { ...form.value, keywords: parseKeywords(keywordInput.value) }
  if (!payload.name || !payload.description || payload.keywords.length === 0) {
    message.warning('请填写专题名称、描述和关键词')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await topicStore.update(editingId.value, payload)
      message.success('专题已更新')
    } else {
      await topicStore.create(payload)
      message.success('专题已创建')
    }
    showModal.value = false
  } catch (e: any) {
    message.error(e?.message || '操作失败')
  } finally {
    saving.value = false
  }
}

async function handleDelete(id: number) {
  try {
    await topicStore.remove(id)
    message.success('专题已删除')
  } catch (e: any) {
    message.error(e?.message || '删除失败')
  }
}

function getArticleCountForTopic(topicName: string): number {
  return articleStore.articles.filter(a => a.topic === topicName).length
}

function getBookmarkedCountForTopic(topicName: string): number {
  return articleStore.articles.filter(a => a.topic === topicName && a.bookmarked).length
}

const topicStats = computed(() => {
  const enabled = topicStore.topics.filter(t => t.enabled).length
  const disabled = topicStore.topics.filter(t => !t.enabled).length
  return { enabled, disabled, total: topicStore.topics.length }
})

onMounted(async () => {
  try {
    await topicStore.load()
    if (articleStore.articles.length === 0) await articleStore.loadAll()
  } catch (e) {
    console.error('TopicsView mount error:', e)
  }
})
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-2xl font-bold text-cyan-400">专题管理</h1>
        <p class="text-slate-400 text-sm mt-1">共 {{ topicStats.total }} 个专题 · {{ topicStats.enabled }} 运行中 · {{ topicStats.disabled }} 已停用</p>
      </div>
      <n-button type="primary" @click="openCreate">+ 新增专题</n-button>
    </div>

    <n-spin :show="topicStore.loading">
      <n-empty v-if="topicStore.topics.length === 0 && !topicStore.loading" description="暂无专题，点击右上角创建" />

      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <n-card v-for="topic in topicStore.topics" :key="topic.id" hoverable>
          <div class="flex items-center justify-between">
            <h3 class="text-lg font-semibold text-slate-100">{{ topic.name }}</h3>
            <n-tag :type="topic.enabled ? 'success' : 'default'" size="small" :bordered="false">
              {{ topic.enabled ? '运行中' : '已停用' }}
            </n-tag>
          </div>
          <p class="text-slate-400 text-sm mt-2">{{ topic.description }}</p>
          <div class="flex flex-wrap gap-1.5 mt-3">
            <n-tag v-for="kw in topic.keywords" :key="kw" size="small" :bordered="false" type="info">{{ kw }}</n-tag>
          </div>
          <div class="text-xs text-slate-500 mt-2">调度：{{ topic.schedule }}</div>

          <!-- Stats bar -->
          <div class="mt-3 pt-3 border-t border-slate-700/40">
            <div class="flex items-center justify-between text-xs text-slate-400 mb-1">
              <span>资讯 {{ getArticleCountForTopic(topic.name) }} 篇 · 收藏 {{ getBookmarkedCountForTopic(topic.name) }} 篇</span>
            </div>
          </div>

          <n-space class="mt-3">
            <n-button size="small" ghost @click="openEdit(topic)">编辑</n-button>
            <n-popconfirm @positive-click="handleDelete(topic.id)">
              <template #trigger>
                <n-button size="small" type="error" ghost>删除</n-button>
              </template>
              确定删除专题"{{ topic.name }}"吗？
            </n-popconfirm>
          </n-space>
        </n-card>
      </div>
    </n-spin>

    <!-- Create/Edit Modal -->
    <n-modal v-model:show="showModal" :title="editingId ? '编辑专题' : '新增专题'" preset="card" style="max-width: 560px">
      <n-form>
        <n-form-item label="专题名称">
          <n-input v-model:value="form.name" placeholder="例如：无人机产业追踪" />
        </n-form-item>
        <n-form-item label="专题描述">
          <n-input v-model:value="form.description" type="textarea" :rows="3" placeholder="说明该专题要跟踪什么公开信息..." />
        </n-form-item>
        <n-form-item label="关键词">
          <n-input v-model:value="keywordInput" placeholder="用逗号分隔，例如：无人机, 察打一体, 航空工业" />
        </n-form-item>
        <n-form-item label="调度计划">
          <n-input v-model:value="form.schedule" placeholder="例如：每天 09:00 / 21:00" />
        </n-form-item>
        <n-form-item label="启用状态">
          <n-switch v-model:value="form.enabled" />
          <span class="ml-2 text-sm text-slate-400">{{ form.enabled ? '创建后立即启用' : '创建后暂停' }}</span>
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showModal = false">取消</n-button>
          <n-button type="primary" :loading="saving" @click="handleSubmit">
            {{ editingId ? '保存修改' : '创建专题' }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>
