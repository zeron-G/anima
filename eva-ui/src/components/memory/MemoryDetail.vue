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
  return new Date(ts * 1000).toLocaleString('zh-CN')
}
</script>

<template>
  <Transition name="slide">
    <div v-if="memory" class="memory-detail glass">
      <div class="detail-header">
        <span class="type-badge">{{ memory.type }}</span>
        <span class="importance">重要度: {{ (memory.importance * 100).toFixed(0) }}%</span>
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
.memory-detail { padding: 16px; max-width: 400px; }
.detail-header { display: flex; justify-content: space-between; margin-bottom: 10px; }
.type-badge { font-size: 11px; padding: 2px 8px; border-radius: 8px; background: hsla(200, 40%, 25%, 0.3); color: var(--eva-ice); }
.importance { font-size: 12px; color: var(--eva-text-dim); }
.detail-content { font-size: 13px; line-height: 1.6; color: var(--eva-text); max-height: 200px; overflow-y: auto; margin-bottom: 10px; white-space: pre-wrap; }
.detail-footer { display: flex; justify-content: space-between; font-size: 10px; color: var(--eva-text-dim); opacity: 0.5; }
.slide-enter-active, .slide-leave-active { transition: all 0.3s ease; }
.slide-enter-from, .slide-leave-to { opacity: 0; transform: translateX(20px); }
</style>
