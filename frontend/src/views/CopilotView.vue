<script setup lang="ts">
import { ref, onMounted, nextTick, watch as vueWatch } from 'vue'
import {
  NCard, NInput, NButton, NSpin, NTag,
} from 'naive-ui'

// ─── Types ───────────────────────────────────────────────────────────────

interface CopilotMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  toolLog?: ToolLogEntry[]
  timestamp: string
  streaming?: boolean
}

interface ToolLogEntry {
  tool: string
  status: 'executing' | 'done'
  result_preview?: string
}

// ─── State ───────────────────────────────────────────────────────────────

const inputText = ref('')
const messages = ref<CopilotMessage[]>([])
const loading = ref(false)
const threadId = ref<string | null>(null)
const showToolLog = ref(false)
const chatContainer = ref<HTMLElement | null>(null)
const errorMsg = ref('')
const useSupervisor = ref(false)  // 多 Agent 模式开关

// ─── Persistence ─────────────────────────────────────────────────────────

const STORAGE_KEY = 'copilot_state'

function saveState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      threadId: threadId.value,
      messages: messages.value.map(m => ({ ...m, streaming: false })),
    }))
  } catch { /* quota */ }
}

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const state = JSON.parse(raw)
      if (state.threadId) threadId.value = state.threadId
      if (state.messages?.length) messages.value = state.messages.map((m: CopilotMessage) => ({ ...m, streaming: false }))
    }
  } catch { /* parse */ }
}

vueWatch(messages, saveState, { deep: true })
vueWatch(threadId, saveState)
onMounted(loadState)

// ─── SSE Streaming ────────────────────────────────────────────────────────

async function sendMessage() {
  const text = inputText.value.trim()
  if (!text || loading.value) return

  loading.value = true
  inputText.value = ''
  errorMsg.value = ''

  // Add user message
  messages.value.push({
    id: `u_${Date.now()}`,
    role: 'user',
    content: text,
    timestamp: new Date().toLocaleTimeString(),
  })

  // Add placeholder assistant message
  const asstId = `a_${Date.now()}`
  const assistantMsg: CopilotMessage = {
    id: asstId,
    role: 'assistant',
    content: '',
    toolLog: [],
    timestamp: new Date().toLocaleTimeString(),
    streaming: true,
  }
  messages.value.push(assistantMsg)
  await scrollToBottom()

  const TIMEOUT_MS = 120_000  // 2 minute timeout
  let timedOut = false

  // Set a safety timeout
  const timeoutHandle = setTimeout(() => {
    if (assistantMsg.streaming) {
      timedOut = true
      assistantMsg.streaming = false
      assistantMsg.content += '\n\n⚠️ 响应超时，请重试。'
      loading.value = false
    }
  }, TIMEOUT_MS)

  try {
    const res = await fetch('/api/copilot/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, thread_id: threadId.value, use_supervisor: useSupervisor.value }),
    })

    if (!res.ok) {
      clearTimeout(timeoutHandle)
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
      assistantMsg.content = `❌ 请求失败: ${err.detail || res.status}`
      assistantMsg.streaming = false
      loading.value = false
      return
    }

    const reader = res.body?.getReader()
    if (!reader) {
      clearTimeout(timeoutHandle)
      assistantMsg.content = '❌ 无法建立流式连接'
      assistantMsg.streaming = false
      loading.value = false
      return
    }

    const decoder = new TextDecoder()
    let buffer = ''

    while (!timedOut) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // Process complete SSE messages (separated by double newlines)
      // Use a more robust parser that handles multi-line data fields
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''  // Keep incomplete part

      for (const part of parts) {
        if (!part.trim()) continue

        let eventType = ''
        let dataLines: string[] = []

        for (const line of part.split('\n')) {
          if (line.startsWith('event:')) {
            eventType = line.slice(6).trim()
          } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trimStart())  // keep one leading space if present
          } else if (line.startsWith(' ') || line.startsWith('\t')) {
            // SSE continuation line — append to previous data
            if (dataLines.length > 0) {
              dataLines[dataLines.length - 1] += '\n' + line.slice(1)
            }
          }
        }

        // Combine data lines (SSE spec: multiple data: lines joined by \n)
        const dataStr = dataLines.join('\n')
        if (!dataStr) continue

        try {
          const data = JSON.parse(dataStr)
          handleSSEEvent(data, assistantMsg)
        } catch (e) {
          console.warn('SSE parse error:', dataStr.substring(0, 100), e)
        }
      }
    }

    assistantMsg.streaming = false
    
    // If no content was received at all, show a message
    if (!assistantMsg.content.trim()) {
      assistantMsg.content = '⚠️ 未收到回复，请重试。'
    }
  } catch (e: any) {
    assistantMsg.content = `❌ 连接错误: ${e.message}`
    assistantMsg.streaming = false
  }

  clearTimeout(timeoutHandle)
  loading.value = false
  await scrollToBottom()
}

function handleSSEEvent(event: any, assistantMsg: CopilotMessage) {
  const type = event.type

  if (type === 'token') {
    const token = event.content || ''
    if (token) {
      // Update content directly on the message object
      assistantMsg.content += token
      // Force Vue reactivity: splice the array to trigger re-render
      const idx = messages.value.findIndex(m => m.id === assistantMsg.id)
      if (idx !== -1) {
        // Trigger reactivity by replacing the reference
        messages.value.splice(idx, 1, { ...assistantMsg })
      }
      scrollToBottom()
    }
  } else if (type === 'tool_start') {
    assistantMsg.toolLog = assistantMsg.toolLog || []
    assistantMsg.toolLog.push({
      tool: event.tool || '?',
      status: 'executing',
    })
    const idx = messages.value.findIndex(m => m.id === assistantMsg.id)
    if (idx !== -1) messages.value.splice(idx, 1, { ...assistantMsg })
  } else if (type === 'tool_end') {
    if (assistantMsg.toolLog && assistantMsg.toolLog.length > 0) {
      const last = assistantMsg.toolLog[assistantMsg.toolLog.length - 1]
      last.status = 'done'
      last.result_preview = (event.result || '').replace(/\n/g, ' ').slice(0, 200)
    }
    const idx = messages.value.findIndex(m => m.id === assistantMsg.id)
    if (idx !== -1) messages.value.splice(idx, 1, { ...assistantMsg })
  } else if (type === 'worker_switch') {
    // Show which worker is active
    const workerNames: Record<string, string> = {
      supervisor: '🧠 调度中心',
      researcher: '🔍 采集助手',
      analyst: '📊 分析助手',
      manager: '📋 管理助手',
    }
    const workerLabel = workerNames[event.worker] || event.worker
    assistantMsg.content += `\n⟳ ${workerLabel} 开始工作\n`
    const idx = messages.value.findIndex(m => m.id === assistantMsg.id)
    if (idx !== -1) messages.value.splice(idx, 1, { ...assistantMsg })
  } else if (type === 'done') {
    assistantMsg.streaming = false
    if (event.thread_id) {
      threadId.value = event.thread_id
    }
    const idx = messages.value.findIndex(m => m.id === assistantMsg.id)
    if (idx !== -1) messages.value.splice(idx, 1, { ...assistantMsg })
  } else if (type === 'error') {
    assistantMsg.content += `\n\n❌ 错误: ${event.message || '未知错误'}`
    assistantMsg.streaming = false
    const idx = messages.value.findIndex(m => m.id === assistantMsg.id)
    if (idx !== -1) messages.value.splice(idx, 1, { ...assistantMsg })
  }
}

// ─── Helpers ─────────────────────────────────────────────────────────────

async function scrollToBottom() {
  await nextTick()
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

function clearChat() {
  messages.value = []
  threadId.value = null
  localStorage.removeItem(STORAGE_KEY)
}

const suggestions = [
  '列出所有专题',
  '帮我搜索AI最新进展',
  '知识库状态怎么样',
  '帮我生成一份研究报告',
  '收藏的文章有哪些',
  '系统状态',
]

function useSuggestion(text: string) {
  inputText.value = text
  sendMessage()
}

const toolEmojis: Record<string, string> = {
  search_web: '🔍', ingest_url: '📥', collect_topic: '📦',
  rag_query: '📚', rag_stats: '📊', rag_reindex: '🔄',
  list_topics: '📋', create_topic: '➕', update_topic: '✏️', delete_topic: '🗑️',
  list_articles: '📄', bookmark_article: '⭐', delete_article: '🗑️',
  list_knowledge_bases: '📚', create_knowledge_base: '➕', delete_knowledge_base: '🗑️',
  upload_document: '📤', list_kb_documents: '📄',
  generate_report: '📝', list_reports: '📝',
  get_system_status: '📊', get_scheduler_jobs: '⏰',
  write_context_file: '💾', read_context_file: '📖',
}
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- Header -->
    <div class="mb-4 flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold text-cyan-400">🤖 智能助手</h1>
        <p class="text-slate-400 text-sm mt-1">自然语言驱动 · Agent 自主完成所有操作</p>
      </div>
      <div class="flex items-center gap-2">
        <n-tag v-if="threadId" size="tiny" :bordered="false" type="info">
          {{ threadId.slice(-6) }}
        </n-tag>
        <n-tag
          size="tiny"
          :bordered="false"
          :type="useSupervisor ? 'warning' : 'default'"
          style="cursor:pointer"
          @click="useSupervisor = !useSupervisor"
        >
          {{ useSupervisor ? '🤖 多Agent' : '🤖 单Agent' }}
        </n-tag>
        <n-button size="tiny" quaternary @click="clearChat">清空</n-button>
      </div>
    </div>

    <!-- Chat Area -->
    <div ref="chatContainer" class="flex-1 overflow-y-auto space-y-4 mb-4 pr-1"
      style="max-height: calc(100vh - 280px)">

      <!-- Empty state -->
      <div v-if="messages.length === 0" class="flex flex-col items-center justify-center py-12">
        <div class="text-5xl mb-4">🤖</div>
        <p class="text-slate-400 mb-6">告诉我你想做什么</p>
        <div class="grid grid-cols-2 gap-2 max-w-lg">
          <n-tag v-for="s in suggestions" :key="s"
            style="cursor:pointer" :bordered="false" type="info"
            class="hover:bg-cyan-900/30 transition-colors"
            @click="useSuggestion(s)"
          >{{ s }}</n-tag>
        </div>
      </div>

      <!-- Messages -->
      <template v-else>
        <div v-for="msg in messages" :key="msg.id">
          <!-- User -->
          <div v-if="msg.role === 'user'" class="flex justify-end mb-2">
            <div class="bg-cyan-900/40 border border-cyan-700/30 rounded-lg px-4 py-2 max-w-[80%]">
              <p class="text-cyan-200 text-sm">{{ msg.content }}</p>
            </div>
          </div>

          <!-- Assistant -->
          <div v-else class="flex justify-start mb-2">
            <div class="bg-slate-800/50 border border-slate-700/30 rounded-lg px-4 py-3 max-w-[90%]">
              <!-- Thinking -->
              <div v-if="msg.streaming && !msg.content && (!msg.toolLog || msg.toolLog.length === 0)"
                class="flex items-center gap-2 text-cyan-400">
                <n-spin size="small" />
                <span class="text-sm">思考中...</span>
              </div>

              <!-- Tool executing (show even when no content yet) -->
              <div v-if="msg.toolLog && msg.toolLog.length > 0" class="mb-2">
                <div v-for="(tl, ti) in msg.toolLog" :key="ti"
                  class="text-xs flex items-center gap-1.5 py-0.5">
                  <span>{{ toolEmojis[tl.tool] || '🔧' }}</span>
                  <span class="text-slate-400">{{ tl.tool.replace(/_/g, ' ') }}</span>
                  <span v-if="tl.status === 'done'" class="text-green-400">✓</span>
                  <n-spin v-else size="tiny" />
                </div>
              </div>

              <!-- Content -->
              <div v-if="msg.content" class="text-slate-300 text-sm leading-relaxed whitespace-pre-line">
                {{ msg.content }}
                <span v-if="msg.streaming" class="inline-block w-1.5 h-4 bg-cyan-400 animate-pulse ml-0.5" />
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- Error -->
    <div v-if="errorMsg" class="text-red-400 text-xs mb-2 px-2">{{ errorMsg }}</div>

    <!-- Input -->
    <n-card size="small" class="shrink-0">
      <div class="flex items-end gap-2">
        <n-input
          v-model:value="inputText"
          type="textarea"
          :rows="2"
          placeholder="告诉我你想做什么... (Ctrl+Enter 发送)"
          @keydown.enter.ctrl="sendMessage"
          :disabled="loading"
        />
        <n-button
          type="primary"
          :loading="loading"
          :disabled="!inputText.trim()"
          @click="sendMessage"
        >
          {{ loading ? '执行中' : '发送' }}
        </n-button>
      </div>
      <div class="mt-2 flex gap-1 flex-wrap">
        <n-tag v-for="s in suggestions.slice(0, 4)" :key="s"
          size="tiny" :bordered="false" style="cursor:pointer"
          @click="useSuggestion(s)"
        >{{ s }}</n-tag>
      </div>
    </n-card>
  </div>
</template>

<style scoped>
.animate-pulse {
  animation: pulse 1s cubic-bezier(0.4, 0, 0.6, 1) infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
.overflow-y-auto::-webkit-scrollbar { width: 4px; }
.overflow-y-auto::-webkit-scrollbar-track { background: transparent; }
.overflow-y-auto::-webkit-scrollbar-thumb { background: rgba(100,116,139,0.3); border-radius: 4px; }
</style>
