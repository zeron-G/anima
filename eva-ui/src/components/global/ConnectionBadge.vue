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
  padding: 4px 12px;
  border-radius: 20px;
  background: hsla(0, 40%, 12%, 0.6);
  backdrop-filter: blur(12px);
  border: 1px solid hsla(0, 40%, 30%, 0.15);
  font-family: 'DM Sans', sans-serif;
  font-size: 10px;
  letter-spacing: 0.03em;
  color: hsla(0, 50%, 65%, 0.8);
  z-index: 1000;
  transition: all 0.5s ease;
}

.connection-badge.connected {
  background: hsla(150, 30%, 10%, 0.5);
  border-color: hsla(150, 30%, 30%, 0.15);
  color: hsla(150, 40%, 60%, 0.7);
}

.status-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: hsl(0, 50%, 50%);
  transition: all 0.5s ease;
}

.connected .status-dot {
  background: hsl(150, 50%, 50%);
  box-shadow: 0 0 6px hsla(150, 50%, 50%, 0.5);
}

.status-text {
  font-weight: 500;
}
</style>
