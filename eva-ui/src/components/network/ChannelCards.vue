<script setup lang="ts">
const props = defineProps<{
  channels: Array<{ name: string; connected: boolean; type: string }>
}>()
</script>

<template>
  <div class="channel-cards">
    <div v-for="ch in channels" :key="ch.name" class="channel-card glass">
      <svg class="ch-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
      </svg>
      <span class="ch-name">{{ ch.name }}</span>
      <span class="ch-status" :class="{ online: ch.connected }">
        <span class="status-dot" :class="ch.connected ? 'success' : 'error'" />
        {{ ch.connected ? 'Online' : 'Offline' }}
      </span>
    </div>
    <div v-if="channels.length === 0" class="empty">No active channels</div>
  </div>
</template>

<style scoped>
.channel-cards { display: flex; gap: 10px; flex-wrap: wrap; }
.channel-card { display: flex; align-items: center; gap: 10px; padding: 10px 16px; min-width: 160px; }
.ch-icon { color: var(--text-dim); flex-shrink: 0; }
.ch-name { font-size: 13px; color: var(--text); flex: 1; }
.ch-status { display: flex; align-items: center; gap: 5px; font-family: var(--font-heading); font-size: 10px; letter-spacing: 0.5px; text-transform: uppercase; }
.ch-status.online { color: var(--success); }
.ch-status:not(.online) { color: var(--text-dim); }
.empty { color: var(--text-dim); font-size: 13px; }
</style>
