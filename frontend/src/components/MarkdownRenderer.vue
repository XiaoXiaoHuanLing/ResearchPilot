<template>
  <div class="markdown-content prose prose-invert prose-sm max-w-none" v-html="rendered"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ content: string }>()

const rendered = computed(() => {
  if (!props.content) return ''
  // Simple markdown-like rendering
  let html = props.content
    // Headers
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    // Bold
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // Italic
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // Code blocks
    .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    // Inline code
    .replace(/`(.+?)`/g, '<code>$1</code>')
    // Lists
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    // Paragraphs (double newline)
    .replace(/\n\n/g, '</p><p>')
    // Single newline
    .replace(/\n/g, '<br/>')
  return `<p>${html}</p>`
})
</script>

<style scoped>
.markdown-content :deep(h1) { font-size: 1.25rem; font-weight: 700; margin: 0.75rem 0 0.5rem; color: #22d3ee; }
.markdown-content :deep(h2) { font-size: 1.1rem; font-weight: 600; margin: 0.5rem 0 0.25rem; color: #67e8f9; }
.markdown-content :deep(h3) { font-size: 1rem; font-weight: 600; margin: 0.5rem 0 0.25rem; color: #a5f3fc; }
.markdown-content :deep(strong) { color: #e2e8f0; }
.markdown-content :deep(code) { background: rgba(100,116,139,0.2); padding: 0.1rem 0.3rem; border-radius: 3px; font-size: 0.85em; }
.markdown-content :deep(pre) { background: rgba(30,41,59,0.8); padding: 0.5rem; border-radius: 6px; overflow-x: auto; margin: 0.5rem 0; }
.markdown-content :deep(pre code) { background: none; padding: 0; }
.markdown-content :deep(li) { margin-left: 1rem; list-style: disc; }
</style>
