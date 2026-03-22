<script setup lang="ts">
const props = defineProps<{
  stats: { total: number; by_type: Record<string, number> }
}>()
</script>

<template>
  <div class="memory-stats glass">
    <div class="stat-total">
      <span class="total-number">{{ stats.total }}</span>
      <span class="total-label">Memories</span>
    </div>
    <div class="stat-types">
      <div v-for="(count, type) in stats.by_type" :key="type" class="type-stat">
        <span class="type-label">{{ type }}</span>
        <span class="type-count">{{ count }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.memory-stats { padding: var(--space-lg); }
.stat-total { text-align: center; margin-bottom: var(--space-md); }
.total-number { font-family: var(--font-display); font-size: 36px; font-weight: 300; color: var(--accent); }
.total-label { display: block; font-family: var(--font-heading); font-size: 10px; letter-spacing: 2px; text-transform: uppercase; color: var(--text-dim); margin-top: 4px; }
.stat-types { display: flex; flex-wrap: wrap; gap: 6px; justify-content: center; }
.type-stat { padding: 4px 12px; border-radius: 100px; background: rgba(var(--accent-rgb), 0.04); border: 1px solid var(--border); font-size: 12px; }
.type-label { color: var(--text-dim); margin-right: 4px; text-transform: capitalize; }
.type-count { color: var(--text); font-family: var(--font-mono); font-weight: 500; }
</style>
