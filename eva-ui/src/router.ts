import { createRouter, createWebHistory } from 'vue-router'
import AppShell from './components/AppShell.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/ThresholdView.vue') },
    {
      path: '/',
      component: AppShell,
      children: [
        { path: '', name: 'talk', component: () => import('./views/ConversationView.vue') },
        { path: 'mind', name: 'mind', component: () => import('./views/MindView.vue') },
        { path: 'soul', name: 'soul', component: () => import('./views/SoulView.vue') },
        { path: 'memory', name: 'memory', component: () => import('./views/MemoryView.vue') },
        { path: 'evolution', name: 'evolution', component: () => import('./views/EvolutionView.vue') },
        { path: 'network', name: 'network', component: () => import('./views/NetworkView.vue') },
        { path: 'health', name: 'health', component: () => import('./views/HealthView.vue') },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

// Real guard: first visit goes through the threshold; once entered (or a token
// is present) the app is open. A gated backend still 401s and the interceptor
// bounces back to /login.
router.beforeEach((to) => {
  const entered = sessionStorage.getItem('eva_entered') || localStorage.getItem('eva_auth_token')
  if (to.name !== 'login' && !entered) return { name: 'login' }
  if (to.name !== 'login') sessionStorage.setItem('eva_entered', '1')
  return true
})

export default router
