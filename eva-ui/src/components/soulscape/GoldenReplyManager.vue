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

/* Scene type SVG icons (no emojis) */
const sceneIcons: Record<string, string> = {
  greeting: `<path d="M7 11v-1a5 5 0 0 1 10 0v1" stroke="currentColor" stroke-width="1.5" fill="none"/><circle cx="12" cy="6" r="3" stroke="currentColor" stroke-width="1.5" fill="none"/>`,
  technical: `<rect x="3" y="4" width="18" height="14" rx="2" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M8 10l3 3 5-5" stroke="currentColor" stroke-width="1.5" fill="none"/>`,
  emotional: `<path d="M12 21c-4-3-8-6.5-8-10.5a5 5 0 0 1 8-4 5 5 0 0 1 8 4c0 4-4 7.5-8 10.5z" stroke="currentColor" stroke-width="1.5" fill="none"/>`,
  disagreement: `<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M12 8v4m0 4h.01" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/>`,
  casual: `<path d="M18 8h1a4 4 0 0 1 0 8h-1M2 8h16v9a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V8zm4-5v3m4-3v3m4-3v3" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/>`,
  task_report: `<rect x="5" y="2" width="14" height="20" rx="2" stroke="currentColor" stroke-width="1.5" fill="none"/><line x1="9" y1="7" x2="15" y2="7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><line x1="9" y1="11" x2="15" y2="11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><line x1="9" y1="15" x2="13" y2="15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>`,
}

const defaultIcon = `<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.5" fill="none"/><path d="M8 12h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>`
</script>

<template>
  <div class="golden-manager">
    <div v-for="reply in replies" :key="reply.id" class="golden-card glass">
      <div class="card-top">
        <div class="scene-badge">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" v-html="sceneIcons[reply.scene] || defaultIcon" />
          <span>{{ reply.scene }}</span>
        </div>
        <span class="score-badge">{{ (reply.score * 100).toFixed(0) }}%</span>
      </div>

      <div class="card-exchange">
        <div class="exchange-row user-row">
          <span class="role-tag">User</span>
          <span class="exchange-text">{{ reply.user }}</span>
        </div>
        <div class="exchange-row eva-row">
          <span class="role-tag eva">Eva</span>
          <span class="exchange-text">{{ reply.eva }}</span>
        </div>
      </div>

      <div class="card-bottom">
        <span class="card-meta">{{ reply.added }} / {{ reply.source }}</span>
        <button class="remove-btn" @click="emit('delete', reply.id)">Remove</button>
      </div>
    </div>

    <div v-if="replies.length === 0" class="empty-state">
      No golden replies saved yet
    </div>
  </div>
</template>

<style scoped>
.golden-manager {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.golden-card {
  padding: var(--space-md);
}

.card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-md);
}

.scene-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  padding: 3px 10px;
  border-radius: 100px;
  background: rgba(var(--violet-rgb), 0.08);
  border: 1px solid rgba(var(--violet-rgb), 0.12);
  color: var(--violet);
}

.score-badge {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--success);
  font-weight: 500;
}

.card-exchange {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.exchange-row {
  display: flex;
  gap: 8px;
  font-size: 13px;
  line-height: 1.5;
}

.role-tag {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: 4px;
  font-family: var(--font-heading);
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text-dim);
  align-self: flex-start;
  margin-top: 2px;
}

.role-tag.eva {
  background: rgba(var(--accent-rgb), 0.08);
  color: var(--accent);
}

.user-row .exchange-text { color: var(--text-secondary); }
.eva-row .exchange-text { color: var(--text); }

.card-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: var(--space-md);
  padding-top: var(--space-sm);
  border-top: 1px solid var(--border);
}

.card-meta {
  font-size: 10px;
  color: var(--text-dim);
}

.remove-btn {
  font-size: 11px;
  color: var(--text-dim);
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
  transition: all var(--transition-fast);
}

.remove-btn:hover {
  color: var(--error);
  background: rgba(248, 113, 113, 0.08);
}

.empty-state {
  text-align: center;
  color: var(--text-dim);
  padding: var(--space-xl);
  font-size: 13px;
}
</style>
