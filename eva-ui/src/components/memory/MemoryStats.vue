<script setup lang="ts">
const props = defineProps<{
  stats: { total: number; by_type: Record<string, number> }
}>()

const typeLabels: Record<string, string> = {
  chat: '对话', self_thinking: '思考', system: '系统', task: '任务',
}
</script>

<template>
  <div class="memory-stats glass">
    <div class="stat-total">
      <span class="total-number">{{ stats.total }}</span>
      <span class="total-label">条记忆</span>
    </div>
    <div class="stat-types">
      <div v-for="(count, type) in stats.by_type" :key="type" class="type-stat">
        <span class="type-label">{{ typeLabels[type as string] || type }}</span>
        <span class="type-count">{{ count }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.memory-stats { padding: 16px; }
.stat-total { text-align: center; margin-bottom: 12px; }
.total-number { font-size: 36px; font-weight: 200; color: var(--eva-ice); }
.total-label { display: block; font-size: 12px; color: var(--eva-text-dim); }
.stat-types { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; }
.type-stat { padding: 4px 10px; border-radius: 8px; background: hsla(200, 20%, 15%, 0.3); font-size: 12px; }
.type-label { color: var(--eva-text-dim); margin-right: 4px; }
.type-count { color: var(--eva-text); font-weight: 600; }
</style>
