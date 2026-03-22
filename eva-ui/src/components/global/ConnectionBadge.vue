<script setup lang="ts">
import { ws } from '../../api/websocket'
</script>

<template>
  <div class="connection-badge" :class="{ connected: ws.connected.value }">
    <div class="badge-dot" />
    <span class="badge-text">{{ ws.connected.value ? 'Connected' : 'Reconnecting...' }}</span>
  </div>
</template>

<style scoped>
.connection-badge {
  position: fixed;
  bottom: 12px;
  right: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: 100px;
  background: rgba(248, 113, 113, 0.08);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(248, 113, 113, 0.12);
  font-family: var(--font-heading);
  font-size: 10px;
  letter-spacing: 0.5px;
  color: rgba(248, 113, 113, 0.7);
  z-index: 1000;
  transition: all 0.5s ease;
}

.connection-badge.connected {
  background: rgba(var(--accent-rgb), 0.06);
  border-color: rgba(var(--accent-rgb), 0.1);
  color: rgba(var(--accent-rgb), 0.6);
}

.badge-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--error);
  transition: all 0.5s ease;
}

.connected .badge-dot {
  background: var(--accent);
  box-shadow: 0 0 6px rgba(var(--accent-rgb), 0.4);
}

.badge-text {
  font-weight: 500;
}
</style>
