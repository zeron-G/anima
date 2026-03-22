<script setup lang="ts">
const props = defineProps<{
  info: { version: string; uptime_s: number; agent_name: string; python_version: string; chromadb: boolean }
}>()

const emit = defineEmits<{
  (e: 'restart'): void
  (e: 'shutdown'): void
}>()

function formatUptime(s: number) {
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${h}h ${m}m`
}
</script>

<template>
  <div class="config-card glass">
    <h3 class="card-title">系统信息</h3>
    <div class="info-grid">
      <div class="info-row"><span class="info-label">版本</span><span class="info-value">{{ info.version }}</span></div>
      <div class="info-row"><span class="info-label">Agent</span><span class="info-value">{{ info.agent_name }}</span></div>
      <div class="info-row"><span class="info-label">运行时间</span><span class="info-value">{{ formatUptime(info.uptime_s) }}</span></div>
      <div class="info-row"><span class="info-label">Python</span><span class="info-value">{{ info.python_version }}</span></div>
      <div class="info-row"><span class="info-label">ChromaDB</span><span class="info-value" :class="{ ok: info.chromadb }">{{ info.chromadb ? '✓' : '✗' }}</span></div>
    </div>
    <div class="action-bar">
      <button class="action-btn restart" @click="emit('restart')">重启</button>
      <button class="action-btn shutdown" @click="emit('shutdown')">关闭</button>
    </div>
  </div>
</template>

<style scoped>
.config-card { padding: 20px; }
.card-title { font-size: 15px; font-weight: 500; color: var(--eva-ice); margin-bottom: 16px; }
.info-grid { margin-bottom: 16px; }
.info-row { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid hsla(200, 20%, 20%, 0.15); font-size: 13px; }
.info-label { color: var(--eva-text-dim); }
.info-value { color: var(--eva-text); font-family: 'JetBrains Mono', monospace; }
.info-value.ok { color: hsl(140, 50%, 55%); }
.action-bar { display: flex; gap: 8px; justify-content: flex-end; }
.action-btn { padding: 8px 16px; border-radius: 8px; font-size: 13px; cursor: pointer; border: 1px solid; }
.action-btn.restart { border-color: hsla(200, 50%, 50%, 0.2); background: hsla(200, 40%, 25%, 0.4); color: var(--eva-ice); }
.action-btn.shutdown { border-color: hsla(0, 40%, 40%, 0.2); background: hsla(0, 30%, 20%, 0.4); color: hsl(0, 50%, 60%); }
.action-btn:hover { opacity: 0.9; }
</style>
