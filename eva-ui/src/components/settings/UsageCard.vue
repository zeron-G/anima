<script setup lang="ts">
const props = defineProps<{
  usage: { calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number }
}>()

function formatTokens(n: number) {
  if (!n) return '0'
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
  return String(n)
}
</script>

<template>
  <div class="config-card glass">
    <h3 class="card-title">Usage Today</h3>

    <div class="usage-grid">
      <div class="usage-item">
        <span class="usage-number">{{ usage.calls || 0 }}</span>
        <span class="usage-label">Calls</span>
      </div>
      <div class="usage-item">
        <span class="usage-number">{{ formatTokens(usage.prompt_tokens) }}</span>
        <span class="usage-label">Input</span>
      </div>
      <div class="usage-item">
        <span class="usage-number">{{ formatTokens(usage.completion_tokens) }}</span>
        <span class="usage-label">Output</span>
      </div>
      <div class="usage-item highlight">
        <span class="usage-number">{{ formatTokens(usage.total_tokens) }}</span>
        <span class="usage-label">Total</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.config-card { padding: var(--space-lg); }

.usage-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-sm);
}

.usage-item {
  text-align: center;
  padding: var(--space-md) var(--space-sm);
  border-radius: var(--radius-sm);
  background: rgba(var(--accent-rgb), 0.03);
  border: 1px solid var(--border);
}

.usage-item.highlight {
  background: rgba(var(--accent-rgb), 0.06);
  border-color: rgba(var(--accent-rgb), 0.1);
}

.usage-number {
  display: block;
  font-family: var(--font-display);
  font-size: 28px;
  font-weight: 300;
  color: var(--accent);
  line-height: 1;
  margin-bottom: 6px;
}

.usage-label {
  display: block;
  font-family: var(--font-heading);
  font-size: 10px;
  font-weight: 400;
  color: var(--text-dim);
  letter-spacing: 2px;
  text-transform: uppercase;
}
</style>
