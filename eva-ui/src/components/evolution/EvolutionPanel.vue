<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  status: any
}>()

const stageLabel: Record<string, string> = {
  idle: '空闲',
  implementing: '实现中',
  testing: '测试中',
  reviewing: '审核中',
  deploying: '部署中',
  success: '成功',
  failed: '失败',
  rolled_back: '已回滚',
}

const stageProgress: Record<string, number> = {
  idle: 0,
  implementing: 25,
  testing: 50,
  reviewing: 75,
  deploying: 90,
  success: 100,
  failed: 100,
}

const currentStage = computed(() => props.status?.current?.status || 'idle')
const progress = computed(() => stageProgress[currentStage.value] || 0)
const isActive = computed(() => props.status?.running)
</script>

<template>
  <div class="evo-panel glass">
    <div class="panel-header">
      <h3>当前进化</h3>
      <span class="stage-badge" :class="currentStage">
        {{ stageLabel[currentStage] || currentStage }}
      </span>
    </div>

    <div v-if="isActive && status.current" class="active-evolution">
      <p class="evo-title">{{ status.current.title }}</p>
      <div class="progress-track">
        <div class="progress-fill" :style="{ width: `${progress}%` }" />
        <div class="progress-stages">
          <span v-for="s in ['implementing', 'testing', 'reviewing', 'deploying']" :key="s"
                class="stage-dot" :class="{ done: progress >= (stageProgress[s] || 0), active: currentStage === s }" />
        </div>
      </div>
      <div class="evo-meta">
        <span v-if="status.current.risk">风险: {{ status.current.risk }}</span>
        <span v-if="status.current.files">文件: {{ status.current.files?.length || 0 }}</span>
      </div>
    </div>

    <div v-else class="idle-state">
      <p class="idle-text">无进行中的进化</p>
      <div class="idle-stats">
        <span>本小时: {{ status?.evolutions_this_hour || 0 }}/3</span>
        <span>连续失败: {{ status?.consecutive_failures || 0 }}</span>
        <span v-if="status?.cooldown_remaining > 0">冷却: {{ status.cooldown_remaining }}s</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.evo-panel { padding: 20px; }
.panel-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.panel-header h3 { font-size: 16px; font-weight: 500; color: var(--eva-text); }
.stage-badge { font-size: 12px; padding: 3px 10px; border-radius: 10px; }
.stage-badge.idle { background: hsla(200, 20%, 20%, 0.3); color: var(--eva-text-dim); }
.stage-badge.implementing, .stage-badge.testing, .stage-badge.reviewing, .stage-badge.deploying {
  background: hsla(200, 50%, 25%, 0.4); color: var(--eva-ice); animation: pulse 2s infinite; }
.stage-badge.success { background: hsla(140, 40%, 20%, 0.4); color: hsl(140, 50%, 60%); }
.stage-badge.failed { background: hsla(0, 40%, 20%, 0.4); color: hsl(0, 50%, 60%); }
@keyframes pulse { 0%, 100% { opacity: 0.7; } 50% { opacity: 1; } }

.evo-title { font-size: 15px; color: var(--eva-text); margin-bottom: 12px; }
.progress-track { position: relative; height: 4px; background: hsla(200, 20%, 20%, 0.3); border-radius: 2px; margin-bottom: 12px; }
.progress-fill { height: 100%; background: var(--eva-ice); border-radius: 2px; transition: width 0.8s ease; }
.progress-stages { position: absolute; top: -4px; left: 0; right: 0; display: flex; justify-content: space-between; padding: 0 5%; }
.stage-dot { width: 10px; height: 10px; border-radius: 50%; background: hsla(200, 20%, 20%, 0.4); border: 2px solid var(--eva-dark); transition: all 0.3s; }
.stage-dot.done { background: var(--eva-ice); }
.stage-dot.active { background: var(--eva-ice); box-shadow: 0 0 8px var(--eva-ice); }
.evo-meta { display: flex; gap: 16px; font-size: 12px; color: var(--eva-text-dim); }

.idle-state { text-align: center; padding: 20px 0; }
.idle-text { color: var(--eva-text-dim); margin-bottom: 8px; }
.idle-stats { display: flex; gap: 16px; justify-content: center; font-size: 12px; color: var(--eva-text-dim); }
</style>
