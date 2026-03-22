<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'

const router = useRouter()
const route = useRoute()

const navItems = [
  { path: '/', name: 'Chat', icon: '\uD83D\uDCAC', key: 'chat' },
  { path: '/soulscape', name: 'Soulscape', icon: '\uD83D\uDD2E', key: 'soulscape' },
  { path: '/evolution', name: 'Evolution', icon: '\uD83E\uDDEC', key: 'evolution' },
  { path: '/memory', name: 'Memory', icon: '\u2728', key: 'memory' },
  { path: '/network', name: 'Network', icon: '\uD83C\uDF10', key: 'network' },
  { path: '/settings', name: 'Settings', icon: '\u2699\uFE0F', key: 'settings' },
]

function isActive(path: string) {
  return route.path === path
}

function navigate(path: string) {
  router.push(path)
}
</script>

<template>
  <nav class="orbit-nav">
    <div class="orbit-track">
      <div
        v-for="item in navItems"
        :key="item.key"
        class="orbit-node"
        :class="{ active: isActive(item.path) }"
        @click="navigate(item.path)"
        :title="item.name"
      >
        <span class="node-icon">{{ item.icon }}</span>
        <span class="node-label">{{ item.name }}</span>
        <div class="node-glow" v-if="isActive(item.path)" />
      </div>
    </div>
  </nav>
</template>

<style scoped>
.orbit-nav {
  width: 64px;
  height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: hsla(220, 25%, 8%, 0.8);
  backdrop-filter: blur(20px);
  border-right: 1px solid var(--eva-glass-border);
  z-index: 100;
  flex-shrink: 0;
}

.orbit-track {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
}

.orbit-node {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  position: relative;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  background: transparent;
}

.orbit-node:hover {
  background: var(--eva-glass);
  transform: scale(1.15);
}

.orbit-node:hover .node-label {
  opacity: 1;
  transform: translateX(0);
  pointer-events: auto;
}

.orbit-node.active {
  background: hsla(200, 60%, 40%, 0.2);
}

.node-icon {
  font-size: 20px;
  z-index: 1;
}

.node-label {
  position: absolute;
  left: 56px;
  background: var(--eva-surface);
  backdrop-filter: blur(12px);
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 13px;
  white-space: nowrap;
  opacity: 0;
  transform: translateX(-8px);
  transition: all 0.2s ease;
  pointer-events: none;
  border: 1px solid var(--eva-glass-border);
  color: var(--eva-text);
}

.node-glow {
  position: absolute;
  inset: -2px;
  border-radius: 50%;
  background: radial-gradient(circle, var(--eva-ice) 0%, transparent 70%);
  opacity: 0.3;
  animation: breathe var(--breath-duration) ease-in-out infinite;
}

@keyframes breathe {
  0%, 100% { opacity: 0.2; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(1.1); }
}
</style>
