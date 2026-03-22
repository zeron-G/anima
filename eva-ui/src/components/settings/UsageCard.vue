<script setup lang="ts">
const props = defineProps<{
  usage: { calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number }
}>()

function formatTokens(n: number) {
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
  return String(n)
}
</script>

<template>
  <div class="config-card glass">
    <h3 class="card-title">Token 用量 (今日)</h3>
    <div class="usage-grid">
      <div class="usage-stat">
        <span class="stat-number">{{ usage.calls }}</span>
        <span class="stat-label">调用次数</span>
      </div>
      <div class="usage-stat">
        <span class="stat-number">{{ formatTokens(usage.prompt_tokens) }}</span>
        <span class="stat-label">输入 tokens</span>
      </div>
      <div class="usage-stat">
        <span class="stat-number">{{ formatTokens(usage.completion_tokens) }}</span>
        <span class="stat-label">输出 tokens</span>
      </div>
      <div class="usage-stat">
        <span class="stat-number">{{ formatTokens(usage.total_tokens) }}</span>
        <span class="stat-label">总计</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.config-card { padding: 20px; }
.card-title { font-size: 15px; font-weight: 500; color: var(--eva-ice); margin-bottom: 16px; }
.usage-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.usage-stat { text-align: center; }
.stat-number { display: block; font-size: 24px; font-weight: 200; color: var(--eva-ice); }
.stat-label { display: block; font-size: 11px; color: var(--eva-text-dim); margin-top: 4px; }
</style>
