import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue') },
    { path: '/', name: 'chat', component: () => import('./views/ChatView.vue') },
    { path: '/soulscape', name: 'soulscape', component: () => import('./views/SoulscapeView.vue') },
    { path: '/evolution', name: 'evolution', component: () => import('./views/EvolutionView.vue') },
    { path: '/memory', name: 'memory', component: () => import('./views/MemoryView.vue') },
    { path: '/network', name: 'network', component: () => import('./views/NetworkView.vue') },
    { path: '/robotics', name: 'robotics', component: () => import('./views/RoboticsView.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
  ],
})

// Auth guard
router.beforeEach((to) => {
  if (to.name !== 'login') {
    // If auth is configured but no token, redirect to login
    // (will be checked against backend on first API call)
    if (!localStorage.getItem('eva_auth_token')) {
      // Token check placeholder — currently permissive
    }
  }
  return true
})

export default router
