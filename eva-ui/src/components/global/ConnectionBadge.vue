<script setup lang="ts">
import { ws } from '../../api/websocket'
</script>

<template>
  <div class="connection-badge" :class="{ connected: ws.connected.value }">
    <div class="status-dot" />
    <span class="status-text">{{ ws.connected.value ? 'Connected' : 'Reconnecting...' }}</span>
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
  padding: 4px 10px;
  border-radius: 16px;
  background: hsla(0, 60%, 20%, 0.6);
  backdrop-filter: blur(8px);
  border: 1px solid hsla(0, 60%, 40%, 0.2);
  font-size: 11px;
  color: hsla(0, 60%, 70%, 0.8);
  z-index: 1000;
  transition: all 0.5s ease;
}

.connection-badge.connected {
  background: hsla(140, 40%, 15%, 0.6);
  border-color: hsla(140, 40%, 40%, 0.2);
  color: hsla(140, 40%, 65%, 0.8);
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: hsl(0, 60%, 50%);
  transition: background 0.5s ease;
}

.connected .status-dot {
  background: hsl(140, 50%, 50%);
  box-shadow: 0 0 6px hsl(140, 50%, 50%);
}

.status-text {
  font-weight: 500;
}
</style>
