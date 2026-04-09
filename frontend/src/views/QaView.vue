<script setup lang="ts">
import { ref } from 'vue'
import { NCard, NInput, NButton, NSpin, NEmpty, NDivider, NList, NListItem, NSpace, NTag, useMessage } from 'naive-ui'
import { useQaStore } from '../stores'

const qaStore = useQaStore()
const message = useMessage()
const question = ref('最近舰船领域有哪些值得关注的公开动态？')

async function handleAsk() {
  if (!question.value.trim()) {
    message.warning('请输入研究问题')
    return
  }
  await qaStore.ask(question.value)
}
</script>

<template>
  <div>
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-cyan-400">知识问答</h1>
      <p class="text-slate-400 text-sm mt-1">基于收藏资讯的检索式问答 · 后续将升级为 RAG 问答链路</p>
    </div>

    <n-card>
      <n-input
        v-model:value="question"
        type="textarea"
        :rows="4"
        placeholder="输入研究问题..."
      />
      <n-button type="primary" class="mt-3" :loading="qaStore.loading" @click="handleAsk">
        {{ qaStore.loading ? '分析中...' : '开始分析' }}
      </n-button>
    </n-card>

    <n-spin :show="qaStore.loading">
      <n-card v-if="qaStore.result" class="mt-4" title="分析结果">
        <h3 class="text-lg font-semibold text-slate-100 mb-2">回答</h3>
        <p class="text-slate-300 leading-relaxed">{{ qaStore.result.answer }}</p>

        <n-divider>引用来源</n-divider>

        <n-list v-if="qaStore.result.citations.length > 0" bordered>
          <n-list-item v-for="(citation, idx) in qaStore.result.citations" :key="idx">
            <n-space align="center" :wrap="false">
              <n-tag size="small" :bordered="false">{{ idx + 1 }}</n-tag>
              <strong class="text-slate-100">{{ citation.title }}</strong>
              <span class="text-slate-400 text-sm">· {{ citation.source }}</span>
              <a v-if="citation.url" :href="citation.url" target="_blank" class="text-cyan-400 text-xs hover:underline whitespace-nowrap">查看原文</a>
            </n-space>
          </n-list-item>
        </n-list>
        <n-empty v-else description="无引用来源" size="small" />
      </n-card>
    </n-spin>
  </div>
</template>
