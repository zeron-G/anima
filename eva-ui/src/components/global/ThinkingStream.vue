<script setup lang="ts">
import { ref } from 'vue'
import { useThinkingStore } from '@/stores/thinkingStore'

const thinking = useThinkingStore()
const expanded = ref(false)

const axisIcons: Record<string, string> = {
  human: '\u{1F464}',
  self: '\u{1FA9E}',
  world: '\u{1F30D}',
}

const axisLabels: Record<string, string> = {
  human: 'Human Axis',
  self: 'Self Axis',
  world: 'World Axis',
}

function formatTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
</script>

<template>
  <div class="thinking-stream" v-if="thinking.latest" :class="{ expanded }">
    <!-- Collapsed bar -->
    <div class="stream-bar" @click="expanded = !expanded">
      <span class="stream-icon">&#x1F4AD;</span>
      <span class="stream-axis">{{ axisIcons[thinking.latest.axis] || '\u{1F4AD}' }}</span>
      <span class="stream-text">{{ thinking.latest.summary.slice(0, 80) }}{{ thinking.latest.summary.length > 80 ? '...' : '' }}</span>
      <span class="stream-toggle">{{ expanded ? '\u25BC' : '\u25B2' }}</span>
    </div>

    <!-- Expanded history -->
    <transition name="expand">
      <div v-if="expanded" class="stream-history">
        <div v-for="(entry, i) in [...thinking.entries].reverse().slice(0, 5)" :key="i" class="history-entry">
          <span class="entry-axis">{{ axisIcons[entry.axis] }} {{ axisLabels[entry.axis] }}</span>
          <span class="entry-summary">{{ entry.summary.slice(0, 120) }}</span>
          <span class="entry-action" :class="entry.action">{{ entry.action }}</span>
          <span class="entry-time">{{ formatTime(entry.timestamp) }}</span>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.thinking-stream {
  position: fixed;
  bottom: 0;
  left: 64px;
  right: 0;
  z-index: 90;
  background: hsla(220, 25%, 8%, 0.9);
  backdrop-filter: blur(16px);
  border-top: 1px solid var(--eva-glass-border);
  transition: all 0.3s ease;
}

.stream-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  cursor: pointer;
  font-size: 13px;
  color: var(--eva-text-dim);
  transition: color 0.2s;
}

.stream-bar:hover {
  color: var(--eva-text);
}

.stream-icon { opacity: 0.6; }
.stream-axis { font-size: 14px; }
.stream-text { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stream-toggle { font-size: 10px; opacity: 0.4; }

.stream-history {
  padding: 0 16px 12px;
  max-height: 200px;
  overflow-y: auto;
}

.history-entry {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 12px;
  border-bottom: 1px solid hsla(200, 20%, 20%, 0.2);
}

.entry-axis {
  color: var(--eva-ice);
  font-size: 12px;
  white-space: nowrap;
  min-width: 100px;
}

.entry-summary {
  flex: 1;
  color: var(--eva-text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.entry-action {
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  text-transform: uppercase;
}

.entry-action.quiet { color: var(--eva-text-dim); }
.entry-action.updated { color: hsl(140, 50%, 55%); background: hsla(140, 40%, 20%, 0.3); }
.entry-action.proposed { color: var(--eva-purple); background: hsla(270, 40%, 25%, 0.3); }

.entry-time {
  color: var(--eva-text-dim);
  opacity: 0.4;
  font-size: 11px;
  white-space: nowrap;
}

.expand-enter-active, .expand-leave-active {
  transition: all 0.3s ease;
}
.expand-enter-from, .expand-leave-to {
  max-height: 0;
  opacity: 0;
}
</style>
