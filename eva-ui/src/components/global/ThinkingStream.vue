<script setup lang="ts">
import { ref } from 'vue'
import { useThinkingStore } from '@/stores/thinkingStore'

const thinking = useThinkingStore()
const expanded = ref(false)

/* SVG-based axis indicators instead of emoji */
const axisSvg: Record<string, string> = {
  human: `<circle cx="8" cy="5" r="2.5" stroke="currentColor" stroke-width="1.3" fill="none"/><path d="M3 16v-1a5 5 0 0 1 10 0v1" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round"/>`,
  self: `<circle cx="8" cy="8" r="5" stroke="currentColor" stroke-width="1.3" fill="none"/><circle cx="8" cy="8" r="1.5" fill="currentColor" opacity="0.5"/>`,
  world: `<circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.2" fill="none"/><ellipse cx="8" cy="8" rx="2.5" ry="6" stroke="currentColor" stroke-width="0.8" fill="none" opacity="0.5"/><line x1="2" y1="6" x2="14" y2="6" stroke="currentColor" stroke-width="0.6" opacity="0.4"/><line x1="2" y1="10" x2="14" y2="10" stroke="currentColor" stroke-width="0.6" opacity="0.4"/>`,
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
      <svg class="stream-icon" width="14" height="14" viewBox="0 0 16 16" fill="none">
        <circle cx="4" cy="11" r="1.5" fill="currentColor" opacity="0.4"/>
        <circle cx="7" cy="7" r="2" fill="currentColor" opacity="0.6"/>
        <circle cx="11" cy="4" r="2.5" fill="currentColor" opacity="0.3"/>
      </svg>
      <svg class="stream-axis-icon" width="16" height="16" viewBox="0 0 16 16" v-html="axisSvg[thinking.latest.axis] || axisSvg.self" />
      <span class="stream-text">{{ thinking.latest.summary.slice(0, 80) }}{{ thinking.latest.summary.length > 80 ? '...' : '' }}</span>
      <svg class="stream-toggle" :class="{ 'is-expanded': expanded }" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
        <path d="M6 9l6 6 6-6"/>
      </svg>
    </div>

    <!-- Expanded history -->
    <transition name="expand">
      <div v-if="expanded" class="stream-history">
        <div v-for="(entry, i) in [...thinking.entries].reverse().slice(0, 5)" :key="i" class="history-entry">
          <div class="entry-axis-wrap">
            <svg class="entry-axis-icon" width="14" height="14" viewBox="0 0 16 16" v-html="axisSvg[entry.axis] || axisSvg.self" />
            <span class="entry-axis-label">{{ axisLabels[entry.axis] || entry.axis }}</span>
          </div>
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
  left: 60px;
  right: 0;
  z-index: 90;
  background: hsla(222, 30%, 6%, 0.92);
  backdrop-filter: blur(20px);
  border-top: 1px solid hsla(var(--eva-ice-hsl), 0.06);
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
  color: var(--eva-text-secondary);
}

.stream-icon { opacity: 0.5; flex-shrink: 0; }
.stream-axis-icon { opacity: 0.6; flex-shrink: 0; color: var(--eva-ice); }
.stream-text { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.stream-toggle {
  opacity: 0.3;
  flex-shrink: 0;
  transition: transform 0.25s ease;
}

.stream-toggle.is-expanded {
  transform: rotate(180deg);
}

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
  border-bottom: 1px solid hsla(var(--eva-ice-hsl), 0.04);
}

.entry-axis-wrap {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 100px;
  flex-shrink: 0;
}

.entry-axis-icon {
  color: var(--eva-ice);
  opacity: 0.6;
}

.entry-axis-label {
  color: var(--eva-ice);
  font-family: 'Sora', sans-serif;
  font-size: 11px;
  white-space: nowrap;
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
  font-family: 'Sora', sans-serif;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.entry-action.quiet { color: var(--eva-text-dim); }
.entry-action.updated { color: hsl(150, 50%, 55%); background: hsla(150, 40%, 20%, 0.25); }
.entry-action.proposed { color: var(--eva-purple); background: hsla(270, 40%, 25%, 0.25); }

.entry-time {
  color: var(--eva-text-dim);
  opacity: 0.35;
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
