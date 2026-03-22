<script setup lang="ts">
const props = defineProps<{
  replies: Array<{
    id: string
    scene: string
    user: string
    eva: string
    score: number
    added: string
    source: string
  }>
}>()

const emit = defineEmits<{
  (e: 'delete', id: string): void
}>()

const sceneIcons: Record<string, string> = {
  greeting: '\uD83D\uDC4B',
  technical: '\uD83D\uDCBB',
  emotional: '\uD83D\uDC97',
  disagreement: '\uD83E\uDD14',
  casual: '\u2615',
  task_report: '\uD83D\uDCCB',
}
</script>

<template>
  <div class="golden-manager">
    <div v-for="reply in replies" :key="reply.id" class="golden-card glass">
      <div class="card-header">
        <span class="scene-badge">{{ sceneIcons[reply.scene] || '\uD83D\uDCAC' }} {{ reply.scene }}</span>
        <span class="score-badge">{{ (reply.score * 100).toFixed(0) }}%</span>
      </div>
      <div class="card-exchange">
        <div class="exchange-user">
          <span class="role-label">User:</span>
          {{ reply.user }}
        </div>
        <div class="exchange-eva">
          <span class="role-label">Eva:</span>
          {{ reply.eva }}
        </div>
      </div>
      <div class="card-footer">
        <span class="card-date">{{ reply.added }} · {{ reply.source }}</span>
        <button class="delete-btn" @click="emit('delete', reply.id)">删除</button>
      </div>
    </div>
    <div v-if="replies.length === 0" class="empty">暂无 golden replies</div>
  </div>
</template>

<style scoped>
.golden-manager {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.golden-card {
  padding: 12px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.scene-badge {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  background: hsla(270, 30%, 25%, 0.3);
  color: var(--eva-purple);
}

.score-badge {
  font-size: 12px;
  color: hsl(140, 50%, 55%);
  font-weight: 600;
}

.card-exchange {
  font-size: 13px;
  line-height: 1.5;
}

.exchange-user {
  color: var(--eva-text-dim);
  margin-bottom: 4px;
}

.exchange-eva {
  color: var(--eva-text);
}

.role-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--eva-ice);
  margin-right: 4px;
}

.card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}

.card-date {
  font-size: 10px;
  color: var(--eva-text-dim);
  opacity: 0.5;
}

.delete-btn {
  font-size: 11px;
  color: hsl(0, 50%, 55%);
  background: none;
  border: none;
  cursor: pointer;
  opacity: 0.5;
  transition: opacity 0.2s;
}

.delete-btn:hover {
  opacity: 1;
}

.empty {
  text-align: center;
  color: var(--eva-text-dim);
  padding: 20px;
  font-size: 13px;
}
</style>
