<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NCard, NButton, NSpace, NTag, NSpin, NEmpty, NModal, NInput, NFormItem, NPopconfirm, NSwitch, NSelect, useMessage } from 'naive-ui'
import { useKbStore } from '../stores'
import { fetchReports, indexReportToKb } from '../api/reports'
import type { KnowledgeBase, ReportItem } from '../types'

const kbStore = useKbStore()
const message = useMessage()

const showCreate = ref(false)
const newKbName = ref('')
const newKbDesc = ref('')
const creating = ref(false)

// Document view
const viewingKb = ref<KnowledgeBase | null>(null)
const showDocs = ref(false)

// Upload modal
const showUpload = ref(false)
const uploadKbId = ref(0)
const uploading = ref(false)
const fileInputRef = ref<HTMLInputElement | null>(null)

// Report-to-KB modal
const showReportIngest = ref(false)
const reportIngestKbId = ref(0)
const availableReports = ref<ReportItem[]>([])
const selectedReportId = ref<number | null>(null)
const reportIngestLoading = ref(false)

async function handleCreate() {
  if (!newKbName.value.trim()) { message.warning('请输入知识库名称'); return }
  creating.value = true
  try {
    await kbStore.create(newKbName.value, newKbDesc.value)
    message.success('知识库已创建')
    showCreate.value = false
    newKbName.value = ''; newKbDesc.value = ''
  } catch (e: any) { message.error(e?.message || '创建失败') }
  finally { creating.value = false }
}

async function handleDelete(id: number) {
  try { await kbStore.remove(id); message.success('已删除') }
  catch (e: any) { message.error(e?.message || '删除失败') }
}

async function handleToggle(kb: KnowledgeBase) {
  try {
    const res = await fetch(`/api/knowledge-bases/${kb.id}/toggle`, { method: 'PATCH' })
    if (!res.ok) throw new Error('操作失败')
    const data = await res.json()
    kb.enabled = data.enabled
    message.success(data.enabled ? '已启用' : '已禁用')
  } catch (e: any) { message.error(e?.message || '操作失败') }
}

async function openDocs(kb: KnowledgeBase) {
  viewingKb.value = kb
  showDocs.value = true
  await kbStore.loadDocuments(kb.id)
}

function openUpload(kb: KnowledgeBase) {
  uploadKbId.value = kb.id
  showUpload.value = true
}

async function openReportIngest(kb: KnowledgeBase) {
  reportIngestKbId.value = kb.id
  selectedReportId.value = null
  try {
    const reports = await fetchReports()
    // Only show completed reports not yet indexed to this KB
    availableReports.value = reports.filter(r => r.status === 'ready')
  } catch (e: any) {
    message.error('加载报告列表失败')
    return
  }
  showReportIngest.value = true
}

async function handleReportIngest() {
  if (!selectedReportId.value) {
    message.warning('请选择要入库的报告')
    return
  }
  reportIngestLoading.value = true
  try {
    const result = await indexReportToKb(selectedReportId.value, reportIngestKbId.value)
    message.success(result.message)
    showReportIngest.value = false
    // Refresh doc list
    if (viewingKb.value) await kbStore.loadDocuments(viewingKb.value.id)
    await kbStore.load() // refresh counts
  } catch (e: any) {
    message.error(e?.message || '报告入库失败')
  } finally {
    reportIngestLoading.value = false
  }
}

async function handleFileUpload(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return

  const allowed = ['.txt', '.md', '.pdf', '.docx', '.doc', '.markdown', '.text']
  const ext = '.' + file.name.split('.').pop()?.toLowerCase()
  if (!allowed.includes(ext)) {
    message.warning(`不支持的格式，仅支持：${allowed.join(', ')}`)
    return
  }

  uploading.value = true
  try {
    await kbStore.uploadFile(uploadKbId.value, file)
    message.success(`已上传 ${file.name}，正在异步入库索引...`)
    showUpload.value = false
    if (viewingKb.value) await kbStore.loadDocuments(viewingKb.value.id)
    await kbStore.load()
  } catch (e: any) { message.error(e?.message || '上传失败') }
  finally { uploading.value = false; if (event.target) (event.target as HTMLInputElement).value = '' }
}

async function handleDeleteDoc(kbId: number, docId: number) {
  try { await kbStore.removeDocument(kbId, docId); message.success('已删除') }
  catch (e: any) { message.error(e?.message || '删除失败') }
}

const reportOptions = computed(() =>
  availableReports.value.map(r => ({ label: `${r.title} (${r.created_at})`, value: r.id }))
)

// Need computed import
import { computed } from 'vue'

onMounted(() => kbStore.load())
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-2xl font-bold text-cyan-400">知识库管理</h1>
        <p class="text-slate-400 text-sm mt-1">管理 RAG 知识库 · 支持上传文档和报告入库</p>
      </div>
      <n-button type="primary" @click="showCreate = true">+ 新建知识库</n-button>
    </div>

    <n-spin :show="kbStore.loading">
      <n-empty v-if="kbStore.knowledgeBases.length === 0 && !kbStore.loading" description="暂无知识库" />
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <n-card v-for="kb in kbStore.knowledgeBases" :key="kb.id" hoverable>
          <div class="flex items-center justify-between">
            <h3 class="text-lg font-semibold text-slate-100">{{ kb.name }}</h3>
            <n-space align="center" :size="8">
              <n-tag :type="kb.enabled ? 'success' : 'default'" size="small" :bordered="false">
                {{ kb.enabled ? '已启用' : '已禁用' }}
              </n-tag>
              <n-switch :value="kb.enabled" size="small" @update:value="handleToggle(kb)" />
            </n-space>
          </div>
          <p class="text-slate-400 text-sm mt-2">{{ kb.description }}</p>
          <div class="text-xs text-slate-500 mt-2">
            文档数：{{ kb.document_count }} · 分块数：{{ kb.chunk_count }} · 创建于 {{ kb.created_at }}
          </div>

          <n-space class="mt-3">
            <n-button size="small" ghost @click="openDocs(kb)">查看文档</n-button>
            <n-button size="small" type="primary" ghost @click="openUpload(kb)">上传文件</n-button>
            <n-button size="small" type="info" ghost @click="openReportIngest(kb)">报告入库</n-button>
            <n-popconfirm @positive-click="handleDelete(kb.id)">
              <template #trigger><n-button size="small" type="error" ghost>删除</n-button></template>
              确定删除知识库「{{ kb.name }}」及所有文档？
            </n-popconfirm>
          </n-space>
        </n-card>
      </div>
    </n-spin>

    <!-- Create KB Modal -->
    <n-modal v-model:show="showCreate" title="新建知识库" preset="card" style="max-width:480px">
      <n-form>
        <n-form-item label="名称"><n-input v-model:value="newKbName" placeholder="知识库名称" /></n-form-item>
        <n-form-item label="描述"><n-input v-model:value="newKbDesc" type="textarea" :rows="2" placeholder="简述用途" /></n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showCreate = false">取消</n-button>
          <n-button type="primary" :loading="creating" @click="handleCreate">创建</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- Documents Modal -->
    <n-modal v-model:show="showDocs" :title="viewingKb ? viewingKb.name + ' - 文档列表' : '文档'" preset="card" style="max-width:640px">
      <n-empty v-if="kbStore.currentDocs.length === 0" description="暂无文档" />
      <div class="space-y-2">
        <div v-for="doc in kbStore.currentDocs" :key="doc.id"
          class="flex items-center justify-between py-2 px-3 rounded bg-slate-800/30">
          <div class="flex-1 min-w-0">
            <span class="text-slate-200 text-sm">{{ doc.title }}</span>
            <n-tag v-if="doc.index_status === 'indexed'" size="tiny" type="success" :bordered="false" class="ml-2">已索引</n-tag>
            <n-tag v-else-if="doc.index_status === 'indexing'" size="tiny" type="info" :bordered="false" class="ml-2">索引中</n-tag>
            <n-tag v-else-if="doc.index_status === 'failed'" size="tiny" type="error" :bordered="false" class="ml-2">失败</n-tag>
            <n-tag v-else size="tiny" type="warning" :bordered="false" class="ml-2">待索引</n-tag>
            <span v-if="doc.source_type === 'report'" class="text-xs text-cyan-400 ml-2">报告入库</span>
            <div class="text-xs text-slate-500">
              {{ doc.source }} · {{ doc.file_type }} · {{ doc.created_at }}
              <span v-if="doc.chunk_count > 0"> · {{ doc.chunk_count }} 分块</span>
            </div>
          </div>
          <n-popconfirm @positive-click="handleDeleteDoc(doc.kb_id, doc.id)">
            <template #trigger><n-button size="tiny" type="error" ghost>删除</n-button></template>
            确定删除？
          </n-popconfirm>
        </div>
      </div>
    </n-modal>

    <!-- Upload Modal — V2: 仅文件上传，支持多种格式 -->
    <n-modal v-model:show="showUpload" title="上传文件到知识库" preset="card" style="max-width:520px">
      <div class="mb-4 text-sm text-slate-400">
        支持格式：txt、md、pdf、docx、doc 等文档文件。上传后将自动进行向量索引。
      </div>
      <div class="border-2 border-dashed border-slate-600 rounded-lg p-8 text-center cursor-pointer hover:border-cyan-400 transition-colors"
        @click="fileInputRef?.click()">
        <div class="text-3xl mb-2">📁</div>
        <div class="text-slate-300">点击选择文件</div>
        <div class="text-xs text-slate-500 mt-1">txt / md / pdf / docx / doc</div>
      </div>
      <input ref="fileInputRef" type="file" accept=".txt,.md,.pdf,.docx,.doc,.markdown,.text" class="hidden"
        @change="handleFileUpload" />
      <div v-if="uploading" class="mt-4 text-center text-cyan-400">
        <n-spin size="small" /> 上传中...
      </div>
      <template #footer>
        <n-button @click="showUpload = false">关闭</n-button>
      </template>
    </n-modal>

    <!-- Report Ingest Modal — 选择已有报告入库 -->
    <n-modal v-model:show="showReportIngest" title="选择报告入库" preset="card" style="max-width:520px">
      <div class="mb-4 text-sm text-slate-400">
        将已生成完成的报告入库到当前知识库，报告内容将以 Markdown 格式进行向量索引。
      </div>
      <n-empty v-if="availableReports.length === 0" description="暂无可入库的报告（需要已生成完成的报告）" />
      <n-select v-else v-model:value="selectedReportId" :options="reportOptions" placeholder="选择要入库的报告" />
      <template #footer>
        <n-space justify="end">
          <n-button @click="showReportIngest = false">取消</n-button>
          <n-button type="primary" :loading="reportIngestLoading" :disabled="!selectedReportId" @click="handleReportIngest">
            入库
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>
