<script setup lang="ts">
import { h, ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  NLayout, NLayoutSider, NLayoutHeader, NLayoutContent,
  NMenu, NIcon, NConfigProvider, NMessageProvider, NDialogProvider, darkTheme, type GlobalThemeOverrides,
} from 'naive-ui'
import {
  HomeOutline,
  BookmarkOutline,
  ChatbubbleOutline,
  DocumentTextOutline,
  ListOutline,
  LibraryOutline,
  RocketOutline,
} from '@vicons/ionicons5'

const router = useRouter()
const route = useRoute()
const collapsed = ref(false)

function renderIcon(icon: any) {
  return () => h(NIcon, null, { default: () => h(icon) })
}

const menuOptions = [
  { label: '仪表盘', key: 'dashboard', icon: renderIcon(HomeOutline) },
  { label: '专题管理', key: 'topics', icon: renderIcon(ListOutline) },
  { label: '资讯中心', key: 'articles', icon: renderIcon(BookmarkOutline) },
  { label: '智能对话', key: 'qa', icon: renderIcon(ChatbubbleOutline) },
  { label: '报告中心', key: 'reports', icon: renderIcon(DocumentTextOutline) },
  { label: '知识库', key: 'knowledge', icon: renderIcon(LibraryOutline) },
  { label: '🤖 智能助手', key: 'copilot', icon: renderIcon(RocketOutline) },
]

function handleMenuUpdate(key: string) {
  router.push({ name: key })
}

const activeKey = computed(() => route.name as string)

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#06b6d4',
    primaryColorHover: '#22d3ee',
    primaryColorPressed: '#0891b2',
    primaryColorSuppl: '#06b6d4',
    bodyColor: '#0f172a',
    cardColor: '#1e293b',
    modalColor: '#1e293b',
    popoverColor: '#1e293b',
    tableColor: '#1e293b',
    inputColor: '#0f172a',
    hoverColor: 'rgba(6, 182, 212, 0.08)',
    textColor1: '#f1f5f9',
    textColor2: '#cbd5e1',
    textColor3: '#94a3b8',
    dividerColor: 'rgba(148, 163, 184, 0.15)',
    borderColor: 'rgba(148, 163, 184, 0.2)',
  },
  Menu: {
    itemTextColor: '#cbd5e1',
    itemTextColorActive: '#22d3ee',
    itemTextColorChildActive: '#22d3ee',
    itemTextColorActiveHover: '#22d3ee',
    itemColorActive: 'rgba(6, 182, 212, 0.12)',
    itemColorActiveHover: 'rgba(6, 182, 212, 0.18)',
  },
  Card: {
    borderColor: 'rgba(148, 163, 184, 0.12)',
    color: '#1e293b',
  },
  Tag: {
    borderRadius: '9999px',
  },
  Button: {
    borderRadiusMedium: '8px',
    borderRadiusSmall: '6px',
  },
  Input: {
    borderRadius: '8px',
  },
}
</script>

<template>
  <n-config-provider :theme="darkTheme" :theme-overrides="themeOverrides">
    <n-message-provider>
    <n-dialog-provider>
    <n-layout has-sider style="height: 100vh">
      <n-layout-sider
        bordered
        collapse-mode="width"
        :collapsed-width="64"
        :width="220"
        :collapsed="collapsed"
        show-trigger
        @collapse="collapsed = true"
        @expand="collapsed = false"
        :native-scrollbar="false"
        content-style="padding: 0;"
        style="background: #0f172a;"
      >
        <div class="flex items-center gap-2 px-4 py-5" style="border-bottom: 1px solid rgba(148,163,184,0.12);">
          <span class="text-2xl">🐕</span>
          <span v-if="!collapsed" class="text-lg font-bold" style="color: #22d3ee;">ResearchPilot</span>
        </div>
        <n-menu
          :collapsed="collapsed"
          :collapsed-width="64"
          :collapsed-icon-size="22"
          :options="menuOptions"
          :value="activeKey"
          @update:value="handleMenuUpdate"
        />
      </n-layout-sider>

      <n-layout>
        <n-layout-header bordered style="height: 52px; display: flex; align-items: center; justify-content: space-between; padding: 0 24px; background: #1e293b; border-bottom: 1px solid rgba(148,163,184,0.12);">
          <span style="color: #64748b; font-size: 12px; letter-spacing: 1.5px;">OPEN-SOURCE RESEARCH WORKBENCH</span>
          <span style="color: #94a3b8; font-size: 13px;">{{ new Date().toLocaleDateString('zh-CN') }}</span>
        </n-layout-header>

        <n-layout-content content-style="padding: 28px;" style="min-height: calc(100vh - 52px); background: linear-gradient(160deg, #0f172a 0%, #1e293b 40%, #0f172a 100%);">
          <router-view />
        </n-layout-content>
      </n-layout>
    </n-layout>
    </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
