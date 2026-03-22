<script setup lang="ts">
const props = defineProps<{
  info: { version: string; uptime_s: number; agent_name: string; python_version: string; chromadb: boolean }
}>()

const emit = defineEmits<{
  (e: 'restart'): void
  (e: 'shutdown'): void
}>()

function formatUptime(s: number) {
  if (!s) return '--'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${h}h ${m}m`
}
</script>

<template>
  <div class="config-card glass">
    <h3 class="card-title">System</h3>

    <div class="info-list">
      <div class="info-row">
        <span class="info-label">Version</span>
        <span class="info-value mono">{{ info.version || '--' }}</span>
      </div>
      <div class="info-row">
        <span class="info-label">Agent</span>
        <span class="info-value">{{ info.agent_name || '--' }}</span>
      </div>
      <div class="info-row">
        <span class="info-label">Uptime</span>
        <span class="info-value mono">{{ formatUptime(info.uptime_s) }}</span>
      </div>
      <div class="info-row">
        <span class="info-label">Python</span>
        <span class="info-value mono">{{ info.python_version || '--' }}</span>
      </div>
      <div class="info-row">
        <span class="info-label">ChromaDB</span>
        <span class="info-value">
          <span class="status-indicator" :class="info.chromadb ? 'ok' : 'off'" />
          {{ info.chromadb ? 'Active' : 'Inactive' }}
        </span>
      </div>
    </div>

    <div class="action-row">
      <button class="action-btn restart" @click="emit('restart')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
          <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
        </svg>
        Restart
      </button>
      <button class="action-btn shutdown" @click="emit('shutdown')">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
          <path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/>
        </svg>
        Shutdown
      </button>
    </div>
  </div>
</template>

<style scoped>
.config-card { padding: var(--space-lg); }

.info-list {
  display: flex;
  flex-direction: column;
  margin-bottom: var(--space-lg);
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}

.info-row:last-child { border-bottom: none; }

.info-label {
  color: var(--text-secondary);
}

.info-value {
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 6px;
}

.info-value.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}

.status-indicator {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.status-indicator.ok {
  background: var(--success);
  box-shadow: 0 0 6px rgba(52, 211, 153, 0.4);
}

.status-indicator.off {
  background: var(--text-dim);
}

.action-row {
  display: flex;
  gap: 8px;
}

.action-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 9px 16px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-family: var(--font-heading);
  letter-spacing: 0.5px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.action-btn.restart {
  border: 1px solid rgba(var(--accent-rgb), 0.15);
  background: rgba(var(--accent-rgb), 0.06);
  color: var(--accent);
}

.action-btn.restart:hover {
  background: rgba(var(--accent-rgb), 0.12);
  border-color: rgba(var(--accent-rgb), 0.25);
}

.action-btn.shutdown {
  border: 1px solid rgba(248, 113, 113, 0.15);
  background: rgba(248, 113, 113, 0.06);
  color: var(--error);
}

.action-btn.shutdown:hover {
  background: rgba(248, 113, 113, 0.12);
  border-color: rgba(248, 113, 113, 0.25);
}
</style>
