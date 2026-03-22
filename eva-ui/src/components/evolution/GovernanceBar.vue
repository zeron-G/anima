<script setup lang="ts">
const props = defineProps<{
  governance: any
}>()

const emit = defineEmits<{
  (e: 'changeMode', mode: string): void
}>()

const modes = ['active', 'cautious', 'minimal'] as const
const modeLabels: Record<string, string> = { active: '完全自主', cautious: '保守', minimal: '被动' }
const modeColors: Record<string, string> = { active: '#44cc66', cautious: '#ddaa44', minimal: '#cc4444' }
</script>

<template>
  <div class="governance-bar glass">
    <div class="bar-left">
      <span class="bar-label">治理模式</span>
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
.governance-bar { display: flex; justify-content: space-between; align-items: center; padding: 12px 20px; }
.bar-left { display: flex; align-items: center; gap: 12px; }
.bar-label { font-size: 13px; color: var(--eva-text-dim); }
.mode-selector { display: flex; gap: 4px; }
.mode-btn { padding: 4px 12px; border-radius: 6px; border: 1px solid hsla(200, 20%, 30%, 0.3); background: transparent; color: var(--eva-text-dim); font-size: 12px; cursor: pointer; transition: all 0.2s; }
.mode-btn:hover { background: hsla(200, 20%, 20%, 0.3); }
.mode-btn.active { background: hsla(200, 20%, 15%, 0.4); }
.bar-right { display: flex; gap: 16px; }
.stat { font-size: 11px; color: var(--eva-text-dim); }
</style>
