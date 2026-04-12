<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { NCard, NButton, NSpace, NTag, NSpin, NEmpty, NModal, NInput, NFormItem, NPopconfirm, useMessage } from 'naive-ui'
import { useKbStore } from '../stores'
import type { KnowledgeBase, KbDocument } from '../types'

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
const uploadTitle = ref('')
const uploadContent = ref('')
const uploading = ref(false)

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

async function openDocs(kb: KnowledgeBase) {
  viewingKb.value = kb
  showDocs.value = true
  await kbStore.loadDocuments(kb.id)
}

async function openUpload(kb: KnowledgeBase) {
  uploadKbId.value = kb.id
  uploadTitle.value = ''
  uploadContent.value = ''
  showUpload.value = true
}

async function handleUpload() {
  if (!uploadTitle.value.trim() || !uploadContent.value.trim()) {
    message.warning('请输入标题和内容'); return
  }
  uploading.value = true
  try {
    await kbStore.uploadDocument(uploadKbId.value, uploadTitle.value, uploadContent.value)
    message.success('文档已上传并索引')
    showUpload.value = false
  } catch (e: any) { message.error(e?.message || '上传失败') }
  finally { uploading.value = false }
}

async function handleFileUpload(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  try {
    await kbStore.uploadFile(uploadKbId.value, file)
    message.success(`已上传 ${file.name}`)
    showUpload.value = false
  } catch (e: any) { message.error(e?.message || '上传失败') }
}

async function handleDeleteDoc(kbId: number, docId: number) {
  try { await kbStore.removeDocument(kbId, docId); message.success('已删除') }
  catch (e: any) { message.error(e?.message || '删除失败') }
}

async function handleUnbookmarkDoc(articleId: number) {
  try {
    const { toggleBookmark } = await import('../api')
    await toggleBookmark(articleId, false)
    message.success('已取消收藏（同步从向量库移除）')
    // Refresh docs list
    if (viewingKb.value) await kbStore.loadDocuments(viewingKb.value.id)
  } catch (e: any) { message.error(e?.message || '操作失败') }
}

onMounted(() => kbStore.load())
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-2xl font-bold text-cyan-400">知识库管理</h1>
        <p class="text-slate-400 text-sm mt-1">管理 RAG 知识库 · 收藏资讯自动入库 · 支持手动上传文档</p>
      </div>
      <n-button type="primary" @click="showCreate = true">+ 新建知识库</n-button>
    </div>

    <n-spin :show="kbStore.loading">
      <n-empty v-if="kbStore.knowledgeBases.length === 0 && !kbStore.loading" description="暂无知识库" />
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <n-card v-for="kb in kbStore.knowledgeBases" :key="kb.id" hoverable>
          <div class="flex items-center justify-between">
            <h3 class="text-lg font-semibold text-slate-100">{{ kb.name }}</h3>
            <n-tag :type="kb.kb_type === 'bookmarks' ? 'success' : 'info'" size="small" :bordered="false">
              {{ kb.kb_type === 'bookmarks' ? '收藏资讯' : '用户上传' }}
            </n-tag>
          </div>
          <p class="text-slate-400 text-sm mt-2">{{ kb.description }}</p>
          <div class="text-xs text-slate-500 mt-2">文档数：{{ kb.article_count }} · 创建于 {{ kb.created_at }}</div>

          <n-space class="mt-3">
            <n-button size="small" ghost @click="openDocs(kb)">查看文档</n-button>
            <n-button v-if="kb.kb_type === 'upload'" size="small" type="primary" ghost @click="openUpload(kb)">上传文档</n-button>
            <n-popconfirm v-if="!kb.is_default" @positive-click="handleDelete(kb.id)">
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
            <n-tag v-if="doc.indexed" size="tiny" type="success" :bordered="false" class="ml-2">已索引</n-tag>
            <n-tag v-else size="tiny" type="warning" :bordered="false" class="ml-2">未索引</n-tag>
            <div class="text-xs text-slate-500">{{ doc.source }} · {{ doc.created_at }}</div>
          </div>
          <!-- For bookmark-type KB: show un-bookmark button -->
          <n-button v-if="viewingKb && viewingKb.kb_type === 'bookmarks'" size="tiny" type="warning" ghost
            @click="handleUnbookmarkDoc(doc.id)">取消收藏</n-button>
          <!-- For upload-type KB: show delete button -->
          <n-popconfirm v-if="viewingKb && viewingKb.kb_type === 'upload'" @positive-click="handleDeleteDoc(doc.kb_id, doc.id)">
            <template #trigger><n-button size="tiny" type="error" ghost>删除</n-button></template>
            确定删除？
          </n-popconfirm>
        </div>
      </div>
    </n-modal>

    <!-- Upload Modal -->
    <n-modal v-model:show="showUpload" title="上传文档" preset="card" style="max-width:520px">
      <n-form>
        <n-form-item label="文档标题"><n-input v-model:value="uploadTitle" placeholder="标题" /></n-form-item>
        <n-form-item label="粘贴内容"><n-input v-model:value="uploadContent" type="textarea" :rows="5" placeholder="粘贴文本内容..." /></n-form-item>
        <n-form-item label="或上传文件（txt/md）">
          <input type="file" accept=".txt,.md,.text,.markdown" @change="handleFileUpload" class="text-sm text-slate-400" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showUpload = false">取消</n-button>
          <n-button type="primary" :loading="uploading" :disabled="!uploadTitle.trim() || !uploadContent.trim()" @click="handleUpload">上传并索引</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>
