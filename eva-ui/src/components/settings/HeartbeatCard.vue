<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{ config: any }>()
const emit = defineEmits<{ (e: 'update', key: string, value: any): void }>()

const scriptInterval = ref(30)
const llmInterval = ref(300)
const majorInterval = ref(900)
const editing = ref(false)
const saveStatus = ref('')

watch(() => props.config, (cfg) => {
  if (!cfg?.heartbeat) return
  scriptInterval.value = cfg.heartbeat.script_interval_s || 30
  llmInterval.value = cfg.heartbeat.llm_interval_s || 300
  majorInterval.value = cfg.heartbeat.major_interval_s || 900
}, { immediate: true, deep: true })

function formatInterval(s: number) {
  if (s >= 60) return `${Math.round(s / 60)} min`
  return `${s}s`
}

async function applyChanges() {
  saveStatus.value = 'saving'
  try {
    emit('update', 'heartbeat.script_interval_s', scriptInterval.value)
    emit('update', 'heartbeat.llm_interval_s', llmInterval.value)
    emit('update', 'heartbeat.major_interval_s', majorInterval.value)
    editing.value = false
    saveStatus.value = 'saved'
    setTimeout(() => { saveStatus.value = '' }, 2000)
  } catch {
    saveStatus.value = 'error'
  }
}
</script>

<template>
  <div class="config-card glass">
    <div class="card-header-row">
      <h3 class="card-title">Heartbeat</h3>
      <button v-if="!editing" class="edit-btn" @click="editing = true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
        </svg>
        Edit
      </button>
    </div>

    <div class="hb-grid">
      <div class="hb-item">
        <div class="hb-label">Script</div>
        <div v-if="!editing" class="hb-value">{{ formatInterval(scriptInterval) }}</div>
        <div v-else class="hb-input-row">
          <input v-model.number="scriptInterval" type="number" class="input-field hb-input" />
          <span class="hb-unit">sec</span>
        </div>
        <div class="hb-desc">System sampling, file monitoring</div>
      </div>

      <div class="hb-item">
        <div class="hb-label">LLM</div>
        <div v-if="!editing" class="hb-value">{{ formatInterval(llmInterval) }}</div>
        <div v-else class="hb-input-row">
          <input v-model.number="llmInterval" type="number" class="input-field hb-input" />
          <span class="hb-unit">sec</span>
        </div>
        <div class="hb-desc">Self-thinking, tri-axis dispatch</div>
      </div>

      <div class="hb-item">
        <div class="hb-label">Major</div>
        <div v-if="!editing" class="hb-value">{{ formatInterval(majorInterval) }}</div>
        <div v-else class="hb-input-row">
          <input v-model.number="majorInterval" type="number" class="input-field hb-input" />
          <span class="hb-unit">sec</span>
        </div>
        <div class="hb-desc">Evolution evaluation</div>
      </div>
    </div>

    <button v-if="editing" class="apply-btn btn-primary" @click="applyChanges">
      <span v-if="saveStatus === 'saved'">Applied</span>
      <span v-else>Apply Changes</span>
    </button>
  </div>
</template>

<style scoped>
.config-card { padding: var(--space-lg); }

.card-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-lg);
}

.card-header-row .card-title { margin-bottom: 0; }

.edit-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.edit-btn:hover {
  border-color: var(--border-hover);
  color: var(--accent);
}

.hb-grid {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.hb-item {
  display: grid;
  grid-template-columns: 80px 100px 1fr;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-sm) 0;
  border-bottom: 1px solid var(--border);
}

.hb-item:last-child { border-bottom: none; }

.hb-label {
  font-family: var(--font-heading);
  font-size: 13px;
  font-weight: 400;
  color: var(--text);
}

.hb-value {
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--accent);
  font-weight: 500;
}

.hb-input-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.hb-input {
  width: 72px;
  padding: 6px 8px;
  font-size: 12px;
}

.hb-unit {
  font-size: 11px;
  color: var(--text-dim);
}

.hb-desc {
  font-size: 12px;
  color: var(--text-dim);
}

.apply-btn {
  width: 100%;
  margin-top: var(--space-md);
  padding: 10px;
  font-size: 12px;
}
</style>
