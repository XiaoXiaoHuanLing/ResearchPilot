import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('../views/DashboardView.vue'),
    },
    {
      path: '/topics',
      name: 'topics',
      component: () => import('../views/TopicsView.vue'),
    },
    {
      path: '/articles',
      name: 'articles',
      component: () => import('../views/ArticlesView.vue'),
    },
    {
      path: '/qa',
      name: 'qa',
      component: () => import('../views/QaView.vue'),
    },
    {
      path: '/reports',
      name: 'reports',
      component: () => import('../views/ReportsView.vue'),
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      component: () => import('../views/KnowledgeBaseView.vue'),
    },
    {
      path: '/copilot',
      name: 'copilot',
      component: () => import('../views/CopilotView.vue'),
    },
  ],
})

export default router
