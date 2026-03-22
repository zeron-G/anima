<script setup lang="ts">
const props = defineProps<{
  governance: any
}>()

const emit = defineEmits<{
  (e: 'changeMode', mode: string): void
}>()

const modes = ['active', 'cautious', 'minimal'] as const
const modeLabels: Record<string, string> = { active: 'Autonomous', cautious: 'Cautious', minimal: 'Passive' }
const modeColors: Record<string, string> = { active: '#34d399', cautious: '#fbbf24', minimal: '#f87171' }
</script>

<template>
  <div class="governance-bar glass">
    <div class="bar-left">
      <span class="bar-label">Governance</span>
      <div class="mode-selector">
        <button v-for="m in modes" :key="m"
                class="mode-btn"
                :class="{ active: governance?.activity_level === m }"
                :style="governance?.activity_level === m ? { borderColor: modeColors[m], color: modeColors[m] } : {}"
                @click="emit('changeMode', m)">
          {{ modeLabels[m] }}
        </button>
      </div>
    </div>
    <div class="bar-right">
      <span class="stat">Drift: {{ governance?.drift_scores?.length || 0 }} entries</span>
      <span class="stat">Thinking: {{ governance?.recent_self_thinking?.length || 0 }}/5</span>
    </div>
  </div>
</template>

<style scoped>
.governance-bar { display: flex; justify-content: space-between; align-items: center; padding: 12px var(--space-lg); }
.bar-left { display: flex; align-items: center; gap: 12px; }
.bar-label { font-family: var(--font-heading); font-size: 11px; letter-spacing: 1.5px; text-transform: uppercase; color: var(--text-dim); }
.mode-selector { display: flex; gap: 4px; }
.mode-btn { padding: 5px 14px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: transparent; color: var(--text-dim); font-family: var(--font-heading); font-size: 11px; letter-spacing: 0.5px; cursor: pointer; transition: all 0.2s; }
.mode-btn:hover { background: rgba(255,255,255,0.03); }
.mode-btn.active { background: rgba(255,255,255,0.04); }
.bar-right { display: flex; gap: 16px; }
.stat { font-family: var(--font-mono); font-size: 11px; color: var(--text-dim); }
</style>
