<script setup lang="ts">
const props = defineProps<{
  memory: {
    id: string
    content: string
    type: string
    importance: number
    created_at: number
  } | null
}>()

function formatTime(ts: number) {
  return new Date(ts * 1000).toLocaleString()
}
</script>

<template>
  <Transition name="slide">
    <div v-if="memory" class="memory-detail glass">
      <div class="detail-header">
        <span class="type-badge">{{ memory.type }}</span>
        <span class="importance">Importance: {{ (memory.importance * 100).toFixed(0) }}%</span>
      </div>
      <div class="detail-content">{{ memory.content }}</div>
      <div class="detail-footer">
        <span class="detail-id">{{ memory.id }}</span>
        <span class="detail-time">{{ formatTime(memory.created_at) }}</span>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.memory-detail { padding: var(--space-lg); max-width: 400px; }
.detail-header { display: flex; justify-content: space-between; margin-bottom: var(--space-md); }
.type-badge { font-family: var(--font-heading); font-size: 10px; letter-spacing: 1px; text-transform: uppercase; padding: 3px 10px; border-radius: 100px; background: rgba(var(--accent-rgb), 0.08); color: var(--accent); }
.importance { font-size: 12px; color: var(--text-dim); }
.detail-content { font-size: 13px; line-height: 1.7; color: var(--text); max-height: 200px; overflow-y: auto; margin-bottom: var(--space-md); white-space: pre-wrap; }
.detail-footer { display: flex; justify-content: space-between; font-family: var(--font-mono); font-size: 10px; color: var(--text-dim); opacity: 0.5; }
.slide-enter-active, .slide-leave-active { transition: all 0.3s ease; }
.slide-enter-from, .slide-leave-to { opacity: 0; transform: translateX(20px); }
</style>
