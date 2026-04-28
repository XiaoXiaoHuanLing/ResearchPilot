<template>
  <div class="flex items-center gap-1">
    <span
      v-if="status === 'pending'"
      class="text-xs text-slate-400 flex items-center gap-1"
    >⏳ 待索引</span>
    <span
      v-else-if="status === 'indexing'"
      class="text-xs text-amber-400 flex items-center gap-1"
    >🔄 索引中</span>
    <span
      v-else-if="status === 'indexed'"
      class="text-xs text-emerald-400 flex items-center gap-1"
    >✅ 已索引</span>
    <span
      v-else-if="status === 'failed'"
      class="text-xs text-red-400 flex items-center gap-1"
    >❌ 失败</span>
    <span v-else class="text-xs text-slate-500">{{ status }}</span>
    <span v-if="chunkCount && chunkCount > 0" class="text-[10px] text-slate-500">({{ chunkCount }} chunks)</span>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  status: string
  chunkCount?: number
}>()
</script>
