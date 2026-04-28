<template>
  <div class="flex flex-wrap gap-1 mt-2">
    <span
      v-for="(c, i) in citations.slice(0, maxShow)"
      :key="i"
      class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded bg-slate-700/50 text-slate-300 hover:bg-slate-600/50 transition-colors"
    >
      <span v-if="c.source" class="text-cyan-400">{{ c.source }}</span>
      <span v-else class="text-slate-400">引用{{ i + 1 }}</span>
      <span v-if="c.relevance_score" class="text-emerald-400 ml-1">{{ (c.relevance_score * 100).toFixed(0) }}%</span>
    </span>
    <span v-if="citations.length > maxShow" class="text-xs text-slate-500">
      +{{ citations.length - maxShow }}
    </span>
  </div>
</template>

<script setup lang="ts">
interface Citation {
  title?: string
  source?: string
  url?: string
  relevance_score?: number
  snippet?: string
}

const props = withDefaults(defineProps<{
  citations: Citation[]
  maxShow?: number
}>(), {
  maxShow: 5,
})
</script>
