<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { NCard, NInput, NButton, NSpin, NDivider, NSpace, NTag, NRadioGroup, NRadio, NPopover, NEmpty, useMessage } from 'naive-ui'
import { chatWithSearch, fetchKnowledgeBases, createChatSession, fetchChatSessions, fetchChatSessionHistory, deleteChatSession } from '../api'
import type { ChatResponse, KnowledgeBase, ChatSession, ChatMessage } from '../types'

const message = useMessage()
const question = ref('')
const loading = ref(false)
const result = ref<ChatResponse | null>(null)

// Chat session persistence
const sessionId = ref<number | null>(null)
const chatHistory = ref<Array<{role: string, content: string, mode?: string, search_used?: boolean, citations?: any[]}>>([])
const sessions = ref<ChatSession[]>([])

// Three modes
const chatMode = ref('hybrid')
const selectedKbId = ref<number | null>(null)
const knowledgeBases = ref<KnowledgeBase[]>([])

const modeOptions = [
  { label: '混合智能', value: 'hybrid' },
  { label: '联网搜索', value: 'search' },
  { label: '知识库问答', value: 'knowledge' },
]

const showKbSelector = computed(() => chatMode.value === 'knowledge')

// Auto-scroll target
const chatContainer = ref<HTMLElement | null>(null)

async function loadKnowledgeBases() {
  try { knowledgeBases.value = await fetchKnowledgeBases() } catch { /* ignore */ }
}

async function loadSessions() {
  try {
    sessions.value = await fetchChatSessions()
  } catch { /* ignore */ }
}

// Initialize new chat session
async function initSession(mode?: string) {
  try {
    const m = mode || chatMode.value
    const s = await createChatSession('新对话', m)
    sessionId.value = s.id
    chatHistory.value = []
    result.value = null
    chatMode.value = m
    await loadSessions()
  } catch {
    // Session persistence is optional; chat still works without it
  }
}

// Load session history when resuming a session
async function loadSessionHistory(sid: number) {
  try {
    const msgs: ChatMessage[] = await fetchChatSessionHistory(sid)
    chatHistory.value = msgs.map(m => ({
      role: m.role,
      content: m.content,
      mode: m.mode,
      search_used: m.search_used,
      citations: m.citations || [],
    }))
    // Scroll to bottom
    await nextTick()
    scrollToBottom()
  } catch { /* ignore */ }
}

// Switch to an existing session
async function switchSession(sid: number) {
  sessionId.value = sid
  result.value = null
  await loadSessionHistory(sid)
  // Update mode from session
  const s = sessions.value.find(s => s.id === sid)
  if (s) chatMode.value = s.mode
}

// Delete a session
async function removeSession(sid: number) {
  try {
    await deleteChatSession(sid)
    if (sessionId.value === sid) {
      await initSession()
    }
    await loadSessions()
  } catch {
    message.error('删除会话失败')
  }
}

function scrollToBottom() {
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

onMounted(async () => {
  await loadKnowledgeBases()
  await loadSessions()
  // Resume last session or create new
  if (sessions.value.length > 0) {
    const lastSession = sessions.value[0] // sorted by updated_at desc
    await switchSession(lastSession.id)
  } else {
    await initSession()
  }
})

// Watch mode change — just update mode, don't create new session
watch(chatMode, () => {
  // Mode can be switched within the same session
})

const suggestedQuestions: Record<string, string[]> = {
  hybrid: ['最近驱逐舰有什么最新动态？', '帮我查查无人机领域的最新进展', '当前海军装备发展趋势是什么？'],
  search: ['今天有什么国际新闻？', '最新AI技术突破有哪些？', '最近全球军事演习动态？'],
  knowledge: ['已收藏的驱逐舰资讯要点总结', '无人艇测试的关键技术指标', '舰船综合电力推进的技术路线'],
}

async function handleAsk() {
  if (!question.value.trim()) { message.warning('请输入问题'); return }
  loading.value = true
  result.value = null
  try {
    result.value = await chatWithSearch(question.value, chatMode.value, selectedKbId.value, false, sessionId.value)
    // Add to local chat history
    chatHistory.value.push({ role: 'user', content: question.value })
    chatHistory.value.push({
      role: 'assistant',
      content: result.value.answer,
      mode: result.value.mode,
      search_used: result.value.search_used,
      citations: result.value.citations,
    })
    question.value = ''
    await nextTick()
    scrollToBottom()
    // Refresh session list (title may have updated)
    await loadSessions()
  } catch (e: any) { message.error(e?.message || '请求失败') }
  finally { loading.value = false }
}

function useSuggestion(q: string) {
  question.value = q
  handleAsk()
}

// Format relevance score for display
function formatScore(score: number | null | undefined): string {
  if (score == null) return ''
  return (score * 100).toFixed(1) + '%'
}

function formatTime(t: string): string {
  if (!t) return ''
  // Show only HH:mm if today, otherwise MM-DD HH:mm
  const d = new Date(t.replace(' ', 'T'))
  if (isNaN(d.getTime())) return t
  const now = new Date()
  const isToday = d.toDateString() === now.toDateString()
  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (isToday) return hhmm
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${hhmm}`
}
</script>

<template>
  <div class="flex gap-4 h-[calc(100vh-80px)]">
    <!-- Sidebar: Session list -->
    <div class="w-52 shrink-0 flex flex-col gap-2">
      <n-button block type="primary" ghost size="small" @click="initSession()">
        ✨ 新对话
      </n-button>
      <div class="flex-1 overflow-y-auto space-y-1 pr-0 sidebar-scroll">
        <div v-for="s in sessions" :key="s.id"
          class="group relative rounded-md px-2.5 py-1.5 cursor-pointer transition-colors"
          :class="s.id === sessionId ? 'bg-cyan-900/40 border border-cyan-600/30' : 'bg-slate-800/30 hover:bg-slate-700/30'"
          @click="switchSession(s.id)">
          <div class="text-xs text-slate-200 truncate" :title="s.title">{{ s.title }}</div>
          <div class="text-[10px] text-slate-500 mt-0.5 flex items-center gap-1">
            <span v-if="s.mode === 'search'" class="text-amber-400">联网</span>
            <span v-else-if="s.mode === 'knowledge'" class="text-blue-400">知识库</span>
            <span v-else class="text-emerald-400">混合</span>
            <span>{{ formatTime(s.updated_at || s.created_at) }}</span>
          </div>
          <button class="absolute top-0.5 right-1 opacity-0 group-hover:opacity-100 transition-opacity text-slate-600 hover:text-red-400 text-[10px]"
            @click.stop="removeSession(s.id)" title="删除">✕</button>
        </div>
        <div v-if="sessions.length === 0" class="text-center text-xs text-slate-600 mt-6">暂无对话</div>
      </div>
    </div>

    <!-- Main chat area -->
    <div class="flex-1 flex flex-col min-w-0">
      <!-- Top bar: mode selector -->
      <n-card class="mb-3 shrink-0" size="small">
        <div class="flex items-center justify-between flex-wrap gap-3">
          <div class="flex items-center gap-3">
            <span class="text-sm text-slate-400">对话模式：</span>
            <n-radio-group v-model:value="chatMode" size="small">
              <n-radio v-for="opt in modeOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</n-radio>
            </n-radio-group>
          </div>
          <div v-if="showKbSelector" class="flex items-center gap-2">
            <span class="text-xs text-slate-500">指定知识库：</span>
            <n-tag
              v-for="kb in knowledgeBases" :key="kb.id"
              :type="selectedKbId === kb.id ? 'info' : 'default'"
              size="small" style="cursor:pointer"
              @click="selectedKbId = selectedKbId === kb.id ? null : kb.id"
            >{{ kb.name }}</n-tag>
            <n-tag v-if="!selectedKbId" type="info" size="small" :bordered="false">全部</n-tag>
          </div>
          <div class="text-xs text-slate-500">
            <template v-if="chatMode === 'hybrid'">🔄 先查知识库，不足时自动联网</template>
            <template v-else-if="chatMode === 'search'">🌐 直接搜索互联网</template>
            <template v-else>📚 仅基于已收藏/上传的文档</template>
          </div>
        </div>
      </n-card>

      <!-- Chat messages -->
      <div ref="chatContainer" class="flex-1 overflow-y-auto mb-3 space-y-3 pr-1">
        <!-- Empty state with suggestions -->
        <template v-if="chatHistory.length === 0">
          <n-card size="small" title="💡 试试这样问" class="mt-2">
            <n-space>
              <n-tag v-for="q in (suggestedQuestions[chatMode] || suggestedQuestions.hybrid)" :key="q"
                style="cursor:pointer" :bordered="false" type="info" @click="useSuggestion(q)">{{ q }}</n-tag>
            </n-space>
          </n-card>
        </template>

        <!-- Message bubbles -->
        <div v-for="(msg, idx) in chatHistory" :key="idx"
          :class="msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'">
          <div :class="msg.role === 'user'
            ? 'bg-cyan-900/40 border border-cyan-700/30 rounded-lg px-4 py-2 max-w-[75%]'
            : 'bg-slate-800/50 border border-slate-700/30 rounded-lg px-4 py-3 max-w-[85%]'">
            <!-- User message -->
            <template v-if="msg.role === 'user'">
              <p class="text-cyan-200 text-sm">{{ msg.content }}</p>
            </template>
            <!-- Assistant message -->
            <template v-else>
              <div class="flex items-center gap-2 mb-2">
                <n-tag v-if="msg.mode === 'search'" type="warning" size="tiny" :bordered="false">联网</n-tag>
                <n-tag v-else-if="msg.mode === 'knowledge'" type="info" size="tiny" :bordered="false">知识库</n-tag>
                <n-tag v-else-if="msg.search_used" type="success" size="tiny" :bordered="false">混合(联网)</n-tag>
                <n-tag v-else type="info" size="tiny" :bordered="false">本地</n-tag>
              </div>
              <p class="text-slate-300 text-sm leading-relaxed whitespace-pre-line">{{ msg.content }}</p>
              <!-- Compact citations -->
              <div v-if="msg.citations && msg.citations.length > 0" class="mt-2 flex flex-wrap gap-1">
                <n-popover v-for="(c, ci) in msg.citations.slice(0, 5)" :key="ci" trigger="hover">
                  <template #trigger>
                    <n-tag size="tiny" :bordered="false" type="info">
                      {{ c.source || c.title || `引用${ci+1}` }}
                    </n-tag>
                  </template>
                  <div class="max-w-xs text-xs">
                    <div class="font-bold mb-1">{{ c.title }}</div>
                    <div v-if="c.snippet" class="text-slate-400">{{ c.snippet?.slice(0, 150) }}</div>
                    <div v-if="c.url" class="mt-1"><a :href="c.url" target="_blank" class="text-cyan-400">原文 ↗</a></div>
                  </div>
                </n-popover>
                <n-tag v-if="msg.citations.length > 5" size="tiny" :bordered="false">+{{ msg.citations.length - 5 }}</n-tag>
              </div>
            </template>
          </div>
        </div>

        <!-- Loading indicator -->
        <div v-if="loading" class="flex justify-start">
          <div class="bg-slate-800/50 border border-slate-700/30 rounded-lg px-4 py-3">
            <n-spin size="small" />
            <span class="text-slate-400 text-sm ml-2">思考中...</span>
          </div>
        </div>
      </div>

      <!-- Input area -->
      <n-card class="shrink-0" size="small">
        <n-input v-model:value="question" type="textarea" :rows="2"
          :placeholder="chatMode === 'search' ? '输入你想搜索的问题...' : chatMode === 'knowledge' ? '基于知识库文档提问...' : '输入问题，系统自动判断是否联网...'"
          :disabled="loading"
          @keydown.enter.ctrl="handleAsk" />
        <div class="mt-2 flex items-center justify-between">
          <span class="text-xs text-slate-500">Ctrl+Enter 发送 · 对话自动保存</span>
          <n-button type="primary" :loading="loading" @click="handleAsk">
            {{ loading ? '思考中...' : chatMode === 'search' ? '🔍 搜索' : chatMode === 'knowledge' ? '📚 查询' : '🤖 提问' }}
          </n-button>
        </div>
      </n-card>

      <!-- Expandable latest result detail -->
      <n-spin :show="loading">
        <n-card v-if="result && result.citations.length > 0" class="mt-3 shrink-0" size="small">
          <template #header>
            <div class="flex items-center gap-3">
              <span class="text-slate-200 font-bold text-sm">📚 引用详情 ({{ result.citations.length }})</span>
              <n-tag v-if="result.mode === 'search'" type="warning" size="tiny" :bordered="false">联网搜索</n-tag>
              <n-tag v-else-if="result.mode === 'knowledge'" type="info" size="tiny" :bordered="false">知识库</n-tag>
              <n-tag v-else-if="result.search_used" type="success" size="tiny" :bordered="false">混合(已联网)</n-tag>
              <n-tag v-else type="info" size="tiny" :bordered="false">混合(本地)</n-tag>
            </div>
          </template>
          <div class="space-y-2 max-h-48 overflow-y-auto">
            <div v-for="(c, idx) in result.citations" :key="idx" class="flex items-start gap-2">
              <n-tag size="tiny" :bordered="false" type="info">{{ idx + 1 }}</n-tag>
              <div class="flex-1 min-w-0">
                <span class="text-slate-100 text-xs font-medium">{{ c.title || '未知标题' }}</span>
                <n-tag v-if="c.relevance_score" size="tiny" :bordered="false" type="success" class="ml-1">{{ formatScore(c.relevance_score) }}</n-tag>
                <div class="text-xs text-slate-500">
                  <span v-if="c.source">{{ c.source }}</span>
                  <span v-if="c.url" class="ml-2"><a :href="c.url" target="_blank" class="text-cyan-400 hover:underline">原文 ↗</a></span>
                </div>
              </div>
            </div>
          </div>
        </n-card>
      </n-spin>
    </div>
  </div>
</template>

<style scoped>
/* Thin scrollbar for sidebar and chat */
.sidebar-scroll::-webkit-scrollbar,
.overflow-y-auto::-webkit-scrollbar {
  width: 4px;
}
.sidebar-scroll::-webkit-scrollbar-track,
.overflow-y-auto::-webkit-scrollbar-track {
  background: transparent;
}
.sidebar-scroll::-webkit-scrollbar-thumb,
.overflow-y-auto::-webkit-scrollbar-thumb {
  background: rgba(100, 116, 139, 0.3);
  border-radius: 4px;
}
.sidebar-scroll::-webkit-scrollbar-thumb:hover,
.overflow-y-auto::-webkit-scrollbar-thumb:hover {
  background: rgba(100, 116, 139, 0.5);
}
</style>
