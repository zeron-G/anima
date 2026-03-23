<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'

const router = useRouter()
const route = useRoute()

interface NavItem {
  path: string
  name: string
  key: string
  svg: string
}

const navItems: NavItem[] = [
  {
    path: '/', name: 'Chat', key: 'chat',
    svg: `<path d="M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H8.5L4 19V6z" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/><line x1="8" y1="8" x2="16" y2="8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><line x1="8" y1="11.5" x2="13" y2="11.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>`,
  },
  {
    path: '/soulscape', name: 'Soulscape', key: 'soulscape',
    svg: `<circle cx="12" cy="12" r="3.5" stroke="currentColor" stroke-width="1.5" fill="none"/><circle cx="12" cy="12" r="8" stroke="currentColor" stroke-width="1" fill="none" opacity="0.4"/><line x1="12" y1="2" x2="12" y2="5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><line x1="12" y1="19" x2="12" y2="22" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><line x1="2" y1="12" x2="5" y2="12" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><line x1="19" y1="12" x2="22" y2="12" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>`,
  },
  {
    path: '/evolution', name: 'Evolution', key: 'evolution',
    svg: `<path d="M6 3c0 4 6 6 6 9s-6 5-6 9" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/><path d="M18 3c0 4-6 6-6 9s6 5 6 9" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/><line x1="7" y1="6.5" x2="17" y2="6.5" stroke="currentColor" stroke-width="1" opacity="0.5" stroke-linecap="round"/><line x1="7" y1="17.5" x2="17" y2="17.5" stroke="currentColor" stroke-width="1" opacity="0.5" stroke-linecap="round"/><line x1="9" y1="12" x2="15" y2="12" stroke="currentColor" stroke-width="1" opacity="0.5" stroke-linecap="round"/>`,
  },
  {
    path: '/memory', name: 'Memory', key: 'memory',
    svg: `<circle cx="12" cy="6" r="1.2" fill="currentColor" opacity="0.9"/><circle cx="6" cy="14" r="1" fill="currentColor" opacity="0.7"/><circle cx="18" cy="12" r="0.8" fill="currentColor" opacity="0.6"/><circle cx="10" cy="18" r="1.1" fill="currentColor" opacity="0.8"/><circle cx="16" cy="17" r="0.7" fill="currentColor" opacity="0.5"/><line x1="12" y1="6" x2="6" y2="14" stroke="currentColor" stroke-width="0.7" opacity="0.25"/><line x1="12" y1="6" x2="18" y2="12" stroke="currentColor" stroke-width="0.7" opacity="0.25"/><line x1="6" y1="14" x2="10" y2="18" stroke="currentColor" stroke-width="0.7" opacity="0.25"/><line x1="18" y1="12" x2="16" y2="17" stroke="currentColor" stroke-width="0.7" opacity="0.25"/>`,
  },
  {
    path: '/network', name: 'Network', key: 'network',
    svg: `<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.5" fill="none"/><ellipse cx="12" cy="12" rx="4" ry="9" stroke="currentColor" stroke-width="1" fill="none" opacity="0.5"/><line x1="3" y1="9" x2="21" y2="9" stroke="currentColor" stroke-width="0.8" opacity="0.4" stroke-linecap="round"/><line x1="3" y1="15" x2="21" y2="15" stroke="currentColor" stroke-width="0.8" opacity="0.4" stroke-linecap="round"/>`,
  },
  {
    path: '/settings', name: 'Settings', key: 'settings',
    svg: `<circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32 1.41-1.41" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>`,
  },
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
    <!-- Logo mark -->
    <div class="nav-logo">
      <div class="logo-mark">
        <div class="logo-dot" />
      </div>
    </div>

    <div class="orbit-track">
      <div
        v-for="item in navItems"
        :key="item.key"
        class="orbit-node"
        :class="{ active: isActive(item.path) }"
        @click="navigate(item.path)"
      >
        <div v-if="isActive(item.path)" class="active-indicator" />

        <svg
          class="node-icon"
          viewBox="0 0 24 24"
          width="20"
          height="20"
          v-html="item.svg"
        />

        <span class="node-label">{{ item.name }}</span>
      </div>
    </div>

    <!-- Bottom accent -->
    <div class="nav-bottom-accent" />
  </nav>
</template>

<style scoped>
.orbit-nav {
  width: 64px;
  height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  background: rgba(5, 5, 8, 0.9);
  backdrop-filter: blur(24px);
  border-right: 1px solid var(--border);
  z-index: 100;
  flex-shrink: 0;
  position: relative;
}

/* Logo */
.nav-logo {
  padding: 24px 0 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.logo-mark {
  width: 32px;
  height: 32px;
  border: 1.5px solid var(--accent);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: box-shadow var(--transition);
}

.logo-mark:hover {
  box-shadow: 0 0 20px rgba(var(--accent-rgb), 0.3);
}

.logo-dot {
  width: 6px;
  height: 6px;
  background: var(--accent);
  border-radius: 50%;
  box-shadow: 0 0 12px rgba(var(--accent-rgb), 0.5);
}

/* Track */
.orbit-track {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: center;
  padding-top: 8px;
}

.orbit-node {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  position: relative;
  transition: all var(--transition);
  background: transparent;
  color: var(--text-dim);
}

.orbit-node:hover {
  background: rgba(var(--accent-rgb), 0.05);
  color: var(--text-secondary);
}

.orbit-node:hover .node-label {
  opacity: 1;
  transform: translateX(0);
  pointer-events: auto;
}

.orbit-node.active {
  color: var(--accent);
  background: rgba(var(--accent-rgb), 0.07);
}

/* Active line indicator */
.active-indicator {
  position: absolute;
  left: -1px;
  top: 50%;
  transform: translateY(-50%);
  width: 2px;
  height: 20px;
  border-radius: 1px;
  background: var(--accent);
  box-shadow: 0 0 8px rgba(var(--accent-rgb), 0.5);
  animation: indicatorGlow var(--breath-duration) ease-in-out infinite;
}

@keyframes indicatorGlow {
  0%, 100% { box-shadow: 0 0 6px rgba(var(--accent-rgb), 0.3); }
  50% { box-shadow: 0 0 14px rgba(var(--accent-rgb), 0.5); }
}

.node-icon {
  z-index: 1;
  transition: transform var(--transition-fast);
}

.orbit-node:hover .node-icon {
  transform: scale(1.08);
}

.node-label {
  position: absolute;
  left: 56px;
  background: var(--surface-elevated);
  backdrop-filter: blur(16px);
  padding: 6px 16px;
  border-radius: var(--radius-sm);
  font-family: var(--font-heading);
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 1px;
  white-space: nowrap;
  opacity: 0;
  transform: translateX(-8px);
  transition: all 0.25s var(--ease);
  pointer-events: none;
  border: 1px solid var(--border);
  color: var(--text);
  box-shadow: 0 4px 24px rgba(0, 0, 0, 0.5);
  z-index: 200;
}

.nav-bottom-accent {
  position: absolute;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  width: 20px;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(var(--accent-rgb), 0.2), transparent);
}
</style>
