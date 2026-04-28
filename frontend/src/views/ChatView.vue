<script setup lang="ts">
import { ref, nextTick, computed, onMounted, watch } from 'vue'
import { NCard, NInput, NButton, NSpin, NTag, NModal, NSpace, NDescriptions, NDescriptionsItem, NPopconfirm, NEmpty, NScrollbar, NDivider } from 'naive-ui'
import { connectSSE } from '../composables/useSSE'
import CitationCard from '../components/CitationCard.vue'
import MarkdownRenderer from '../components/MarkdownRenderer.vue'
import { API_BASE } from '../api/index'
import { fetchChatSessions, fetchChatSessionHistory, deleteChatSession } from '../api/chat'

// ─── Types ───
interface ChatMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: any[]
  agentActivity: string[]
  streaming: boolean
}

interface SessionInfo {
  id: number
  title: string
  mode: string
  created_at: string
  updated_at: string
  message_count: number
}

// ─── Interrupt 审批状态 ───
interface InterruptRequest {
  tool: string
  input: any
  message: string
  threadId: string
  resolved: boolean
}

// ─── State ───
const question = ref('')
const loading = ref(false)
const messages = ref<ChatMsg[]>([])
const chatContainer = ref<HTMLElement | null>(null)
const showSidebar = ref(true)

// ─── Session 持久化 ───
const CHAT_SESSION_KEY = 'chat_session_id'
const CHAT_MESSAGES_KEY = 'chat_messages'
const CHAT_DB_SESSION_KEY = 'chat_db_session_id'
const sessionId = ref<string | null>(localStorage.getItem(CHAT_SESSION_KEY))
const _savedDbId = localStorage.getItem(CHAT_DB_SESSION_KEY)
const dbSessionId = ref<number | null>(_savedDbId ? parseInt(_savedDbId, 10) : null)

// 恢复上次对话记录
const savedMsgs = localStorage.getItem(CHAT_MESSAGES_KEY)
if (savedMsgs) {
  try {
    const parsed = JSON.parse(savedMsgs)
    if (Array.isArray(parsed) && parsed.length > 0) {
      messages.value = parsed
    }
  } catch { /* ignore */ }
}

function saveMessages() {
  try {
    localStorage.setItem(CHAT_MESSAGES_KEY, JSON.stringify(messages.value.slice(-50)))
  } catch { /* quota exceeded, ignore */ }
}

// ─── 会话历史 ───
const sessions = ref<SessionInfo[]>([])
const loadingSessions = ref(false)
const activeDbSessionId = ref<number | null>(null)

async function loadSessions() {
  loadingSessions.value = true
  try {
    sessions.value = await fetchChatSessions()
  } catch (e) {
    console.error('Failed to load sessions:', e)
  }
  loadingSessions.value = false
}

async function switchToSession(dbId: number) {
  if (loading.value) return
  try {
    const history = await fetchChatSessionHistory(dbId)
    messages.value = history.map(m => ({
      id: `hist_${m.id}`,
      role: m.role as 'user' | 'assistant',
      content: m.content,
      citations: m.citations || [],
      agentActivity: [],
      streaming: false,
    }))
    activeDbSessionId.value = dbId
    dbSessionId.value = dbId
    localStorage.setItem(CHAT_DB_SESSION_KEY, String(dbId))
    // 使用 DB session id 作为 thread_id 的一部分，恢复 Checkpointer
    const tid = `db_${dbId}`
    sessionId.value = tid
    localStorage.setItem(CHAT_SESSION_KEY, tid)
    saveMessages()
  } catch (e) {
    console.error('Failed to load session history:', e)
  }
  await scrollToBottom()
}

async function deleteSession(dbId: number) {
  try {
    await deleteChatSession(dbId)
    sessions.value = sessions.value.filter(s => s.id !== dbId)
    if (activeDbSessionId.value === dbId) {
      clearChat()
    }
  } catch (e) {
    console.error('Failed to delete session:', e)
  }
}

function formatTime(t: string) {
  if (!t) return ''
  // "2026-04-28 08:10" → "04/28 08:10"
  return t.replace(/^\d{4}-/, '').replace('-', '/')
}

onMounted(() => {
  loadSessions()
})

// ─── Interrupt 审批 ───
const interruptRequest = ref<InterruptRequest | null>(null)
const interruptApproved = ref<boolean | null>( null)

const toolNameMap: Record<string, string> = {
  save_memory: '💾 保存记忆',
  edit_file: '📝 编辑文件',
}

async function approveInterrupt() {
  if (!interruptRequest.value) return
  interruptApproved.value = true
  try {
    const tid = interruptRequest.value.threadId
    const res = await fetch(`${API_BASE}/api/chat/interrupt/${tid}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approved: true }),
    })
    const data = await res.json().catch(() => ({}))
    if (data.status === 'approved') {
      messages.value.push({
        id: `sys_${Date.now()}`,
        role: 'assistant',
        content: `✅ 已批准「${toolNameMap[interruptRequest.value.tool] || interruptRequest.value.tool}」执行`,
        citations: [], agentActivity: [], streaming: false,
      })
    }
  } catch (e: any) {
    messages.value.push({
      id: `err_${Date.now()}`,
      role: 'assistant',
      content: `❌ 审批请求失败: ${e.message}`,
      citations: [], agentActivity: [], streaming: false,
    })
  }
  interruptRequest.value = null
  interruptApproved.value = null
  await scrollToBottom()
}

async function rejectInterrupt() {
  if (!interruptRequest.value) return
  interruptApproved.value = false
  try {
    const tid = interruptRequest.value.threadId
    const res = await fetch(`${API_BASE}/api/chat/interrupt/${tid}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approved: false }),
    })
    const data = await res.json().catch(() => ({}))
    if (data.status === 'rejected') {
      messages.value.push({
        id: `sys_${Date.now()}`,
        role: 'assistant',
        content: `🚫 已拒绝「${toolNameMap[interruptRequest.value.tool] || interruptRequest.value.tool}」执行`,
        citations: [], agentActivity: [], streaming: false,
      })
    }
  } catch (e: any) {
    messages.value.push({
      id: `err_${Date.now()}`,
      role: 'assistant',
      content: `❌ 拒绝请求失败: ${e.message}`,
      citations: [], agentActivity: [], streaming: false,
    })
  }
  interruptRequest.value = null
  interruptApproved.value = null
  await scrollToBottom()
}

// ─── Auto-scroll ───
async function scrollToBottom() {
  await nextTick()
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

// ─── Interrupt Modal ───
const showInterruptModal = computed({
  get: () => interruptRequest.value !== null && interruptApproved.value === null,
  set: (val: boolean) => { if (!val) { /* don't close on mask click */ } },
})

// ─── Send message via SSE ───
async function handleAsk() {
  const text = question.value.trim()
  if (!text || loading.value) return

  loading.value = true
  question.value = ''

  // Add user message
  messages.value.push({
    id: `u_${Date.now()}`,
    role: 'user',
    content: text,
    citations: [],
    agentActivity: [],
    streaming: false,
  })

  // Add placeholder assistant message
  const asstId = `a_${Date.now()}`
  const assistantMsg: ChatMsg = {
    id: asstId,
    role: 'assistant',
    content: '',
    citations: [],
    agentActivity: [],
    streaming: true,
  }
  messages.value.push(assistantMsg)
  await scrollToBottom()

  // Connect SSE
  await connectSSE(
    `${API_BASE}/api/chat/stream`,
    { message: text, session_id: sessionId.value, db_session_id: dbSessionId.value },
    {
      onToken: (token) => {
        assistantMsg.content += token
        const idx = messages.value.findIndex(m => m.id === asstId)
        if (idx !== -1) messages.value.splice(idx, 1, { ...assistantMsg })
        scrollToBottom()
      },
      onAgentStart: (agent, task) => {
        const agentNames: Record<string, string> = {
          searcher: '🔍 联网搜索',
          retriever: '📚 知识库检索',
        }
        const label = agentNames[agent] || agent
        assistantMsg.agentActivity.push(`${label}: ${task}`)
        const idx = messages.value.findIndex(m => m.id === asstId)
        if (idx !== -1) messages.value.splice(idx, 1, { ...assistantMsg })
      },
      onToolStart: (tool, _agent) => {
        const toolNames: Record<string, string> = {
          search_web: '搜索',
          fetch_page: '抓取页面',
          get_search_content: '阅读页面',
          search_knowledge: '知识库检索',
          get_recall_nodes: '阅读节点',
          list_active_kbs: '查询知识库',
          get_current_time: '获取时间',
          recall_memory: '召回记忆',
          save_memory: '保存记忆',
          recall_early_messages: '召回早期对话',
        }
        const label = toolNames[tool] || tool
        assistantMsg.agentActivity.push(`  → ${label}`)
        const idx = messages.value.findIndex(m => m.id === asstId)
        if (idx !== -1) messages.value.splice(idx, 1, { ...assistantMsg })
      },
      onToolEnd: (_tool, _result, _agent) => {
        // Silent
      },
      onDone: (data) => {
        assistantMsg.streaming = false
        if (data.session_id) {
          sessionId.value = data.session_id
          localStorage.setItem(CHAT_SESSION_KEY, data.session_id)
        }
        if (data.db_session_id) {
          dbSessionId.value = data.db_session_id
          localStorage.setItem(CHAT_DB_SESSION_KEY, String(data.db_session_id))
        }
        const idx = messages.value.findIndex(m => m.id === asstId)
        if (idx !== -1) messages.value.splice(idx, 1, { ...assistantMsg })
        saveMessages()
        // 刷新会话列表
        loadSessions()
      },
      onError: (msg) => {
        assistantMsg.content += `\n\n❌ 错误: ${msg}`
        assistantMsg.streaming = false
        const idx = messages.value.findIndex(m => m.id === asstId)
        if (idx !== -1) messages.value.splice(idx, 1, { ...assistantMsg })
      },
      onInterrupt: (tool, input, message) => {
        interruptRequest.value = {
          tool,
          input,
          message,
          threadId: sessionId.value || '',
          resolved: false,
        }
        assistantMsg.agentActivity.push(`⏸️ 等待审批: ${toolNameMap[tool] || tool}`)
        const idx = messages.value.findIndex(m => m.id === asstId)
        if (idx !== -1) messages.value.splice(idx, 1, { ...assistantMsg })
      },
    },
  )

  loading.value = false
  await scrollToBottom()
}

// ─── Suggestions ───
const suggestions = [
  '最近AI领域有什么新进展？',
  '我的知识库里有哪些内容？',
  '帮我查查2026年最新手机参数对比',
  '什么是RAG技术？',
]

function useSuggestion(q: string) {
  question.value = q
  handleAsk()
}

function clearChat() {
  messages.value = []
  sessionId.value = null
  dbSessionId.value = null
  activeDbSessionId.value = null
  localStorage.removeItem(CHAT_SESSION_KEY)
  localStorage.removeItem(CHAT_MESSAGES_KEY)
  localStorage.removeItem(CHAT_DB_SESSION_KEY)
}
</script>

<template>
  <div class="flex h-[calc(100vh-80px)] gap-3">
    <!-- 左侧：会话历史 -->
    <div v-if="showSidebar" class="w-56 shrink-0 flex flex-col">
      <n-card size="small" class="flex-1 overflow-hidden flex flex-col p-0">
        <template #header>
          <div class="flex items-center justify-between">
            <span class="text-sm font-medium text-slate-300">💬 历史会话</span>
            <n-button size="tiny" quaternary @click="loadSessions" :loading="loadingSessions">↻</n-button>
          </div>
        </template>
        <n-scrollbar class="flex-1" style="max-height: calc(100vh - 180px)">
          <div v-if="sessions.length === 0" class="p-3">
            <n-empty size="small" description="暂无会话" />
          </div>
          <div v-else class="p-1 space-y-0.5">
            <div
              v-for="s in sessions" :key="s.id"
              @click="switchToSession(s.id)"
              class="group px-2 py-1.5 rounded cursor-pointer transition-colors"
              :class="activeDbSessionId === s.id
                ? 'bg-cyan-800/30 border border-cyan-600/40'
                : 'hover:bg-slate-700/50'"
            >
              <div class="flex items-center justify-between">
                <span class="text-xs text-slate-300 truncate flex-1">{{ s.title }}</span>
                <n-popconfirm @positive-click="deleteSession(s.id)">
                  <template #trigger>
                    <n-button
                      size="tiny" quaternary
                      class="opacity-0 group-hover:opacity-100 transition-opacity text-slate-500 hover:text-red-400"
                      @click.stop
                    >✕</n-button>
                  </template>
                  确定删除此会话？
                </n-popconfirm>
              </div>
              <div class="text-[10px] text-slate-500 mt-0.5">
                {{ formatTime(s.updated_at) }} · {{ s.message_count }}条
              </div>
            </div>
          </div>
        </n-scrollbar>
      </n-card>
    </div>

    <!-- 右侧：对话主区域 -->
    <div class="flex-1 flex flex-col min-w-0">
      <!-- Header -->
      <div class="mb-3 flex items-center justify-between shrink-0">
        <div class="flex items-center gap-2">
          <n-button size="tiny" quaternary @click="showSidebar = !showSidebar">
            {{ showSidebar ? '◀' : '▶' }}
          </n-button>
          <div>
            <h1 class="text-2xl font-bold text-cyan-400">💬 智能对话</h1>
            <p class="text-slate-400 text-xs mt-0.5">自动判断搜索或知识库 · 混合智能</p>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <n-tag v-if="sessionId" size="tiny" :bordered="false" type="info">
            {{ sessionId.slice(-6) }}
          </n-tag>
          <n-button size="tiny" quaternary @click="clearChat">新对话</n-button>
        </div>
      </div>

      <!-- Chat messages -->
      <div ref="chatContainer" class="flex-1 overflow-y-auto mb-3 space-y-3 pr-1">
        <!-- Empty state -->
        <template v-if="messages.length === 0">
          <n-card size="small" class="mt-4">
            <div class="text-center">
              <p class="text-slate-300 text-sm mb-3">💡 试试这样问</p>
              <div class="flex flex-wrap justify-center gap-2">
                <n-tag
                  v-for="q in suggestions" :key="q"
                  style="cursor:pointer" :bordered="false" type="info"
                  @click="useSuggestion(q)"
                >{{ q }}</n-tag>
              </div>
            </div>
          </n-card>
        </template>

        <!-- Messages -->
        <div v-for="msg in messages" :key="msg.id"
          :class="msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'">
          <div :class="msg.role === 'user'
            ? 'bg-cyan-900/40 border border-cyan-700/30 rounded-lg px-4 py-2 max-w-[75%]'
            : 'bg-slate-800/50 border border-slate-700/30 rounded-lg px-4 py-3 max-w-[85%]'">
            <template v-if="msg.role === 'user'">
              <p class="text-cyan-200 text-sm">{{ msg.content }}</p>
            </template>
            <template v-else>
              <div v-if="msg.agentActivity.length > 0" class="mb-2 text-[10px] text-slate-500 space-y-0.5">
                <div v-for="(act, ai) in msg.agentActivity" :key="ai">{{ act }}</div>
              </div>
              <MarkdownRenderer v-if="msg.content" :content="msg.content" />
              <div v-if="msg.streaming && !msg.content" class="flex items-center gap-2">
                <n-spin size="small" />
                <span class="text-slate-400 text-sm">思考中...</span>
              </div>
              <CitationCard v-if="msg.citations.length > 0" :citations="msg.citations" />
            </template>
          </div>
        </div>
      </div>

      <!-- Interrupt 审批对话框 -->
      <n-modal v-model:show="showInterruptModal" preset="card" title="🔒 操作审批"
        style="max-width: 480px" :mask-closable="false" :close-on-esc="false">
        <template v-if="interruptRequest">
          <p class="text-slate-300 mb-3">{{ interruptRequest.message }}</p>
          <n-descriptions label-placement="left" bordered :column="1" size="small">
            <n-descriptions-item label="操作">
              <n-tag type="warning" size="small">{{ toolNameMap[interruptRequest.tool] || interruptRequest.tool }}</n-tag>
            </n-descriptions-item>
            <n-descriptions-item label="参数">
              <span class="text-slate-300 text-xs font-mono">{{ JSON.stringify(interruptRequest.input, null, 2) }}</span>
            </n-descriptions-item>
          </n-descriptions>
        </template>
        <template #footer>
          <n-space justify="end">
            <n-button @click="rejectInterrupt" :disabled="interruptApproved !== null">🚫 拒绝</n-button>
            <n-button type="primary" @click="approveInterrupt" :disabled="interruptApproved !== null">✅ 同意</n-button>
          </n-space>
        </template>
      </n-modal>

      <!-- Input area -->
      <n-card class="shrink-0" size="small">
        <n-input v-model:value="question" type="textarea" :rows="2"
          placeholder="输入问题，系统自动判断使用知识库或联网搜索..."
          :disabled="loading"
          @keydown.enter.ctrl="handleAsk" />
        <div class="mt-2 flex items-center justify-between">
          <span class="text-xs text-slate-500">Ctrl+Enter 发送</span>
          <n-button type="primary" :loading="loading" @click="handleAsk">
            {{ loading ? '思考中...' : '🤖 提问' }}
          </n-button>
        </div>
      </n-card>
    </div>
  </div>
</template>

<style scoped>
.overflow-y-auto::-webkit-scrollbar { width: 4px; }
.overflow-y-auto::-webkit-scrollbar-track { background: transparent; }
.overflow-y-auto::-webkit-scrollbar-thumb { background: rgba(100,116,139,0.3); border-radius: 4px; }
</style>
