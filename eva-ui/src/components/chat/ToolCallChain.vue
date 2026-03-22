<script setup lang="ts">
import { ref } from 'vue'

defineProps<{
  calls: Array<{ tool: string; args?: unknown; result_preview?: string; duration_ms?: number }>
}>()

const expanded = ref(false)
</script>

<template>
  <div class="tool-chain" @click="expanded = !expanded">
    <div class="chain-header">
      <svg class="chain-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
      </svg>
      <span class="chain-summary">{{ calls.length }} tool{{ calls.length > 1 ? 's' : '' }} used</span>
      <svg class="chain-chevron" :class="{ open: expanded }" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
        <path d="M6 9l6 6 6-6"/>
      </svg>
    </div>

    <transition name="expand">
      <div v-if="expanded" class="chain-details">
        <div v-for="(call, i) in calls" :key="i" class="tool-card">
          <div v-if="i > 0" class="tool-connector" />
          <div class="tool-name">{{ call.tool }}</div>
          <div v-if="call.args" class="tool-args">
            <code>{{ typeof call.args === 'string' ? call.args : JSON.stringify(call.args).slice(0, 100) }}</code>
          </div>
          <div v-if="call.result_preview" class="tool-result">
            {{ call.result_preview.slice(0, 120) }}
          </div>
          <div v-if="call.duration_ms" class="tool-time">{{ call.duration_ms }}ms</div>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.tool-chain { margin-top: 8px; cursor: pointer; }

.chain-header {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 10px; border-radius: var(--radius-sm);
  background: rgba(255,255,255,0.03);
  font-size: 12px; color: var(--text-dim);
  transition: background 0.2s;
}
.chain-header:hover { background: rgba(255,255,255,0.05); }

.chain-icon { opacity: 0.5; flex-shrink: 0; }
.chain-summary { flex: 1; }
.chain-chevron { opacity: 0.3; flex-shrink: 0; transition: transform 0.2s; }
.chain-chevron.open { transform: rotate(180deg); }

.chain-details {
  padding: 8px 0 0 16px;
  border-left: 2px solid rgba(var(--accent-rgb), 0.1);
  margin-left: 12px; margin-top: 4px;
}

.tool-card {
  padding: 6px 10px; margin-bottom: 6px;
  border-radius: var(--radius-sm);
  background: rgba(255,255,255,0.02);
  font-size: 12px;
}

.tool-connector { width: 2px; height: 6px; background: rgba(var(--accent-rgb), 0.1); margin: 0 0 4px 6px; }
.tool-name { color: var(--accent); font-weight: 500; font-size: 12px; }
.tool-args code { font-family: var(--font-mono); font-size: 11px; color: var(--text-dim); display: block; margin-top: 2px; }
.tool-result { color: var(--text-secondary); font-size: 11px; margin-top: 2px; }
.tool-time { font-family: var(--font-mono); color: var(--text-dim); font-size: 10px; text-align: right; opacity: 0.4; }

.expand-enter-active, .expand-leave-active { transition: all 0.2s ease; max-height: 500px; overflow: hidden; }
.expand-enter-from, .expand-leave-to { max-height: 0; opacity: 0; }
</style>
