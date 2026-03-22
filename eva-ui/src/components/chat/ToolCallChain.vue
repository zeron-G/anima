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
      <span class="chain-icon">&#128295;</span>
      <span class="chain-summary">{{ calls.length }} tool{{ calls.length > 1 ? 's' : '' }} used</span>
      <span class="chain-toggle">{{ expanded ? '\u25BC' : '\u25B6' }}</span>
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
          <div v-if="call.duration_ms" class="tool-time">
            {{ call.duration_ms }}ms
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.tool-chain {
  margin-top: 8px;
  cursor: pointer;
}

.chain-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 8px;
  background: hsla(220, 20%, 15%, 0.4);
  font-size: 12px;
  color: var(--eva-text-dim);
  transition: background 0.2s;
}

.chain-header:hover {
  background: hsla(220, 20%, 20%, 0.5);
}

.chain-icon { font-size: 14px; }
.chain-summary { flex: 1; }
.chain-toggle { font-size: 10px; opacity: 0.5; }

.chain-details {
  padding: 8px 0 0 16px;
  border-left: 2px solid hsla(200, 40%, 40%, 0.2);
  margin-left: 12px;
  margin-top: 4px;
}

.tool-card {
  padding: 6px 10px;
  margin-bottom: 6px;
  border-radius: 6px;
  background: hsla(220, 20%, 12%, 0.4);
  font-size: 12px;
}

.tool-connector {
  width: 2px;
  height: 6px;
  background: hsla(200, 40%, 40%, 0.2);
  margin: 0 0 4px 6px;
}

.tool-name {
  color: var(--eva-ice);
  font-weight: 600;
  font-size: 12px;
}

.tool-args code {
  font-size: 11px;
  color: var(--eva-text-dim);
  display: block;
  margin-top: 2px;
}

.tool-result {
  color: var(--eva-silver);
  font-size: 11px;
  margin-top: 2px;
  opacity: 0.7;
}

.tool-time {
  color: var(--eva-text-dim);
  font-size: 10px;
  text-align: right;
  opacity: 0.4;
}

.expand-enter-active, .expand-leave-active {
  transition: all 0.2s ease;
  max-height: 500px;
  overflow: hidden;
}
.expand-enter-from, .expand-leave-to {
  max-height: 0;
  opacity: 0;
}
</style>
