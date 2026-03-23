<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  status: any
}>()

const stageLabel: Record<string, string> = {
  idle: 'Idle', implementing: 'Implementing', testing: 'Testing',
  reviewing: 'Reviewing', deploying: 'Deploying',
  success: 'Success', failed: 'Failed', rolled_back: 'Rolled Back',
}

const stageProgress: Record<string, number> = {
  idle: 0, implementing: 25, testing: 50, reviewing: 75, deploying: 90, success: 100, failed: 100,
}

const currentStage = computed(() => props.status?.current?.status || 'idle')
const progress = computed(() => stageProgress[currentStage.value] || 0)
const isActive = computed(() => props.status?.running)
</script>

<template>
  <div class="evo-panel glass">
    <div class="panel-header">
      <h3 class="card-title">Current Evolution</h3>
      <span class="stage-badge" :class="currentStage">
        {{ stageLabel[currentStage] || currentStage }}
      </span>
    </div>

    <div v-if="isActive && status.current" class="active-evo">
      <p class="evo-title">{{ status.current.title }}</p>
      <div class="progress-track">
        <div class="progress-fill" :style="{ width: `${progress}%` }" />
        <div class="progress-stages">
          <span v-for="s in ['implementing', 'testing', 'reviewing', 'deploying']" :key="s"
                class="stage-dot" :class="{ done: progress >= (stageProgress[s] || 0), current: currentStage === s }" />
        </div>
      </div>
      <div class="evo-meta">
        <span v-if="status.current.risk">Risk: {{ status.current.risk }}</span>
        <span v-if="status.current.files">Files: {{ status.current.files?.length || 0 }}</span>
      </div>
    </div>

    <div v-else class="idle-state">
      <p class="idle-text">No evolution in progress</p>
      <div class="idle-stats">
        <span>This hour: {{ status?.evolutions_this_hour || 0 }}/3</span>
        <span>Consecutive fails: {{ status?.consecutive_failures || 0 }}</span>
        <span v-if="status?.cooldown_remaining > 0">Cooldown: {{ status.cooldown_remaining }}s</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.evo-panel { padding: var(--space-lg); }
.panel-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-md); }
.stage-badge { font-family: var(--font-heading); font-size: 11px; padding: 3px 12px; border-radius: 100px; letter-spacing: 0.5px; }
.stage-badge.idle { background: rgba(255,255,255,0.04); color: var(--text-dim); }
.stage-badge.implementing, .stage-badge.testing, .stage-badge.reviewing, .stage-badge.deploying {
  background: rgba(var(--accent-rgb), 0.1); color: var(--accent); animation: pulse 2s infinite; }
.stage-badge.success { background: rgba(52,211,153,0.1); color: var(--success); }
.stage-badge.failed { background: rgba(248,113,113,0.1); color: var(--error); }
@keyframes pulse { 0%, 100% { opacity: 0.7; } 50% { opacity: 1; } }

.evo-title { font-size: 14px; color: var(--text); margin-bottom: var(--space-md); }
.progress-track { position: relative; height: 4px; background: rgba(255,255,255,0.04); border-radius: 2px; margin-bottom: var(--space-md); }
.progress-fill { height: 100%; background: var(--accent); border-radius: 2px; transition: width 0.8s ease; }
.progress-stages { position: absolute; top: -4px; left: 0; right: 0; display: flex; justify-content: space-between; padding: 0 5%; }
.stage-dot { width: 10px; height: 10px; border-radius: 50%; background: rgba(255,255,255,0.06); border: 2px solid var(--bg); transition: all 0.3s; }
.stage-dot.done { background: var(--accent); }
.stage-dot.current { background: var(--accent); box-shadow: 0 0 8px rgba(var(--accent-rgb), 0.5); }
.evo-meta { display: flex; gap: 16px; font-size: 12px; color: var(--text-dim); }

.idle-state { text-align: center; padding: var(--space-lg) 0; }
.idle-text { color: var(--text-dim); margin-bottom: var(--space-sm); }
.idle-stats { display: flex; gap: 16px; justify-content: center; font-size: 12px; color: var(--text-dim); }
</style>
